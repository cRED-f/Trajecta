"""Isolated baseline-vs-skill replay for Trajecta."""

from __future__ import annotations

import json
import tempfile
import time
import uuid

from collections.abc import (
    Mapping,
)

from pathlib import Path

from typing import Any

from deepagents import (
    FilesystemPermission,
    create_deep_agent,
)

from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    StoreBackend,
)

from langchain.agents.middleware import (
    TodoListMiddleware,
)

from langchain_core.messages import (
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from langgraph.checkpoint.memory import (
    MemorySaver,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from server.src.chat.model import (
    BifrostModelFactory,
)

from server.src.config import Settings

from server.src.memory.provider import (
    MemoryProvider,
)

from server.src.skills.evaluation.fixtures import ReplayFixtureStore
from server.src.skills.evaluation.verifier import (
    OutcomeVerificationResult,
    OutcomeVerifier,
)
from server.src.skills.representation.skill import (
    EvaluationMode,
    Skill,
    SkillEvalCase,
)

from server.src.tools.personal import (
    PersonalToolProvider,
)

from server.src.tools.sandbox import (
    DockerSandboxBackend,
)


# ---------------------------------------------------------------------------
# Tool safety
# ---------------------------------------------------------------------------

_BUILTIN_TOOL_NAMES = {
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "delete",
    "glob",
    "grep",
    "execute",
    "task",
    "write_todos",
}


# These tools must never be replayed automatically against real user state.
_NEVER_AUTO_REPLAY = {
    "ask_user",
    "request_approval",

    "memory_save",
    "memory_forget",

    "task_create",
    "task_update",
    "task_complete",
    "task_delete",

    "schedule_create",
    "schedule_update",
    "schedule_delete",

    "notify_user",

    "browser_click",
    "browser_type",
    "browser_press",
    "browser_download",
    "browser_upload",
    "browser_new_tab",
    "browser_close_tab",

    "computer_click",
    "computer_move",
    "computer_drag",
    "computer_scroll",
    "computer_type",
    "computer_key",
    "computer_shortcut",

    "clipboard_write",
    "wake_on_lan",

    "process_start",
    "process_input",
    "process_stop",

    "http_request",
}


_EVALUATION_PROMPT = """
You are Trajecta running an isolated skill evaluation.

Complete the evaluation task using only the available tools.

Rules:

- The evaluation environment is disposable.
- Never claim completion unless the outcome was actually verified.
- Do not contact real people.
- Do not purchase anything.
- Do not modify external accounts.
- Do not perform real-world destructive actions.
- Do not bypass permissions or safety restrictions.
- Do not ask the user for clarification during automatic evaluation.
- If the task cannot be completed safely in this environment, state the
  limitation clearly.
""".strip()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class ReplayResult(
    BaseModel
):
    model_config = ConfigDict(
        extra="forbid"
    )

    case_id: str

    repetition: int = 1

    variant: str

    completed: bool = False

    skipped: bool = False

    interrupted: bool = False

    success: (
        bool | None
    ) = None

    score: (
        float | None
    ) = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )

    judge_reason: (
        str | None
    ) = None

    final_text: str = ""

    duration_seconds: float = 0.0

    tool_calls: int = 0

    tool_names: list[str] = (
        Field(
            default_factory=list
        )
    )

    tool_errors: int = 0

    retry_count: int = 0

    safety_violations: int = 0

    input_tokens: int = 0

    output_tokens: int = 0

    error: (
        str | None
    ) = None

    verification: (
        OutcomeVerificationResult
        | None
    ) = None

    metadata: dict[
        str,
        Any,
    ] = Field(
        default_factory=dict
    )

    @property
    def total_tokens(
        self,
    ) -> int:
        return (
            self.input_tokens
            +
            self.output_tokens
        )


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------


class ReplayJudge:
    """
    Judge baseline and candidate with identical criteria.

    Deterministic assertions take priority.
    LLM-as-judge is only the fallback.
    """

    def __init__(
        self,
        settings: Settings,
    ) -> None:
        self._models = (
            BifrostModelFactory(
                settings
            )
        )

    async def grade(
        self,
        *,
        case: SkillEvalCase,
        result: ReplayResult,
        model_name: (
            str | None
        ) = None,
    ) -> ReplayResult:
        if result.skipped:
            return (
                result.model_copy(
                    update={
                        "success":
                            None,

                        "score":
                            None,

                        "judge_reason":
                            result.error
                            or "automatic replay skipped",
                    }
                )
            )

        if result.safety_violations:
            return (
                result.model_copy(
                    update={
                        "success":
                            False,

                        "score":
                            0.0,

                        "judge_reason":
                            "evaluation detected "
                            "a safety-policy violation",
                    }
                )
            )

        if result.interrupted:
            return (
                result.model_copy(
                    update={
                        "success":
                            None,

                        "score":
                            None,

                        "judge_reason":
                            "evaluation requested human approval",
                    }
                )
            )

        if result.error:
            return (
                result.model_copy(
                    update={
                        "success":
                            False,

                        "score":
                            0.0,

                        "judge_reason":
                            result.error,
                    }
                )
            )

        verification = (
            result.verification
        )

        # -----------------------------------
        # Deterministic state failure is
        # authoritative.
        # -----------------------------------

        if (
            verification is not None
            and verification.available
            and verification
            .required_failures
            > 0
        ):
            return (
                result.model_copy(
                    update={
                        "success":
                            False,

                        "score":
                            0.0,

                        "judge_reason":
                            "deterministic "
                            "outcome verification "
                            "failed",
                    }
                )
            )

        # -----------------------------------
        # Strong state evidence can establish
        # success without an LLM judge.
        # -----------------------------------

        if (
            verification is not None
            and verification.available
            and verification.decisive
            and verification.passed
        ):
            return (
                result.model_copy(
                    update={
                        "success":
                            True,

                        "score":
                            verification
                            .score,

                        "judge_reason":
                            "deterministic "
                            "outcome verification "
                            "passed",
                    }
                )
            )

        # -----------------------------------
        # Deterministic assertion
        # -----------------------------------

        if case.expected_contains:
            response = (
                result
                .final_text
                .lower()
            )

            matched = all(
                expected.lower()
                in response
                for expected
                in case
                .expected_contains
            )

            return (
                result.model_copy(
                    update={
                        "success":
                            matched,

                        "score":
                            (
                                1.0
                                if matched
                                else 0.0
                            ),

                        "judge_reason":
                            "deterministic "
                            "expected_contains "
                            "assertion",
                    }
                )
            )

        # -----------------------------------
        # LLM judge
        # -----------------------------------

        selected_model = (
            await self
            ._models
            .resolve_or_default(
                model_name
            )
        )

        model = (
            self._models
            .create(
                selected_model
            )
        )

        payload = {
            "task":
                case.task,

            "observable_state_verification":
                (
                    verification
                    .model_dump(
                        mode="json"
                    )

                    if verification
                    is not None

                    else None
                ),

            "rubric":
                case.rubric
                or [
                    "The requested task was completed correctly.",
                    "The result was actually verified.",
                ],

            "agent_response":
                result
                .final_text[
                    :20_000
                ],

            "execution": {
                "completed":
                    result.completed,

                "tool_calls":
                    result.tool_calls,

                "tool_names":
                    result.tool_names,

                "tool_errors":
                    result.tool_errors,

                "retries":
                    result.retry_count,
            },

            "instruction": (
                "Judge observable completion only. "
                "The observable_state_verification "
                "contains deterministic evidence from "
                "the actual post-run environment. "
                "Treat failed required assertions as "
                "task failure. Never let confident "
                "assistant prose override contradictory "
                "state evidence. Return JSON only with: "
                "success:boolean, score:number between "
                "0 and 1, reason:string."
            ),
        }

        try:
            response = (
                await model.ainvoke(
                    [
                        SystemMessage(
                            content=(
                                "You are a strict "
                                "task-completion evaluator. "
                                "Return valid JSON only."
                            )
                        ),

                        HumanMessage(
                            content=json.dumps(
                                payload,
                                ensure_ascii=False,
                            )
                        ),
                    ]
                )
            )

            data = (
                self._parse_json(
                    self._message_text(
                        response.content
                    )
                )
            )

            success = bool(
                data.get(
                    "success"
                )
            )

            score = float(
                data.get(
                    "score",
                    (
                        1.0
                        if success
                        else 0.0
                    ),
                )
            )

            score = max(
                0.0,
                min(
                    1.0,
                    score,
                ),
            )

            reason = str(
                data.get(
                    "reason"
                )
                or "LLM judge"
            )[:2000]

            return (
                result.model_copy(
                    update={
                        "success":
                            success,

                        "score":
                            score,

                        "judge_reason":
                            reason,
                    }
                )
            )

        except Exception as exc:
            # Judge failure must never accidentally become a PASS.
            return (
                result.model_copy(
                    update={
                        "success":
                            None,

                        "score":
                            None,

                        "judge_reason": (
                            "judge unavailable: "
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        ),
                    }
                )
            )

    @staticmethod
    def _message_text(
        content: Any,
    ) -> str:
        if isinstance(
            content,
            str,
        ):
            return content

        if isinstance(
            content,
            list,
        ):
            return "".join(
                str(
                    block.get(
                        "text"
                    )
                    or ""
                )
                for block
                in content
                if isinstance(
                    block,
                    dict,
                )
            )

        return str(
            content
        )

    @staticmethod
    def _parse_json(
        value: str,
    ) -> dict[
        str,
        Any,
    ]:
        text = value.strip()

        if text.startswith(
            "```"
        ):
            first_newline = (
                text.find(
                    "\n"
                )
            )

            if first_newline >= 0:
                text = text[
                    first_newline + 1:
                ]

            if text.endswith(
                "```"
            ):
                text = text[
                    :-3
                ]

        try:
            parsed = json.loads(
                text.strip()
            )

        except json.JSONDecodeError:
            start = text.find(
                "{"
            )

            end = text.rfind(
                "}"
            )

            if (
                start < 0
                or end <= start
            ):
                raise

            parsed = json.loads(
                text[
                    start:
                    end + 1
                ]
            )

        if not isinstance(
            parsed,
            dict,
        ):
            raise ValueError(
                "judge response "
                "must be a JSON object"
            )

        return parsed


# ---------------------------------------------------------------------------
# Deep Agents runner
# ---------------------------------------------------------------------------


class DeepAgentReplayExecutor:
    """
    Run one evaluation in a completely fresh environment.

    Baseline:
        verified skills only.

    Candidate:
        verified skills
        +
        candidate skill as later skill source.

    Deep Agents resolves later skill sources last, so the candidate version
    overrides an already-active skill with the same name during evaluation.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        memory: MemoryProvider,
        personal_tools: (
            PersonalToolProvider
        ),
        replay_fixtures: (
            ReplayFixtureStore
        ),
    ) -> None:
        if memory.store is None:
            raise RuntimeError(
                "MemoryProvider must "
                "be open before replay"
            )

        self._settings = settings

        self._memory = memory

        self._personal_tools = (
            personal_tools
        )

        self._replay_fixtures = (
            replay_fixtures
        )

        self._models = (
            BifrostModelFactory(
                settings
            )
        )

        self._judge = (
            ReplayJudge(
                settings
            )
        )

        self._verifier = (
            OutcomeVerifier(
                replay_fixtures
            )
        )

    async def run(
        self,
        case: SkillEvalCase,
        *,
        skill: (
            Skill | None
        ),
        repetition: int,
        model_name: (
            str | None
        ) = None,
    ) -> ReplayResult:
        variant = (
            "candidate"
            if skill
            is not None
            else "baseline"
        )

        # -----------------------------------
        # Hard safety gate
        # -----------------------------------

        if (
            case.mode
            == EvaluationMode.MANUAL
        ):
            return ReplayResult(
                case_id=case.id,

                repetition=(
                    repetition
                ),

                variant=variant,

                skipped=True,

                error=(
                    "case requires manual "
                    "evaluation because it "
                    "can affect real user state"
                ),
            )

        forbidden = (
            set(
                case.allowed_tools
            )
            & _NEVER_AUTO_REPLAY
        )

        if forbidden:
            return ReplayResult(
                case_id=case.id,

                repetition=(
                    repetition
                ),

                variant=variant,

                skipped=True,

                error=(
                    "automatic replay blocked "
                    "unsafe tools: "
                    + ", ".join(
                        sorted(
                            forbidden
                        )
                    )
                ),
            )

        selected_model = (
            await self
            ._models
            .resolve_or_default(
                model_name
            )
        )

        model = (
            self._models
            .create(
                selected_model
            )
        )

        started = (
            time.perf_counter()
        )

        sandbox: (
            DockerSandboxBackend
            | None
        ) = None

        with tempfile.TemporaryDirectory(
            prefix=(
                "trajecta-skill-eval-"
            )
        ) as tmp:
            root = Path(
                tmp
            )

            workspace = (
                root
                / "workspace"
            )

            uploads = (
                root
                / "uploads"
            )

            candidate_root = (
                root
                / "candidate-skills"
            )

            workspace.mkdir(
                parents=True,
                exist_ok=True,
            )

            uploads.mkdir(
                parents=True,
                exist_ok=True,
            )

            candidate_root.mkdir(
                parents=True,
                exist_ok=True,
            )

            # -----------------------------------
            # Restore initial-state fixture
            # -----------------------------------

            fixture_id = (
                case
                .metadata
                .get(
                    "replay_fixture_id"
                )
            )

            requires_fixture = bool(
                case
                .metadata
                .get(
                    "requires_replay_fixture"
                )
            )

            if (
                requires_fixture
                and not fixture_id
            ):
                return ReplayResult(
                    case_id=case.id,

                    repetition=(
                        repetition
                    ),

                    variant=variant,

                    skipped=True,

                    error=(
                        "evaluation case "
                        "requires a replay "
                        "fixture, but none "
                        "was captured"
                    ),

                    duration_seconds=(
                        time.perf_counter()
                        - started
                    ),
                )

            if fixture_id:
                try:
                    manifest = (
                        await self
                        ._replay_fixtures
                        .materialize(
                            str(
                                fixture_id
                            ),

                            workspace_root=(
                                workspace
                            ),

                            uploads_root=(
                                uploads
                            ),
                        )
                    )

                except Exception as exc:
                    return ReplayResult(
                        case_id=case.id,

                        repetition=(
                            repetition
                        ),

                        variant=variant,

                        skipped=True,

                        error=(
                            "failed to "
                            "materialize replay "
                            "fixture: "
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        ),

                        duration_seconds=(
                            time.perf_counter()
                            - started
                        ),
                    )

            else:
                manifest = None

            # -----------------------------------
            # Default isolated backend
            # -----------------------------------

            docker_available = False

            if (
                self
                ._settings
                .sandbox
                .enabled
            ):
                try:
                    sandbox = (
                        DockerSandboxBackend(
                            image=(
                                self
                                ._settings
                                .sandbox
                                .image
                            ),

                            workspace_root=(
                                str(
                                    workspace
                                )
                            ),

                            uploads_root=(
                                str(
                                    uploads
                                )
                            ),

                            timeout_seconds=(
                                self
                                ._settings
                                .sandbox
                                .timeout_seconds
                            ),

                            memory_limit=(
                                self
                                ._settings
                                .sandbox
                                .memory_limit
                            ),

                            cpu_limit=(
                                self
                                ._settings
                                .sandbox
                                .cpu_limit
                            ),

                            network_enabled=False,

                            auto_remove=True,
                        )
                    )

                    default_backend: Any = (
                        sandbox
                    )

                    docker_available = True

                except Exception:
                    default_backend = (
                        FilesystemBackend(
                            root_dir=str(
                                root
                            ),

                            virtual_mode=True,
                        )
                    )

            else:
                default_backend = (
                    FilesystemBackend(
                        root_dir=str(
                            root
                        ),

                        virtual_mode=True,
                    )
                )

            if (
                "execute"
                in case.allowed_tools
                and not docker_available
            ):
                if sandbox:
                    sandbox.close()

                return ReplayResult(
                    case_id=case.id,

                    repetition=(
                        repetition
                    ),

                    variant=variant,

                    skipped=True,

                    error=(
                        "case requires execute "
                        "but isolated Docker "
                        "sandbox is unavailable"
                    ),

                    duration_seconds=(
                        time.perf_counter()
                        - started
                    ),
                )

            store = (
                self
                ._memory
                .store
            )

            assert (
                store
                is not None
            )

            # -----------------------------------
            # Composite filesystem
            # -----------------------------------

            backend = CompositeBackend(
                default=(
                    default_backend
                ),

                routes={
                    "/skills/":
                        StoreBackend(
                            namespace=lambda _rt: (
                                "trajecta-local",
                                "skills",
                            ),

                            store=store,
                        ),

                    "/memories/":
                        StoreBackend(
                            namespace=lambda _rt: (
                                "trajecta-local",
                            ),

                            store=store,
                        ),

                    "/candidate-skills/":
                        FilesystemBackend(
                            root_dir=str(
                                candidate_root
                            ),

                            virtual_mode=True,
                        ),

                    "/workspace/":
                        FilesystemBackend(
                            root_dir=str(
                                workspace
                            ),

                            virtual_mode=True,
                        ),

                    "/uploads/":
                        FilesystemBackend(
                            root_dir=str(
                                uploads
                            ),

                            virtual_mode=True,
                        ),
                },
            )

            # -----------------------------------
            # Candidate skill
            # -----------------------------------

            candidate_source = (
                "/candidate-skills/"
            )

            if skill is not None:
                for (
                    relative,
                    content,
                ) in (
                    skill
                    .bundle_files()
                    .items()
                ):
                    path = (
                        f"{candidate_source}"
                        f"{skill.name}/"
                        f"{relative}"
                    )

                    response = (
                        await backend
                        .awrite(
                            path,
                            content,
                        )
                    )

                    if response.error:
                        return ReplayResult(
                            case_id=(
                                case.id
                            ),

                            repetition=(
                                repetition
                            ),

                            variant=(
                                variant
                            ),

                            error=(
                                "failed to stage "
                                "candidate skill: "
                                f"{response.error}"
                            ),
                        )

            # -----------------------------------
            # Only allow requested custom tools
            # -----------------------------------

            requested_custom = (
                set(
                    case.allowed_tools
                )
                - _BUILTIN_TOOL_NAMES
            )

            custom_tools = [
                tool
                for tool
                in (
                    self
                    ._personal_tools
                    .get_tools()
                )
                if tool.name
                in requested_custom
            ]

            skill_sources = [
                "/skills/"
            ]

            if skill is not None:
                # Last source wins when the same
                # skill exists in both sources.
                skill_sources.append(
                    candidate_source
                )

            # -----------------------------------
            # Build isolated Deep Agent
            # -----------------------------------

            agent = create_deep_agent(
                model=model,

                tools=(
                    custom_tools
                ),

                system_prompt=(
                    _EVALUATION_PROMPT
                ),

                middleware=[
                    TodoListMiddleware()
                ],

                backend=backend,

                store=store,

                checkpointer=(
                    MemorySaver()
                ),

                skills=(
                    skill_sources
                ),

                permissions=[
                    FilesystemPermission(
                        operations=[
                            "write"
                        ],

                        paths=[
                            "/skills/**"
                        ],

                        mode="deny",
                    ),

                    FilesystemPermission(
                        operations=[
                            "write"
                        ],

                        paths=[
                            "/candidate-skills/**"
                        ],

                        mode="deny",
                    ),
                ],
            )

            result = ReplayResult(
                case_id=case.id,

                repetition=(
                    repetition
                ),

                variant=variant,

                metadata={
                    "replay_fixture_id": (
                        str(fixture_id)
                        if fixture_id
                        else None
                    ),

                    "replay_fixture_file_count": (
                        manifest.file_count
                        if manifest
                        else 0
                    ),
                },
            )

            text_chunks: list[str] = []

            tool_names: list[str] = []

            input_tokens = 0

            output_tokens = 0

            failed_tools: set[str] = (
                set()
            )

            try:
                stream = (
                    agent.astream(
                        {
                            "messages": [
                                {
                                    "role":
                                        "user",

                                    "content":
                                        case.task,
                                }
                            ]
                        },

                        config={
                            "configurable": {
                                "thread_id": (
                                    "skill-eval:"
                                    f"{uuid.uuid4().hex}"
                                )
                            }
                        },

                        stream_mode=[
                            "messages",
                            "updates",
                        ],

                        subgraphs=True,

                        version="v2",

                        durability="exit",
                    )
                )

                async for chunk in stream:
                    chunk_type = (
                        chunk.get(
                            "type"
                        )
                    )

                    namespace = tuple(
                        chunk.get(
                            "ns"
                        )
                        or ()
                    )

                    source = (
                        self._source(
                            namespace
                        )
                    )

                    # -------------------------
                    # Messages
                    # -------------------------

                    if (
                        chunk_type
                        == "messages"
                    ):
                        (
                            token,
                            _metadata,
                        ) = (
                            chunk[
                                "data"
                            ]
                        )

                        if isinstance(
                            token,
                            AIMessageChunk,
                        ):
                            if (
                                source
                                == "main"
                                and not token
                                .tool_call_chunks
                            ):
                                text = (
                                    self
                                    ._content_text(
                                        token.content
                                    )
                                )

                                if text:
                                    text_chunks.append(
                                        text
                                    )

                            usage = getattr(
                                token,
                                "usage_metadata",
                                None,
                            )

                            if isinstance(
                                usage,
                                Mapping,
                            ):
                                input_tokens += int(
                                    usage.get(
                                        "input_tokens"
                                    )
                                    or 0
                                )

                                output_tokens += int(
                                    usage.get(
                                        "output_tokens"
                                    )
                                    or 0
                                )

                        elif isinstance(
                            token,
                            ToolMessage,
                        ):
                            name = str(
                                token.name
                                or "unknown"
                            )

                            # If this tool previously failed and is being
                            # invoked again, count that as a retry.
                            if (
                                name
                                in failed_tools
                            ):
                                result.retry_count += 1

                            tool_names.append(
                                name
                            )

                            status = str(
                                getattr(
                                    token,
                                    "status",
                                    "",
                                )
                                or ""
                            ).lower()

                            if status == "error":
                                result.tool_errors += 1

                                failed_tools.add(
                                    name
                                )

                            else:
                                failed_tools.discard(
                                    name
                                )

                    # -------------------------
                    # Interrupt
                    # -------------------------

                    elif (
                        chunk_type
                        == "updates"
                    ):
                        data = chunk.get(
                            "data"
                        )

                        if (
                            isinstance(
                                data,
                                dict,
                            )
                            and "__interrupt__"
                            in data
                        ):
                            result.interrupted = (
                                True
                            )

                            result.error = (
                                "evaluation run "
                                "requested "
                                "human approval"
                            )

                            break

                result.completed = (
                    not result
                    .interrupted
                )

                result.final_text = (
                    "".join(
                        text_chunks
                    )
                    .strip()
                )

                result.tool_calls = (
                    len(
                        tool_names
                    )
                )

                result.tool_names = (
                    tool_names
                )

                result.input_tokens = (
                    input_tokens
                )

                result.output_tokens = (
                    output_tokens
                )

                if (
                    not result.error
                    and not result.interrupted
                    and result.completed
                ):
                    result.verification = (
                        await self
                        ._verifier
                        .verify(
                            case=case,
                            workspace_root=workspace,
                            uploads_root=uploads,
                        )
                    )

                # Custom external-side-effect tools were not even supplied.
                # If one somehow appears anyway, treat it as a hard violation.
                unexpected_unsafe = (
                    set(
                        tool_names
                    )
                    & _NEVER_AUTO_REPLAY
                )

                if unexpected_unsafe:
                    result.safety_violations += (
                        len(
                            unexpected_unsafe
                        )
                    )

                    result.metadata[
                        "unsafe_tools"
                    ] = sorted(
                        unexpected_unsafe
                    )

            except Exception as exc:
                result.error = (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

            finally:
                result.duration_seconds = (
                    time.perf_counter()
                    - started
                )

                if sandbox:
                    sandbox.close()

        return (
            await self
            ._judge
            .grade(
                case=case,

                result=result,

                model_name=(
                    selected_model
                ),
            )
        )

    @staticmethod
    def _source(
        namespace: tuple[
            str,
            ...,
        ],
    ) -> str:
        for segment in namespace:
            if segment.startswith(
                "tools:"
            ):
                return segment

        return "main"

    @staticmethod
    def _content_text(
        content: Any,
    ) -> str:
        if isinstance(
            content,
            str,
        ):
            return content

        if not isinstance(
            content,
            list,
        ):
            return ""

        chunks: list[str] = []

        for block in content:
            if not isinstance(
                block,
                dict,
            ):
                continue

            if block.get(
                "type"
            ) not in {
                "text",
                "output_text",
            }:
                continue

            value = block.get(
                "text"
            )

            if isinstance(
                value,
                str,
            ):
                chunks.append(
                    value
                )

        return "".join(
            chunks
        )


# ---------------------------------------------------------------------------
# Baseline vs candidate pairing
# ---------------------------------------------------------------------------


class SkillReplay:
    """
    Run the same held-out task against:

        baseline agent
        candidate-skill agent

    Runs are interleaved so temporary provider/network conditions affect
    baseline and candidate as similarly as possible.
    """

    def __init__(
        self,
        executor: (
            DeepAgentReplayExecutor
        ),
    ) -> None:
        self._executor = executor

    async def replay_case(
        self,
        *,
        skill: Skill,
        case: SkillEvalCase,
        repetitions: int,
        model_name: (
            str | None
        ) = None,
    ) -> list[
        tuple[
            ReplayResult,
            ReplayResult,
        ]
    ]:
        pairs: list[
            tuple[
                ReplayResult,
                ReplayResult,
            ]
        ] = []

        for repetition in range(
            1,
            repetitions + 1,
        ):
            baseline = (
                await self
                ._executor
                .run(
                    case,

                    skill=None,

                    repetition=(
                        repetition
                    ),

                    model_name=(
                        model_name
                    ),
                )
            )

            candidate = (
                await self
                ._executor
                .run(
                    case,

                    skill=skill,

                    repetition=(
                        repetition
                    ),

                    model_name=(
                        model_name
                    ),
                )
            )

            pairs.append(
                (
                    baseline,
                    candidate,
                )
            )

        return pairs