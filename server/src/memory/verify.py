"""Offline self-check for the memory system.

Runs fully offline (no network): temp dirs, open the provider (SQLite checkpointer
+ store split on langgraph.db, FTS, embedded Qdrant), exercise short-term threads,
semantic put/get/search via the real StoreBackend, fake episodic search via an
injected SDK client, procedural promote/list, and confirm agent_kwargs assembles.

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
# Stub SDK client (no live `langgraph dev` server)
# ---------------------------------------------------------------------------

class FakeThreads:
    def __init__(self) -> None:
        self._threads: dict[str, dict] = {}
        self._histories: dict[str, list] = {}

    async def search(self, metadata=None, limit=None):  # noqa: ANN001, ANN002
        if metadata and metadata.get("user_id") == "alice":
            return [{"thread_id": "abc123", "metadata": metadata, "created_at": "2026-01-01"}]
        return []

    async def get_history(self, thread_id: str, limit=None):  # noqa: ANN001, ANN002
        if thread_id == "abc123":
            return [{"type": "human", "role": "user", "content": "hello"}]
        return []


class FakeClient:
    def __init__(self) -> None:
        self.threads = FakeThreads()


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
    provider = MemoryProvider(settings, sdk_client=FakeClient())
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
    await sem.aput("user/pref", "user likes dark mode")
    got = await sem.aget("user/pref")
    assert got == "user likes dark mode", f"semantic get got {got!r}"
    hits = await sem.asearch("dark mode", limit=5)
    assert any("dark mode" in (h.get("content") or "") for h in hits), "semantic FTS search missed"
    await sem.adelete("user/pref")
    assert await sem.aget("user/pref") is None
    print("  ok  semantic put/get/search/delete (FTS)")


async def check_episodic(p: MemoryProvider) -> None:
    ep = p.episodic
    ep.set_user("alice")
    hits = await ep.search("billing", limit=5, user_id="alice")
    assert hits and hits[0]["thread_id"] == "abc123", "episodic fake search failed"
    hist = await ep.get_history("abc123")
    assert hist and hist[0]["content"] == "hello", "episodic fake history failed"
    tool = ep.as_tool()
    desc = await tool.ainvoke({"query": "billing", "limit": 3})
    assert "abc123" in desc, f"episodic tool returned {desc!r}"
    print("  ok  episodic fake search + tool")


async def check_procedural(p: MemoryProvider) -> None:
    proc = p.procedural
    await proc.apromote("deploy", "# Deploy\n\ndeploy with docker.\n")
    names = await proc.alist()
    assert "deploy" in names, f"procedural list={names}"
    content = await proc.aload("deploy")
    assert content and "docker" in content
    hits = await proc.asearch("docker", limit=5)
    assert hits and hits[0]["name"] == "deploy"
    print("  ok  procedural promote/list/load/search")


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