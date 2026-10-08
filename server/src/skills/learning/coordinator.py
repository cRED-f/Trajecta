"""Background coordinator for Trajecta's Verified Skill Learning loop.

Successful trajectories only wake this worker. The expensive work — mining,
synthesis, replay evaluation, promotion — never runs synchronously inside the
user's chat SSE request.

    successful trajectory -> threshold -> SkillMiner -> candidate
        -> evaluation -> verified -> promotion

State lives in SQLite so restarting Trajecta does not reset the mining
threshold.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from server.src.config import Settings
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.trajectory_store.store import TrajectoryStore

if TYPE_CHECKING:
    from server.src.skills.evaluation.evaluator import SkillEvaluator
    from server.src.skills.evaluation.live_stream import BackgroundEvaluationStreams
    from server.src.skills.experiments import SkillExperimentService
    from server.src.skills.promotion.promoter import SkillPromoter
    from server.src.skills.repository import SkillRepository
    from server.src.skills.skill_miner.miner import SkillMiner


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SkillLearningCoordinator:
    """Coordinate Trajecta's verified-skill learning lifecycle.

    Flow:

        successful trajectory -> notify_success() -> threshold reached?
            -> SkillMiner.mine() -> candidate -> SkillEvaluator.evaluate()
            -> verdict == "pass" -> SkillPromoter.promote()

    Important:

    A failed mining pass does NOT advance the durable checkpoint.

    Candidate-specific evaluation/promotion errors DO advance the mining
    checkpoint because the candidate already exists and can be retried
    separately.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        db: SQLiteDatabase,
        trajectories: TrajectoryStore,
        miner: SkillMiner,
        evaluator: SkillEvaluator,
        promoter: SkillPromoter,
        repository: SkillRepository | None = None,
        experiments: SkillExperimentService | None = None,
        evaluation_streams: BackgroundEvaluationStreams | None = None,
    ) -> None:
        self._settings = settings
        self._config = settings.skills.learning

        self._db = db
        self._trajectories = trajectories

        self._miner = miner
        self._evaluator = evaluator
        self._promoter = promoter
        # Optional: with no repository the loop cannot tell a brand-new skill
        # from an upgrade, so it falls back to promoting every pass.
        self._repository = repository
        self._experiments = experiments
        self._evaluation_streams = evaluation_streams

        self._wake = asyncio.Event()
        self._interactive_idle = asyncio.Event()
        self._interactive_idle.set()
        self._interactive_count = 0

        # Guarantees only one learning pass runs at a time.
        self._run_lock = asyncio.Lock()

        self._worker_task: asyncio.Task[None] | None = None

        self._force_requested = False
        self._stopping = False

        self._last_notified_trajectory_id: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background learning worker."""

        if self._worker_task is not None and not self._worker_task.done():
            return

        self._stopping = False
        self._worker_task = asyncio.create_task(
            self._worker(),
            name="trajecta-skill-learning",
        )

        # Check old successful trajectories when Trajecta starts: 10 existing
        # successes survive a restart, so mining still runs.
        if self._config.enabled:
            self._wake.set()

    async def stop(self) -> None:
        self._stopping = True

        task = self._worker_task
        self._worker_task = None

        if task is None:
            return

        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Triggers
    # ------------------------------------------------------------------

    def notify_success(self, trajectory_id: str) -> None:
        """Wake the learning worker after a successful trajectory.

        This method deliberately performs no DB work and no model call, so chat
        completion remains fast.
        """

        if not self._config.enabled or self._stopping:
            return

        self._last_notified_trajectory_id = trajectory_id
        self._wake.set()

    def begin_interactive(self) -> None:
        """Chat always has priority over automatic skill mining."""
        self._interactive_count += 1
        self._interactive_idle.clear()

    def end_interactive(self) -> None:
        self._interactive_count = max(0, self._interactive_count - 1)
        if self._interactive_count == 0:
            self._interactive_idle.set()

    def request_run(self, *, force: bool = True) -> bool:
        """Manually request a learning pass.

        force=True ignores ``trigger_every_successes``. It does NOT ignore
        ``minimum_occurrences`` in SkillMiner.
        """

        if self._stopping or self._run_lock.locked():
            return False

        if force:
            self._force_requested = True

        self._wake.set()
        return True

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        total_successes = await self._trajectories.count_successful()
        state = await self._get_state()
        checkpoint = int(state.get("success_count_checkpoint") or 0)

        recent_runs = await self._db.fetch(
            """
            SELECT *
            FROM skill_learning_runs
            ORDER BY started_at DESC
            LIMIT 20
            """
        )
        for run in recent_runs:
            run["metadata"] = self._loads_json(run.get("metadata"), {})

        return {
            "enabled": self._config.enabled,
            "worker_running": bool(
                self._worker_task is not None and not self._worker_task.done()
            ),
            "interactive_runs": self._interactive_count,
            "learning_run_active": self._run_lock.locked(),
            "total_successful_trajectories": total_successes,
            "success_count_checkpoint": checkpoint,
            "pending_successes": max(0, total_successes - checkpoint),
            "trigger_every_successes": self._config.trigger_every_successes,
            "minimum_occurrences": self._config.minimum_occurrences,
            "auto_evaluate": self._config.auto_evaluate,
            "auto_promote_initial": self._config.auto_promote_initial,
            "auto_experiment_upgrades": self._config.auto_experiment_upgrades,
            "last_notified_trajectory_id": self._last_notified_trajectory_id,
            "state": state,
            "recent_runs": recent_runs,
        }

    # ------------------------------------------------------------------
    # Public deterministic execution
    # ------------------------------------------------------------------

    async def run_once(self, *, force: bool = False) -> dict[str, Any]:
        """Run one learning pass.

        Normal chat should use :meth:`notify_success`, not this method. This is
        for API administrative runs, unit tests and CLI tooling.
        """

        async with self._run_lock:
            return await self._run_once_locked(force=force)

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    async def _worker(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()

            force = self._force_requested
            self._force_requested = False

            try:
                # Queue work rather than running LLM synthesis alongside chat.
                await self._interactive_idle.wait()
                await self.run_once(force=force)
            except asyncio.CancelledError:
                raise
            except Exception:
                # run_once persists the error; do not kill the worker
                # permanently.
                continue

    # ------------------------------------------------------------------
    # Learning run
    # ------------------------------------------------------------------

    async def _run_once_locked(self, *, force: bool) -> dict[str, Any]:
        state = await self._get_state()
        checkpoint_before = int(state.get("success_count_checkpoint") or 0)

        observed_success_count = await self._trajectories.count_successful()
        pending = max(0, observed_success_count - checkpoint_before)

        # -- Threshold -------------------------------------------------
        if not force:
            if not self._config.enabled:
                return {
                    "status": "skipped",
                    "reason": "automatic skill learning is disabled",
                    "pending_successes": pending,
                }

            if pending < self._config.trigger_every_successes:
                return {
                    "status": "skipped",
                    "reason": "success threshold not reached",
                    "pending_successes": pending,
                    "required": self._config.trigger_every_successes,
                }

        # -- Create run ------------------------------------------------
        run_id = uuid.uuid4().hex
        started_at = _now()

        await self._db.execute(
            """
            INSERT INTO skill_learning_runs(
                id,
                started_at,
                status,
                checkpoint_before,
                observed_success_count,
                metadata
            )
            VALUES (?, ?, 'running', ?, ?, ?)
            """,
            (
                run_id,
                started_at,
                checkpoint_before,
                observed_success_count,
                json.dumps(
                    {
                        "force": force,
                        "last_notified_trajectory_id": (
                            self._last_notified_trajectory_id
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

        await self._db.execute(
            """
            UPDATE skill_learning_state
            SET
                last_run_id = ?,
                last_started_at = ?,
                last_status = 'running',
                last_error = NULL,
                updated_at = ?
            WHERE id = 1
            """,
            (run_id, started_at, started_at),
        )

        # -- Mine ------------------------------------------------------
        try:
            created = await self._miner.mine(
                limit=self._config.scan_limit,
                minimum_occurrences=self._config.minimum_occurrences,
                sequence_similarity=self._config.sequence_similarity,
                goal_similarity=self._config.goal_similarity,
                model_name=self._config.model_name,
                max_candidates=self._config.max_candidates_per_run,
            )
        except asyncio.CancelledError:
            await self._finish_failed_run(
                run_id=run_id,
                status="cancelled",
                error="skill learning worker was cancelled",
            )
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            await self._finish_failed_run(run_id=run_id, status="error", error=error)
            # IMPORTANT: checkpoint is not advanced, so the batch can be
            # retried instead of silently lost.
            raise

        # -- Evaluate + promote ----------------------------------------
        candidate_ids: list[str] = []
        evaluated_count = 0
        verified_count = 0
        promoted_count = 0
        experiment_count = 0
        errors: list[dict[str, str]] = []

        try:
            for candidate in created:
                candidate_id = str(candidate.get("id") or "").strip()

                if not candidate_id:
                    errors.append(
                        {
                            "stage": "mine",
                            "candidate_id": "",
                            "error": "SkillMiner returned a candidate without an id",
                        }
                    )
                    continue

                candidate_ids.append(candidate_id)

                # Evaluation
                if not self._config.auto_evaluate:
                    continue

                stream = self._evaluation_streams
                if stream is not None:
                    stream.begin(candidate_id)

                async def publish(event: dict[str, Any]) -> None:
                    if stream is not None:
                        await stream.publish(candidate_id, event)

                try:
                    report = await self._evaluator.evaluate(
                        candidate_id,
                        model_name=self._config.model_name,
                        on_event=publish,
                    )
                    evaluated_count += 1
                    await publish({
                        "type": "completed",
                        "report": report.model_dump(mode="json"),
                    })
                except asyncio.CancelledError:
                    await publish({"type": "error", "message": "Evaluation cancelled"})
                    raise
                except Exception as exc:
                    await publish({
                        "type": "error",
                        "message": f"{type(exc).__name__}: {exc}",
                    })
                    errors.append(
                        {
                            "stage": "evaluate",
                            "candidate_id": candidate_id,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    continue

                # Promotion is impossible without a pass.
                if report.verdict != "pass":
                    continue

                verified_count += 1

                # An upgrade must not silently override the user's own
                # disable decision; hold the candidate instead.
                skill_name = await self._candidate_skill_name(candidate_id)
                active = (
                    await self._repository.get_active(skill_name)
                    if self._repository is not None and skill_name
                    else None
                )

                if active is not None and active.get("status") == "disabled":
                    errors.append(
                        {
                            "stage": "promote",
                            "candidate_id": candidate_id,
                            "error": "held_verified_user_disabled",
                        }
                    )
                    continue

                # Existing skill: stage the next version and let a live
                # experiment decide, rather than swapping it in for everyone.
                if (
                    active is not None
                    and self._experiments is not None
                    and self._config.auto_experiment_upgrades
                ):
                    try:
                        await self._experiments.start_candidate(
                            candidate_id=candidate_id,
                        )
                        experiment_count += 1
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        errors.append(
                            {
                                "stage": "experiment",
                                "candidate_id": candidate_id,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                    continue

                # First version of a skill.
                if not self._config.auto_promote_initial:
                    continue

                try:
                    await self._promoter.promote(
                        candidate_id=candidate_id,
                        evaluation_id=report.id,
                    )
                    promoted_count += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Candidate remains VERIFIED and can be promoted later.
                    errors.append(
                        {
                            "stage": "promote",
                            "candidate_id": candidate_id,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
        except asyncio.CancelledError:
            await self._finish_failed_run(
                run_id=run_id,
                status="cancelled",
                error="skill learning worker was cancelled",
            )
            raise

        # -- Finish learning run ---------------------------------------
        completed_at = _now()
        status = "partial" if errors else "complete"
        error_text = (
            json.dumps(errors, ensure_ascii=False) if errors else None
        )
        metadata = {
            "candidate_ids": candidate_ids,
            "errors": errors,
            "force": force,
        }

        await self._db.execute(
            """
            UPDATE skill_learning_runs
            SET
                completed_at = ?,
                status = ?,
                created_count = ?,
                evaluated_count = ?,
                verified_count = ?,
                promoted_count = ?,
                experiment_count = ?,
                error = ?,
                metadata = ?
            WHERE id = ?
            """,
            (
                completed_at,
                status,
                len(candidate_ids),
                evaluated_count,
                verified_count,
                promoted_count,
                experiment_count,
                error_text,
                json.dumps(metadata, ensure_ascii=False),
                run_id,
            ),
        )

        # Mining itself completed successfully. Even if evaluation/promotion
        # had a candidate-specific failure, those candidates already exist and
        # should be retried directly — do not re-synthesize the same batch.
        await self._db.execute(
            """
            UPDATE skill_learning_state
            SET
                success_count_checkpoint = ?,
                last_run_id = ?,
                last_completed_at = ?,
                last_status = ?,
                last_error = ?,
                last_created_count = ?,
                last_evaluated_count = ?,
                last_verified_count = ?,
                last_promoted_count = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (
                observed_success_count,
                run_id,
                completed_at,
                status,
                error_text,
                len(candidate_ids),
                evaluated_count,
                verified_count,
                promoted_count,
                completed_at,
            ),
        )

        return {
            "id": run_id,
            "status": status,
            "checkpoint_before": checkpoint_before,
            "observed_success_count": observed_success_count,
            "pending_successes": pending,
            "created_count": len(candidate_ids),
            "evaluated_count": evaluated_count,
            "verified_count": verified_count,
            "promoted_count": promoted_count,
            "experiment_count": experiment_count,
            "candidate_ids": candidate_ids,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    async def _candidate_skill_name(self, candidate_id: str) -> str | None:
        """Skill a candidate would extend, or None when it is brand new."""

        if self._repository is None:
            return None

        skill = await self._repository.get_candidate_skill(candidate_id)

        return skill.name if skill is not None else None

    async def _get_state(self) -> dict[str, Any]:
        row = await self._db.fetchone(
            """
            SELECT *
            FROM skill_learning_state
            WHERE id = 1
            """
        )
        if row is not None:
            return row

        now = _now()
        await self._db.execute(
            """
            INSERT OR IGNORE INTO skill_learning_state(
                id,
                success_count_checkpoint,
                last_status,
                updated_at
            )
            VALUES (1, 0, 'never', ?)
            """,
            (now,),
        )

        return (
            await self._db.fetchone(
                """
                SELECT *
                FROM skill_learning_state
                WHERE id = 1
                """
            )
            or {}
        )

    async def _finish_failed_run(
        self,
        *,
        run_id: str,
        status: str,
        error: str,
    ) -> None:
        completed_at = _now()

        await self._db.execute(
            """
            UPDATE skill_learning_runs
            SET completed_at = ?, status = ?, error = ?
            WHERE id = ?
            """,
            (completed_at, status, error, run_id),
        )

        await self._db.execute(
            """
            UPDATE skill_learning_state
            SET
                last_run_id = ?,
                last_completed_at = ?,
                last_status = ?,
                last_error = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (run_id, completed_at, status, error, completed_at),
        )

    @staticmethod
    def _loads_json(value: Any, default: Any) -> Any:
        if not isinstance(value, str) or not value:
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
