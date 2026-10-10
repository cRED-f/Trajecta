"""Offline self-check for the memory system.

Runs fully offline (no network): temp dirs, open the provider (SQLite checkpointer
+ store split on langgraph.db, FTS, embedded Qdrant), exercise short-term threads,
semantic put/get/search via the real StoreBackend, durable episodic
consolidation/search, procedural promote/list, and confirm agent_kwargs assembles.

Usage:  python -m server.src.memory.verify
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from server.src.config import Settings
from server.src.memory.provider import MemoryProvider


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

@dataclass
class Ctx:
    tmp: Path = field(default_factory=lambda: Path(tempfile.mkdtemp()))
    settings: Settings | None = None
    provider: MemoryProvider | None = None


async def _open(tmp: Path) -> MemoryProvider:
    settings = Settings.model_validate(
        {
            "memory": {
                "db_path": str(tmp / "trajecta.db"),
                "langgraph_db_path": str(tmp / "langgraph.db"),
                "langgraph_server_url": "http://127.0.0.1:2024",
                "memory_files": ["/memories/AGENTS.md"],
                "skills_path": "/skills/",
                "vector_store": {"enabled": True, "mode": "embedded", "path": str(tmp / "qdrant")},
            }
        }
    )
    provider = MemoryProvider(settings)
    await provider.open()
    return provider


async def check_short_term(p: MemoryProvider) -> None:
    st = p.short_term
    tid = st.new_thread()
    await st.save_thread_state({"messages": ["hello"], "files": {"a.txt": "x"}}, thread_id=tid)
    state = await st.get_thread_state(tid)
    assert state is not None, "short-term thread state not found"
    assert state.get("files", {}).get("a.txt") == "x"
    print("  ok  short-term write/read thread")

async def check_semantic(p: MemoryProvider) -> None:
    sem = p.semantic

    # --------------------------------------------------------------
    # Write semantic memory
    # --------------------------------------------------------------

    await sem.aput(
        "user/pref",
        "user likes dark mode",
    )

    # --------------------------------------------------------------
    # Verify canonical Deep Agents store
    # --------------------------------------------------------------

    got = await sem.aget("user/pref")

    assert got == "user likes dark mode", (
        f"semantic get got {got!r}"
    )

    # --------------------------------------------------------------
    # Verify CompositeBackend routing
    #
    # /memories/user/pref
    #        ↓
    # CompositeBackend strips /memories/
    #        ↓
    # StoreBackend stores /user/pref
    # --------------------------------------------------------------

    assert p.store is not None

    raw = await p.store.aget(
        ("trajecta-local",),
        "/user/pref",
    )

    assert raw is not None, (
        "Semantic memory was not stored using "
        "the CompositeBackend-stripped path"
    )

    # --------------------------------------------------------------
    # Verify hybrid semantic search
    # --------------------------------------------------------------

    hits = await sem.asearch(
        "dark mode",
        limit=5,
    )

    assert any(
        "dark mode" in (hit.get("content") or "")
        for hit in hits
    ), "semantic search missed stored memory"

    # --------------------------------------------------------------
    # Verify Qdrant vector retrieval specifically
    #
    # Important:
    # This prevents FTS from masking a broken vector search.
    # --------------------------------------------------------------

    memory_id = sem._memory_id("user/pref")

    vector_hits = p.vector.search(
        "memories",
        "dark mode",
        limit=10,
    )

    assert any(
        hit.get("doc_id") == memory_id
        for hit in vector_hits
    ), (
        "semantic memory was not retrievable "
        "from Qdrant vector search"
    )

    # --------------------------------------------------------------
    # Delete semantic memory
    # --------------------------------------------------------------

    await sem.adelete("user/pref")

    # --------------------------------------------------------------
    # Verify canonical memory deletion
    # --------------------------------------------------------------

    assert await sem.aget("user/pref") is None, (
        "semantic memory still exists "
        "in the canonical Deep Agents store"
    )

    # --------------------------------------------------------------
    # Verify FTS deletion
    # --------------------------------------------------------------

    if p.fts is not None:
        fts_row = await p.fts.fetch_memory(memory_id)

        assert fts_row is None, (
            "deleted semantic memory still exists "
            "in SQLite/FTS"
        )

    # --------------------------------------------------------------
    # Verify Qdrant deletion
    # --------------------------------------------------------------

    vector_hits = p.vector.search(
        "memories",
        "dark mode",
        limit=10,
    )

    assert all(
        hit.get("doc_id") != memory_id
        for hit in vector_hits
    ), (
        "deleted semantic memory still exists "
        "in Qdrant"
    )

    print(
        "  ok  semantic CRUD + FTS + vector retrieval "
        "+ deletion + routing"
    )
async def check_episodic(p: MemoryProvider) -> None:
    from server.src.skills.trajectory_store import TrajectoryStore

    assert p.sqlite is not None
    store = TrajectoryStore(p.sqlite)
    _, trajectory_id = await store.begin(
        goal="Investigate billing API duplicate invoices",
        thread_id="abc123",
        user_id="local",
    )
    await store.append(
        trajectory_id, event_type="tool.result",
        data={"name": "billing_lookup", "status": "success"},
    )
    await store.finish(
        trajectory_id, outcome="completed", result="Found duplicate invoice records"
    )
    episode = await p.episodic.consolidate(trajectory_id)
    assert episode is not None
    assert episode["source_trajectory_id"] == trajectory_id
    assert episode["outcome_verified"] is False
    hits = await p.episodic.search("billing invoices", limit=5)
    assert hits and hits[0]["id"] == episode["id"]
    assert await p.episodic.get(episode["id"]) is not None
    print("  ok  durable episodes + hybrid retrieval + provenance")


async def check_procedural(p: MemoryProvider) -> None:
    proc = p.procedural

    await proc.apromote(
        "deploy",
        """
# Deploy

Deploy the current native project.

## Process

1. Inspect the launch script.
2. Build the native application.
3. Run the native process.
4. Verify the health endpoint.
""",
        description=(
            "Deploy native projects and verify "
            "that the resulting application is healthy."
        ),
    )

    names = await proc.alist()

    assert "deploy" in names, (
        f"procedural list={names}"
    )

    content = await proc.aload(
        "deploy"
    )

    assert content is not None
    assert "name: deploy" in content
    assert "description:" in content
    assert "# Deploy" in content

    hits = await proc.asearch(
        "deploy",
        limit=5,
    )

    assert hits
    assert hits[0]["name"] == "deploy"

    # Verify CompositeBackend stripped /skills/.
    assert p.store is not None

    raw = await p.store.aget(
        ("trajecta-local", "skills"),
        "/deploy/SKILL.md",
    )

    assert raw is not None, (
        "Skill was not stored under the "
        "CompositeBackend-stripped path"
    )

    print(
        "  ok  procedural valid skill + routing + search"
    )


def check_agent_kwargs(p: MemoryProvider) -> None:
    kw = p.agent_kwargs()
    assert kw["checkpointer"] is p.checkpointer
    assert kw["store"] is p.store
    assert kw["backend"] is p.backend
    assert "/memories/AGENTS.md" in kw["memory"]
    assert kw["skills"] == ["/skills/"]
    print("  ok  agent_kwargs assembled (checkpointer/store/backend/memory/skills)")


async def _run() -> None:
    print("memory verify")
    tmp = Path(tempfile.mkdtemp())
    p = await _open(tmp)
    try:
        await check_short_term(p)
        await check_semantic(p)
        await check_episodic(p)
        await check_procedural(p)
        check_agent_kwargs(p)
    finally:
        await p.close()
    print("  ok  all checks passed")


def main() -> None:
    try:
        asyncio.run(_run())
    except AssertionError as e:
        print(f"FAIL: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
