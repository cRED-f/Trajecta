"""Context assembly for an agent run, backed by unified memory retrieval.

No autonomous evaluation or skill promotion occurs during context assembly.
"""
from __future__ import annotations

from typing import Any

from server.src.memory.retrieval import UnifiedMemoryRetriever


class ContextManager:
    def __init__(self, memory: Any) -> None:
        self.retriever = UnifiedMemoryRetriever(memory)

    async def retrieve(self, task: str, *, workspace_path: str | None = None,
                       user_id: str = "local", limit: int = 8) -> list[dict[str, Any]]:
        return await self.retriever.search(
            task, workspace_path=workspace_path, user_id=user_id, limit=limit,
        )

    async def build(self, task: str, *, workspace_path: str | None = None,
                    user_id: str = "local", thread_id: str | None = None,
                    max_chars: int = 2500, limit: int = 8) -> str:
        return await self.retriever.context(
            task, workspace_path=workspace_path, user_id=user_id,
            thread_id=thread_id, max_chars=max_chars, limit=limit,
        )
