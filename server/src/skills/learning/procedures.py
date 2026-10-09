"""Evidence-based, review-first procedural refinement.

Drafts are NOT executable skills. A confirmed outcome does not verify a workflow,
and reviewed changes must still pass the existing candidate/evaluation/versioning
path before they can affect /skills/. Tool arguments are never persisted here.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from server.src.memory.episodic.store import EpisodicMemory
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.repository import SkillRepository
from server.src.skills.trajectory_store.store import TrajectoryStore

_TOOL = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
_WORD = re.compile(r"[a-z][a-z0-9]{3,}")
_STOP = {"with", "from", "that", "this", "then", "when", "using", "have", "your", "into", "task", "skill", "agent", "tool", "tools", "review", "again", "please"}
_READ_ONLY = {"read_file", "search", "search_files", "list_files", "search_attachments", "list_directory", "web_search"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe(value: Any, limit: int = 300) -> str:
    return EpisodicMemory._text(value, limit)


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold())) - _STOP


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    value = dict(row)
    for key in ("steps_json", "evidence_json"):
        value[{"steps_json": "steps", "evidence_json": "evidence"}[key]] = json.loads(value.pop(key) or ("[]" if key == "steps_json" else "{}"))
    value["user_confirmed"] = bool(value["user_confirmed"])
    return value


class ProcedureRefinementService:
    def __init__(self, db: SQLiteDatabase, trajectories: TrajectoryStore,
                 repository: SkillRepository) -> None:
        self._db = db
        self._trajectories = trajectories
        self._repository = repository

    @staticmethod
    def _steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Reconstruct observable tool order, never infer missing operations.

        Streaming chunks with no name are continuations, not new calls.
        Match results by call ID first, with best-effort name fallback.
        """
        steps: list[dict[str, Any]] = []
        for event in events[-500:]:
            if not isinstance(event, dict):
                continue
            kind, data, seq = event.get("type"), event.get("data"), event.get("seq")
            if not isinstance(data, dict) or not isinstance(seq, int):
                continue
            name = data.get("name")
            if not isinstance(name, str) or not _TOOL.fullmatch(name):
                continue
            if kind == "tool.call.delta":
                # No args, document bodies, file paths, or private model reasoning.
                steps.append({"tool": name, "event_seq": seq, "result_seq": None,
                              "outcome": "unobserved", "call_id": str(data.get("id") or "")[:100]})
            elif kind == "tool.result":
                call_id = str(data.get("tool_call_id") or "")[:100]
                target = next((step for step in reversed(steps)
                               if step["result_seq"] is None and
                               ((call_id and step["call_id"] == call_id) or
                                (not call_id and step["tool"] == name))), None)
                if target is None:
                    target = {"tool": name, "event_seq": seq, "result_seq": None,
                              "outcome": "unobserved", "call_id": ""}
                    steps.append(target)
                target["result_seq"] = seq
                target["outcome"] = ("error" if data.get("status") == "error" else
                                     "observed_result")
        for step in steps:
            step.pop("call_id", None)
        return steps[:16]

    async def _match(self, goal: str, scope: str, user_id: str) -> tuple[str | None, str | None]:
        # Global executable skills must never be auto-targeted from another user
        # or workspace. Scoped drafts stay unlinked until explicitly reviewed.
        if scope != "local" or user_id != "local":
            return None, None
        terms = _words(goal)
        matches: list[tuple[float, dict[str, Any]]] = []
        if len(terms) < 2:
            return None, None
        for skill in await self._repository.list_active():
            # Registry records have no description column: obtain it from the
            # immutable active version rather than guessing from a skill name.
            bundle = await self._repository.get_version_skill(
                str(skill["name"]), str(skill["version"]))
            description = bundle.description if bundle is not None else ""
            text = f"{skill.get('name','').replace('-', ' ')} {description}"
            skill_terms = _words(text)
            overlap = len(terms & skill_terms)
            if overlap >= 2 and skill_terms:
                matches.append((overlap / max(1, min(len(terms), len(skill_terms))), skill))
        matches.sort(key=lambda match: match[0], reverse=True)
        # Require a decisive and reasonably strong match, never select a tie.
        if not matches or matches[0][0] < 0.55 or (
            len(matches) > 1 and matches[1][0] >= matches[0][0] - 0.15
        ):
            return None, None
        chosen = matches[0][1]
        return str(chosen["name"]), str(chosen["version"])

    async def propose(self, trajectory_id: str, *, trigger: str,
                      insights: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        """Build or idempotently update a reviewable draft from source evidence."""
        if trigger not in {"reflection", "feedback"}:
            raise ValueError("invalid proposal trigger")
        trace = await self._trajectories.get_with_task(trajectory_id)
        if trace is None or trace.get("outcome") not in {"completed", "success", "failure"}:
            return None
        meta = trace.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        rating = meta.get("user_feedback")
        if trigger == "feedback" and rating not in {"success", "failure"}:
            return None
        if trigger == "reflection" and not insights:
            return None
        steps = self._steps(trace.get("steps") or [])
        if not steps:
            return None  # Never fabricate a workflow from text alone.
        event_ids = {e.get("seq") for e in (trace.get("steps") or [])
                     if isinstance(e, dict) and isinstance(e.get("seq"), int)}
        lessons = []
        references: set[int] = set()
        for insight in (insights or [])[:3]:
            if not isinstance(insight, dict) or insight.get("kind") not in {"procedure", "lesson", "correction"}:
                continue
            seqs = insight.get("evidence_event_seqs")
            if not isinstance(seqs, list) or not seqs or not all(
                isinstance(seq, int) and seq in event_ids for seq in seqs
            ):
                continue
            text = _safe(insight.get("content"), 450)
            if len(text) < 16:
                continue
            lessons.append(text)
            references.update(seqs)
        if trigger == "reflection" and not lessons:
            return None
        goal = _safe(trace.get("goal"), 280)
        if not goal:
            return None
        task_meta = trace.get("task_metadata") or {}
        if not isinstance(task_meta, dict):
            task_meta = {}
        scope = EpisodicMemory.workspace_scope(task_meta.get("workspace_path"))
        user_id = str(trace.get("user_id") or "local")
        target, base = await self._match(goal, scope, user_id)
        note = _safe(meta.get("user_feedback_note"), 350) if trigger == "feedback" else ""
        rationale = ("\n".join(lessons) if lessons else
                     ("User marked this task successful." if rating == "success" else
                      "User marked this task unsuccessful; observed steps are NOT a recommended workflow."))
        if note:
            rationale += f"\nUser feedback: {note}"
        # Link an earlier reflection to subsequent feedback, but never modify
        # a rejected/approved draft without a fresh explicit review.
        parent = None
        if trigger == "feedback":
            previous = await self._db.fetchone(
                "SELECT id FROM procedure_drafts WHERE source_trajectory_id=? AND trigger='reflection'",
                (trajectory_id,))
            parent = previous["id"] if previous else None
        payload = {"goal": goal, "steps": steps, "rationale": rationale,
                   "rating": rating if trigger == "feedback" else None,
                   "references": sorted(references)}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        evidence = {"source_trajectory_id": trajectory_id,
                    "source_event_seqs": sorted({step["event_seq"] for step in steps} |
                                                {step["result_seq"] for step in steps if step["result_seq"] is not None} |
                                                references),
                    "feedback": rating if trigger == "feedback" else None,
                    "risk_review_required": any(s["tool"] not in _READ_ONLY for s in steps),
                    "raw_arguments_stored": False}
        existing = await self._db.fetchone(
            "SELECT * FROM procedure_drafts WHERE source_trajectory_id=? AND trigger=?",
            (trajectory_id, trigger))
        if existing:
            if existing["fingerprint"] == fingerprint or existing["status"] != "needs_review":
                return _decode(existing)
            await self._db.execute(
                """INSERT INTO procedure_draft_revisions
                   (draft_id, version, snapshot_json, created_at) VALUES (?,?,?,?)""",
                (existing["id"], existing["version"], json.dumps(dict(existing)), _now()))
            await self._db.execute(
                """UPDATE procedure_drafts SET steps_json=?, rationale=?, fingerprint=?,
                   evidence_json=?, version=version+1, updated_at=? WHERE id=?""",
                (json.dumps(steps), rationale, fingerprint, json.dumps(evidence), _now(), existing["id"]))
            return await self.get(str(existing["id"]))
        draft_id = uuid.uuid4().hex
        await self._db.execute(
            """INSERT OR IGNORE INTO procedure_drafts
            (id, source_trajectory_id, trigger, parent_id, user_id, scope,
             kind, target_skill_name, base_skill_version, title, steps_json, rationale,
             fingerprint, evidence_json, status, version, user_confirmed, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'needs_review', 1, ?, ?, ?)""",
            (draft_id, trajectory_id, trigger, parent, user_id, scope,
             "revision" if target else "new", target, base, goal,
             json.dumps(steps), rationale, fingerprint, json.dumps(evidence),
             int(rating == "success" and trigger == "feedback"), _now(), _now()))
        item = await self._db.fetchone(
            "SELECT * FROM procedure_drafts WHERE source_trajectory_id=? AND trigger=?",
            (trajectory_id, trigger))
        return _decode(item) if item else None

    async def get(self, draft_id: str) -> dict[str, Any] | None:
        item = await self._db.fetchone("SELECT * FROM procedure_drafts WHERE id=?", (draft_id,))
        return _decode(item) if item else None

    async def list(self, *, status: str | None = None, limit: int = 100,
                   user_id: str = "local", scope: str = "local") -> list[dict[str, Any]]:
        if status is not None and status not in {"needs_review", "approved", "rejected", "candidate_created"}:
            raise ValueError("invalid procedure status")
        limit = max(1, min(limit, 200))
        if status:
            rows = await self._db.fetch(
                """SELECT * FROM procedure_drafts WHERE user_id=? AND scope=? AND status=?
                   ORDER BY updated_at DESC LIMIT ?""",
                (user_id, scope, status, limit))
        else:
            rows = await self._db.fetch(
                """SELECT * FROM procedure_drafts WHERE user_id=? AND scope=?
                   ORDER BY updated_at DESC LIMIT ?""", (user_id, scope, limit))
        return [_decode(row) for row in rows]

    async def history(self, draft_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT version, snapshot_json, created_at FROM procedure_draft_revisions WHERE draft_id=? ORDER BY version",
            (draft_id,))
        return [{"version": r["version"], "snapshot": _decode(json.loads(r["snapshot_json"])),
                 "created_at": r["created_at"]} for r in rows]

    async def review(self, draft_id: str, *, decision: str) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        cur = await self._db.execute(
            """UPDATE procedure_drafts SET status=?, updated_at=?
               WHERE id=? AND status='needs_review'""",
            ("approved" if decision == "approve" else "rejected", _now(), draft_id))
        if not cur.rowcount:
            raise ValueError("procedure is missing or no longer needs review")
        return (await self.get(draft_id)) or {}

    async def create_candidate(self, draft_id: str, *, name: str | None = None) -> dict[str, Any]:
        """Explicit action only. Produces a candidate, not a version or active skill.

        Revision candidates preserve the original skill content and workflow.
        The proposed amendment is clearly marked and must be manually evaluated.
        """
        from server.src.skills.representation.skill import (
            Skill, SkillMetadata, SkillRisk, SkillWorkflow, WorkflowStep,
        )

        draft = await self.get(draft_id)
        if not draft or draft["status"] != "approved":
            raise ValueError("approve the proposal before creating a candidate")
        if draft["user_id"] != "local" or draft["scope"] != "local":
            raise ValueError("scoped proposals cannot be turned into global skills")
        if not draft["user_confirmed"]:
            raise ValueError("explicit positive feedback is required for a candidate")
        if any(step["outcome"] != "observed_result" for step in draft["steps"]):
            raise ValueError("all tool steps require observed non-error results before candidate creation")
        target = draft["target_skill_name"]
        tools = [step["tool"] for step in draft["steps"]]
        risk = SkillRisk.READ_ONLY if all(t in _READ_ONLY for t in tools) else SkillRisk.SENSITIVE
        if target:
            if name is not None and name != target:
                raise ValueError("cannot rename an existing skill through refinement")
            current = await self._repository.get_active(target)
            if not current or current.get("status") != "active" or str(current["version"]) != draft["base_skill_version"]:
                raise ValueError("the target skill has changed; review against the current version")
            original = await self._repository.get_version_skill(target, draft["base_skill_version"])
            if original is None:
                raise ValueError("active skill bundle could not be loaded")
            skill = original.model_copy(deep=True)
            observed = "\n".join(
                f"{i}. {step['tool']} — {step['outcome']} (event {step['event_seq']})"
                for i, step in enumerate(draft["steps"], 1)
            )
            skill.instructions += ("\n\n## Proposed change (requires evaluation)\n"
                                   + draft["rationale"] + "\n\nObserved steps:\n" + observed)
            skill.metadata.source_trajectory_ids = list(dict.fromkeys(
                [*skill.metadata.source_trajectory_ids, draft["source_trajectory_id"]]))
            skill.metadata.risk = risk if risk != SkillRisk.READ_ONLY else skill.metadata.risk
        else:
            if name is None:
                raise ValueError("new skills require an explicit name")
            workflow = SkillWorkflow(
                trigger=draft["title"],
                steps=[WorkflowStep(instruction=f"Use {step['tool']} after checking current permissions and inputs.",
                                    tool_names=[step["tool"]],
                                    success_signal="Verify the tool result independently")
                       for step in draft["steps"]],
                success_criteria=["Verify the requested outcome independently"],
            )
            skill = Skill(name=name, description=draft["title"][:250],
                          instructions=("Observed tool sequence (not a verified solution):\n" +
                                        "\n".join(f"- {s['tool']}" for s in draft["steps"]) +
                                        "\n\nReview notes:\n" + draft["rationale"]),
                          workflow=workflow,
                          metadata=SkillMetadata(source_trajectory_ids=[draft["source_trajectory_id"]], risk=risk))
        skill.metadata.notes = {**skill.metadata.notes, "procedure_draft_id": draft_id,
                                "requires_manual_evaluation": True}
        candidate = await self._repository.create_candidate(skill)
        await self._db.execute(
            "UPDATE procedure_drafts SET status='candidate_created', candidate_id=?, updated_at=? WHERE id=? AND status='approved'",
            (candidate["id"], _now(), draft_id))
        return candidate
