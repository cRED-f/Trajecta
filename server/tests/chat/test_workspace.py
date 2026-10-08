"""Conversation workspaces: folder selection, persistence, and tool binding.

These tests deliberately exercise the host directory the agent's file tools
resolve against, not just the picker or the metadata column — a conversation
that *says* it is on Project A while `/workspace/` still points at the
configured default would pass a UI-only test.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from server.src.chat.models import ConversationCreate
from server.src.chat.repository import ChatRepository
from server.src.chat.runs import ChatRunRegistry
from server.src.chat.service import ChatService, WorkspaceBusy
from server.src.chat.workspace import METADATA_KEY, conversation_workspace, validate_workspace_path
from server.src.config import Settings
from server.src.guardrails.content import ContentGuardrailService
from server.src.guardrails.policy import PermissionPolicyStore
from server.src.memory.provider import MemoryProvider
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.evaluation.fixtures import ReplayFixtureStore
from server.src.skills.trajectory_store import TrajectoryStore
from server.src.tools.personal.documents import VirtualPathResolver
from server.src.tools.personal.provider import PersonalToolProvider

DEFAULT_MODEL = "9router/claude-opus-free"


class DummyAttachments:
    async def save(self, **_: Any) -> Any:
        raise AssertionError("not used")


class DummyRag:
    pass


class DummyRuntime:
    pass


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    payload: dict[str, Any] = {
        "chat": {
            "default_model": DEFAULT_MODEL,
            "uploads_path": str(tmp_path / "uploads"),
        },
        "memory": {
            "db_path": str(tmp_path / "trajecta.db"),
            "langgraph_db_path": str(tmp_path / "langgraph.db"),
            "vector_store": {"enabled": False},
        },
        "tools": {"workspace_root": str(tmp_path / "default-workspace")},
        "sandbox": {"enabled": False},
    }
    for key, value in overrides.items():
        payload[key] = value
    return Settings.model_validate(payload)


def _service(db: SQLiteDatabase, settings: Settings) -> ChatService:
    return ChatService(
        settings,
        ChatRepository(db),
        DummyAttachments(),  # type: ignore[arg-type]
        DummyRag(),  # type: ignore[arg-type]
        DummyRuntime(),  # type: ignore[arg-type]
        ChatRunRegistry(),
        TrajectoryStore(db),
        ReplayFixtureStore(settings, db),
        ContentGuardrailService(settings, PermissionPolicyStore(db)),
    )


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def test_validate_rejects_a_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist or is inaccessible"):
        validate_workspace_path(str(tmp_path / "not-created"))


def test_validate_rejects_a_file(tmp_path: Path) -> None:
    target = tmp_path / "readme.md"
    target.write_text("hi", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a directory"):
        validate_workspace_path(str(target))


def test_validate_rejects_a_filesystem_root(tmp_path: Path) -> None:
    root = Path(tmp_path.anchor or tmp_path)
    if root == Path(root.anchor):
        with pytest.raises(ValueError, match="filesystem root"):
            validate_workspace_path(str(root))


def test_validate_rejects_empty_and_nul_paths() -> None:
    for bad in ("", "   ", "\x00"):
        with pytest.raises(ValueError, match="required"):
            validate_workspace_path(bad)


def test_validate_resolves_to_an_absolute_path(tmp_path: Path) -> None:
    folder = tmp_path / "project"
    folder.mkdir()

    resolved = validate_workspace_path(str(folder))

    assert Path(resolved).is_absolute()
    assert Path(resolved).name == "project"


def test_validate_follows_a_symlink_to_the_real_folder(tmp_path: Path) -> None:
    real = tmp_path / "real-project"
    real.mkdir()
    link = tmp_path / "link-project"

    try:
        os.symlink(real, link, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks is not permitted on this host")

    assert validate_workspace_path(str(link)) == str(real.resolve())


# ---------------------------------------------------------------------------
# Resolving a conversation's folder
# ---------------------------------------------------------------------------


def test_conversation_without_a_folder_keeps_the_configured_default(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    workspace = conversation_workspace({"metadata": {}}, settings)

    assert workspace == str((tmp_path / "default-workspace").resolve())


def test_a_pinned_folder_wins_over_the_configured_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    project = tmp_path / "project"
    project.mkdir()

    workspace = conversation_workspace(
        {"metadata": {METADATA_KEY: str(project)}},
        settings,
    )

    assert workspace == str(project.resolve())
    assert workspace != str((tmp_path / "default-workspace").resolve())


def test_a_folder_deleted_after_selection_fails_loudly(tmp_path: Path) -> None:
    """Falling back silently would write somewhere the user did not choose."""
    settings = _settings(tmp_path)
    gone = tmp_path / "deleted"
    gone.mkdir()
    stored = str(gone)
    gone.rmdir()

    with pytest.raises(ValueError, match="no longer usable"):
        conversation_workspace({"metadata": {METADATA_KEY: stored}}, settings)


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------


async def test_create_conversation_persists_the_selected_folder(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()

    settings = _settings(tmp_path)
    service = _service(db, settings)
    project = tmp_path / "portfolio"
    project.mkdir()

    conversation = await service.create_conversation(
        ConversationCreate(title="portfolio", workspace_path=str(project))
    )

    stored = await service.get_conversation(conversation.id)
    assert stored.metadata[METADATA_KEY] == str(project.resolve())

    # Reload through a second service on the same file: the folder survives
    # a restart, not just the object that wrote it.
    reopened = SQLiteDatabase(tmp_path / "chat.db")
    await reopened.open()
    after_restart = await _service(reopened, settings).get_conversation(conversation.id)
    await reopened.close()

    assert after_restart.metadata[METADATA_KEY] == str(project.resolve())
    await db.close()


async def test_create_rejects_an_unusable_folder(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()

    service = _service(db, _settings(tmp_path))

    with pytest.raises(ValueError, match="does not exist"):
        await service.create_conversation(
            ConversationCreate(workspace_path=str(tmp_path / "missing"))
        )

    await db.close()


async def test_create_does_not_trust_metadata(tmp_path: Path) -> None:
    """A caller-supplied metadata entry never reaches the run unvalidated."""
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()

    service = _service(db, _settings(tmp_path))

    with pytest.raises(ValueError, match="does not exist"):
        await service.create_conversation(
            ConversationCreate(metadata={METADATA_KEY: str(tmp_path / "missing")})
        )

    await db.close()


async def test_two_conversations_keep_independent_folders(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()

    service = _service(db, _settings(tmp_path))
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()

    chat_a = await service.create_conversation(
        ConversationCreate(workspace_path=str(project_a))
    )
    chat_b = await service.create_conversation(
        ConversationCreate(workspace_path=str(project_b))
    )

    await service.select_workspace(chat_b.id, str(project_a))

    reloaded_a = await service.get_conversation(chat_a.id)
    reloaded_b = await service.get_conversation(chat_b.id)

    assert reloaded_a.metadata[METADATA_KEY] == str(project_a.resolve())
    # Switching B must not rewrite A, or every chat would drift together.
    assert reloaded_b.metadata[METADATA_KEY] == str(project_a.resolve())

    await service.select_workspace(chat_a.id, str(project_b))
    reloaded_a = await service.get_conversation(chat_a.id)
    reloaded_b = await service.get_conversation(chat_b.id)

    assert reloaded_a.metadata[METADATA_KEY] == str(project_b.resolve())
    assert reloaded_b.metadata[METADATA_KEY] == str(project_a.resolve())

    await db.close()


async def test_switching_is_blocked_while_a_run_is_prepared(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()

    service = _service(db, _settings(tmp_path))
    project = tmp_path / "project"
    project.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    conversation = await service.create_conversation(
        ConversationCreate(workspace_path=str(project))
    )
    runs = service._runs

    # A preflight in progress already owns the slot.
    await runs.reserve(conversation.id)
    with pytest.raises(WorkspaceBusy, match="running"):
        await service.select_workspace(conversation.id, str(other))
    await runs.release(conversation.id)

    # A registered run owns it too.
    await runs.register(conversation.id, "run-1")
    with pytest.raises(WorkspaceBusy, match="running"):
        await service.select_workspace(conversation.id, str(other))
    await runs.unregister(conversation.id, "run-1")

    # Released, the same switch now succeeds.
    updated = await service.select_workspace(conversation.id, str(other))
    assert updated.metadata[METADATA_KEY] == str(other.resolve())

    await db.close()


async def test_switching_is_blocked_by_a_pending_approval(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()

    service = _service(db, _settings(tmp_path))
    project = tmp_path / "project"
    project.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    conversation = await service.create_conversation(
        ConversationCreate(workspace_path=str(project))
    )
    branch = await service._repository.get_active_branch(conversation.id)
    assert branch is not None
    user_message_id = "msg-1"

    await service._repository.save_pending_approval(
        conversation_id=conversation.id,
        branch_id=branch.id,
        thread_id=branch.thread_id,
        checkpoint_id="ckpt-1",
        user_message_id=user_message_id,
        model_name=DEFAULT_MODEL,
        interrupt_data={},
    )

    with pytest.raises(WorkspaceBusy, match="approval"):
        await service.select_workspace(conversation.id, str(other))

    # Nothing was written while the approval was pending.
    stored = await service.get_conversation(conversation.id)
    assert stored.metadata[METADATA_KEY] == str(project.resolve())

    await db.close()


async def test_selecting_a_folder_rejects_an_unusable_one(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()

    service = _service(db, _settings(tmp_path))
    project = tmp_path / "project"
    project.mkdir()

    conversation = await service.create_conversation(
        ConversationCreate(workspace_path=str(project))
    )

    with pytest.raises(ValueError, match="does not exist"):
        await service.select_workspace(conversation.id, str(tmp_path / "gone"))

    stored = await service.get_conversation(conversation.id)
    assert stored.metadata[METADATA_KEY] == str(project.resolve())

    await db.close()


# ---------------------------------------------------------------------------
# The actual folder → tool binding
# ---------------------------------------------------------------------------


@pytest.fixture
async def memory(tmp_path: Path):
    provider = MemoryProvider(_settings(tmp_path))
    await provider.open()
    try:
        yield provider
    finally:
        await provider.close()


def _marker(folder: Path, name: str, body: str) -> Path:
    target = folder / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


async def test_file_tools_read_and_write_the_selected_folder(
    memory: MemoryProvider,
    tmp_path: Path,
) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    _marker(project_a, "only-in-a.txt", "alpha marker")
    _marker(project_b, "only-in-b.txt", "beta marker")

    backend_a = memory.agent_kwargs(allow_execute=False, workspace_root=str(project_a))[
        "backend"
    ]
    backend_b = memory.agent_kwargs(allow_execute=False, workspace_root=str(project_b))[
        "backend"
    ]

    # read_file
    read_a = await backend_a.aread("/workspace/only-in-a.txt")
    assert "alpha marker" in _text(read_a)
    with pytest.raises(Exception, match="not found|No such file|does not exist"):
        await backend_a.aread("/workspace/only-in-b.txt")

    # grep
    hits_a = await backend_a.agrep("alpha")
    assert _grep_paths(hits_a) and all("project-a" in p for p in _grep_paths(hits_a))

    hits_b = await backend_b.agrep("alpha")
    assert _grep_paths(hits_b) == []

    # write_file lands in the selected folder and nowhere else
    await backend_a.awrite("/workspace/generated.txt", "written by a")
    assert (project_a / "generated.txt").read_text(encoding="utf-8") == "written by a"
    assert not (project_b / "generated.txt").exists()

    # edit_file
    await backend_a.aedit("/workspace/generated.txt", "written by a", "edited by a")
    assert "edited by a" in (project_a / "generated.txt").read_text(encoding="utf-8")

    # A second conversation sees its own tree, not A's leftovers.
    read_b = await backend_b.aread("/workspace/only-in-b.txt")
    assert "beta marker" in _text(read_b)
    with pytest.raises(Exception, match="not found|No such file|does not exist"):
        await backend_b.aread("/workspace/generated.txt")


async def test_the_default_conversation_still_uses_the_configured_folder(
    memory: MemoryProvider,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    default_root = tmp_path / "default-workspace"

    workspace = conversation_workspace({"metadata": {}}, settings)
    backend = memory.agent_kwargs(allow_execute=False, workspace_root=workspace)["backend"]

    await backend.awrite("/workspace/default-note.txt", "default")

    assert (default_root / "default-note.txt").read_text(encoding="utf-8") == "default"


async def test_personal_tools_follow_the_selected_folder(
    memory: MemoryProvider,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()

    root = PersonalToolProvider(settings, memory)
    try:
        scoped_a = root.for_workspace(str(project_a))
        scoped_b = root.for_workspace(str(project_b))

        assert scoped_a is not scoped_b
        # Repeated asks reuse one provider instead of rebuilding the world.
        assert root.for_workspace(str(project_a)) is scoped_a

        _marker(project_a, "alpha.md", "only in a")
        _marker(project_b, "beta.md", "only in b")

        assert scoped_a.documents.paths.workspace_root == project_a.resolve()
        assert scoped_a.documents.metadata("/workspace/alpha.md")["name"] == "alpha.md"

        with pytest.raises(Exception, match="escapes|not found|No such file"):
            scoped_a.documents.metadata("/workspace/beta.md")

        # The default provider still points at the configured folder: nothing
        # mutated shared settings out from under another conversation.
        assert Path(root.workspace_root) == (tmp_path / "default-workspace").resolve()
        assert Path(settings.tools.workspace_root) == str(tmp_path / "default-workspace")
    finally:
        await root.close()


async def test_virtual_paths_cannot_escape_the_selected_folder(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "inside.txt").write_text("ok", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("sssh", encoding="utf-8")

    resolver = VirtualPathResolver(str(project), str(tmp_path / "uploads"))

    assert resolver.resolve("/workspace/inside.txt") == project / "inside.txt"

    with pytest.raises(ValueError, match="escapes"):
        resolver.resolve("/workspace/../secret.txt")


# ---------------------------------------------------------------------------
# Docker sandbox
# ---------------------------------------------------------------------------


def _sandbox_image_available(image: str) -> bool:
    """Checked inside the test, never at import time.

    A module-level ``skipif`` would open the Docker socket while pytest is
    still collecting, and a daemon that accepts the connection but never
    answers would stall the whole suite.
    """
    try:
        import docker

        client = docker.from_env(timeout=5)
        client.images.get(image)
    except Exception:
        return False
    return True


async def test_execute_runs_inside_the_selected_project(tmp_path: Path) -> None:
    if not _sandbox_image_available("trajecta-sandbox:latest"):
        pytest.skip("trajecta-sandbox image is not built on this host")

    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    _marker(project_a, "marker-a.txt", "a")
    _marker(project_b, "marker-b.txt", "b")

    settings = _settings(tmp_path, sandbox={"enabled": True})
    provider = MemoryProvider(settings)
    await provider.open()

    try:
        backend_a = provider.agent_kwargs(allow_execute=True, workspace_root=str(project_a))[
            "backend"
        ]
        backend_b = provider.agent_kwargs(allow_execute=True, workspace_root=str(project_b))[
            "backend"
        ]

        sandbox_a = backend_a.default
        sandbox_b = backend_b.default

        assert sandbox_a is not sandbox_b, "one container must not serve two projects"
        assert sandbox_a.workspace_root == str(project_a.resolve())
        assert sandbox_b.workspace_root == str(project_b.resolve())

        # Same folder again reuses the container rather than forking it.
        again = provider.agent_kwargs(
            allow_execute=True, workspace_root=str(project_a)
        )["backend"]
        assert again.default is sandbox_a

        listing = sandbox_a.execute("ls /workspace").output
        assert "marker-a.txt" in listing
        assert "marker-b.txt" not in listing
    finally:
        await provider.close()


# ---------------------------------------------------------------------------
# Helpers for the deepagents backend results
# ---------------------------------------------------------------------------


def _text(result: Any) -> str:
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if content is None and isinstance(result, dict):
        content = result.get("content")
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content if content is not None else result)


def _grep_paths(result: Any) -> list[str]:
    matches = getattr(result, "matches", None)
    if matches is None and isinstance(result, dict):
        matches = result.get("matches")
    if matches is None and isinstance(result, list):
        matches = result
    paths: list[str] = []
    for match in matches or []:
        path = getattr(match, "path", None)
        if path is None and isinstance(match, dict):
            path = match.get("path")
        if path is not None:
            paths.append(str(path))
    return paths
