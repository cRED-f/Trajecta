"""Episodic memory — selected useful experiences from previous tasks.

Threads on the LangGraph checkpointer are conversations. Episodic memory makes
them searchable and selectable: a running `langgraph dev` server exposes
`threads.search` so past conversations can be found by metadata (user_id) and
fetched. The langgraph-sdk client is injectable (fakeable) so the verify check
runs without a live server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.src.memory.provider import MemoryProvider


class EpisodicMemory:
    """Selects past threads by metadata and returns their message history.

    Requires a LangGraph server (`langgraph dev`) at `langgraph_server_url` for
    real retrieval; the SDK client is lazily created and injectable for tests.
    """

    def __init__(self, provider: "MemoryProvider", *, sdk_client: Any | None = None) -> None:
        self._provider = provider
        self._sdk_client = sdk_client  # injectable fake
        self._user_id: str | None = None

    # -- configuration -----------------------------------------------------

    def set_user(self, user_id: str) -> None:
        """Scope episodic searches to a user (threads carry user_id metadata)."""
        self._user_id = user_id

    def _client(self) -> Any:
        if self._sdk_client is not None:
            return self._sdk_client
        cfg = self._provider._settings.memory
        from langgraph_sdk import get_client

        self._sdk_client = get_client(url=cfg.langgraph_server_url)
        return self._sdk_client

    # -- retrieval ---------------------------------------------------------

    async def search(
        self,
        query: str,
        limit: int = 10,
        *,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search previous threads using their actual message content.

        Temporary implementation.

        Future architecture:
            trajectory
                -> episode extraction
                -> summary
                -> FTS + embeddings
                -> hybrid retrieval
        """

        uid = user_id or self._user_id
        limit = max(1, int(limit))

        # Pull a broader candidate pool before ranking.
        candidate_limit = max(
            limit * 5,
            25,
        )

        try:
            threads = await self._client().threads.search(
                metadata=(
                    {"user_id": uid}
                    if uid
                    else None
                ),
                limit=candidate_limit,
            )

        except Exception:
            return []

        query_tokens = {
            token.lower()
            for token in query.split()
            if token.strip()
        }

        ranked: list[dict[str, Any]] = []

        for thread in threads:
            thread_id = thread.get("thread_id")

            if not thread_id:
                continue

            metadata = (
                thread.get("metadata")
                or {}
            )

            title = str(
                metadata.get("title")
                or ""
            )

            history = await self.get_history(
                thread_id,
                limit=50,
            )

            history_text = " ".join(
                str(message.get("content") or "")
                for message in history
            )

            searchable = (
                f"{title} {history_text}"
            ).lower()

            if query_tokens:
                matched = sum(
                    1
                    for token in query_tokens
                    if token in searchable
                )

                score = (
                    matched
                    / len(query_tokens)
                )
            else:
                score = 1.0

            if score <= 0:
                continue

            ranked.append(
                {
                    "thread_id": thread_id,
                    "user_id": metadata.get(
                        "user_id"
                    ),
                    "created_at": thread.get(
                        "created_at"
                    ),
                    "title": (
                        title or None
                    ),
                    "score": score,
                }
            )

        ranked.sort(
            key=lambda item: item["score"],
            reverse=True,
        )

        return ranked[:limit]

    async def get_history(self, thread_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Expand one thread into its message history for the agent."""
        try:
            messages = await self._client().threads.get_history(thread_id, limit=limit)
        except Exception:  # pragma: no cover — no live server
            return []
        return [
            {
                "type": m.get("type"),
                "role": m.get("role"),
                "content": m.get("content"),
            }
            for m in messages
        ]

    def as_tool(self, name: str = "search_past_conversations") -> Any:
        """Wrap `search` as a LangChain tool for the agent runtime (added at agent phase)."""
        from langchain_core.tools import tool

        @tool
        async def search_past_conversations(query: str, limit: int = 5) -> str:
            """Find past conversations matching a description; returns thread ids + summaries."""
            hits = await self.search(query, limit=limit)
            return "\n\n".join(
                f"[{h['thread_id']}] user={h.get('user_id')} at={h.get('created_at')} title={h.get('title')}"
                for h in hits
            )

        return search_past_conversations
