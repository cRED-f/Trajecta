"""Local attachment RAG for large documents.

The canonical attachment stays under /uploads/. Extracted text is chunked into:
- SQLite rows for durable metadata
- FTS5 for lexical retrieval
- embedded Qdrant for semantic retrieval

The Deep Agent gets a conversation-scoped search tool, so document chunks are
retrieved on demand instead of being stuffed into every model context.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from server.src.chat.models import Attachment
from server.src.chat.repository import ChatRepository
from server.src.config import ChatRagConfig
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.memory.storage.vector import VectorStore


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _rowid(chunk_id: str) -> int:
    digest = hashlib.sha256(chunk_id.encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\w-]+", query.lower(), flags=re.UNICODE)
    if not tokens:
        return '""'
    # FTS5 OR keeps natural-language queries useful without exposing raw syntax.
    return " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens[:24])


def _chunk_text(text: str, *, size: int, overlap: int) -> list[tuple[int, int, str]]:
    if not text.strip():
        return []
    size = max(500, int(size))
    overlap = max(0, min(int(overlap), size // 2))
    chunks: list[tuple[int, int, str]] = []
    start = 0
    length = len(text)

    while start < length:
        end = min(length, start + size)
        # Prefer a paragraph/sentence boundary near the end of the window.
        if end < length:
            floor = min(length, start + max(500, size // 2))
            boundary = max(
                text.rfind("\n\n", floor, end),
                text.rfind(". ", floor, end),
                text.rfind("\n", floor, end),
            )
            if boundary > start:
                end = boundary + (2 if text[boundary:boundary + 2] == ". " else 0)

        content = text[start:end].strip()
        if content:
            chunks.append((start, end, content))
        if end >= length:
            break
        start = max(start + 1, end - overlap)

    return chunks


class AttachmentRAGIndex:
    def __init__(
        self,
        repository: ChatRepository,
        db: SQLiteDatabase,
        vector: VectorStore,
        *,
        uploads_root: str,
        config: ChatRagConfig,
    ) -> None:
        self._repository = repository
        self._db = db
        self._vector = vector
        self._root = Path(uploads_root).resolve()
        self._config = config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    async def index_attachment(self, attachment: Attachment) -> Attachment:
        """Index extracted text when it is large enough to benefit from RAG."""
        if not self.enabled or not attachment.extracted_virtual_path:
            return attachment

        extracted = self._physical_path(attachment.extracted_virtual_path)
        if not extracted.exists():
            return attachment

        text = extracted.read_text(encoding="utf-8", errors="replace")
        if len(text) < self._config.auto_index_min_chars:
            return attachment

        await self.delete_attachment(attachment.id)
        chunks = _chunk_text(
            text,
            size=self._config.chunk_size_chars,
            overlap=self._config.chunk_overlap_chars,
        )

        for index, (start, end, content) in enumerate(chunks):
            chunk_id = hashlib.sha256(
                f"attachment:{attachment.id}:{index}:{content}".encode()
            ).hexdigest()
            await self._db.execute(
                """
                INSERT INTO attachment_chunks(
                    id, attachment_id, conversation_id, chunk_index,
                    start_char, end_char, content, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    attachment.id,
                    attachment.conversation_id,
                    index,
                    start,
                    end,
                    content,
                    _now(),
                ),
            )
            await self._db.execute(
                """
                INSERT INTO attachment_chunks_fts(
                    rowid, attachment_id, conversation_id,
                    chunk_id, chunk_index, content
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _rowid(chunk_id),
                    attachment.id,
                    attachment.conversation_id,
                    chunk_id,
                    index,
                    content,
                ),
            )
            self._vector.upsert(
                "attachment_chunks",
                chunk_id,
                content,
                payload={
                    "attachment_id": attachment.id,
                    "conversation_id": attachment.conversation_id,
                    "chunk_index": index,
                    "filename": attachment.filename,
                    "start_char": start,
                    "end_char": end,
                },
            )

        metadata = dict(attachment.metadata)
        metadata.update(
            {
                "rag_indexed": True,
                "rag_chunk_count": len(chunks),
                "rag_index_version": 1,
            }
        )
        await self._repository.update_attachment_metadata(attachment.id, metadata)
        refreshed = await self._repository.get_attachment(attachment.id)
        assert refreshed is not None
        return refreshed

    async def delete_attachment(self, attachment_id: str) -> None:
        rows = await self._db.fetch(
            "SELECT id FROM attachment_chunks WHERE attachment_id = ?",
            (attachment_id,),
        )
        for row in rows:
            chunk_id = row["id"]
            await self._db.execute(
                "DELETE FROM attachment_chunks_fts WHERE rowid = ?",
                (_rowid(chunk_id),),
            )
            self._vector.delete("attachment_chunks", chunk_id)
        await self._db.execute(
            "DELETE FROM attachment_chunks WHERE attachment_id = ?",
            (attachment_id,),
        )

    async def search(
        self,
        *,
        conversation_id: str,
        query: str,
        attachment_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        limit = max(1, int(limit or self._config.top_k))
        allowed = set(attachment_ids or [])
        seen: set[str] = set()
        results: list[dict[str, Any]] = []

        fts = _fts_query(query)
        if fts != '""':
            sql = """
                SELECT chunk_id, attachment_id, chunk_index, content,
                       snippet(attachment_chunks_fts, 4, '[', ']', '…', 36) AS snippet
                FROM attachment_chunks_fts
                WHERE attachment_chunks_fts MATCH ? AND conversation_id = ?
            """
            params: list[Any] = [fts, conversation_id]
            if allowed:
                placeholders = ",".join("?" for _ in allowed)
                sql += f" AND attachment_id IN ({placeholders})"
                params.extend(sorted(allowed))
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)
            try:
                lexical = await self._db.fetch(sql, tuple(params))
            except Exception:
                lexical = []

            for row in lexical:
                chunk_id = row["chunk_id"]
                seen.add(chunk_id)
                results.append(
                    {
                        "source": "fts",
                        "chunk_id": chunk_id,
                        "attachment_id": row["attachment_id"],
                        "chunk_index": row["chunk_index"],
                        "content": row["content"],
                        "snippet": row["snippet"],
                    }
                )

        # Pull extra vector candidates because filtering is application-scoped.
        vector_hits = self._vector.search(
            "attachment_chunks",
            query,
            limit=max(limit * 5, 25),
        )
        for hit in vector_hits:
            payload = hit.get("payload") or {}
            if payload.get("conversation_id") != conversation_id:
                continue
            attachment_id = payload.get("attachment_id")
            if allowed and attachment_id not in allowed:
                continue
            chunk_id = hit.get("doc_id")
            if not chunk_id or chunk_id in seen:
                continue
            seen.add(chunk_id)
            results.append(
                {
                    "source": "vector",
                    "chunk_id": chunk_id,
                    "attachment_id": attachment_id,
                    "chunk_index": payload.get("chunk_index"),
                    "filename": payload.get("filename"),
                    "content": hit.get("text", ""),
                    "score": hit.get("score"),
                }
            )
            if len(results) >= limit:
                break

        return results[:limit]

    def as_tool(self, *, conversation_id: str) -> BaseTool:
        index = self

        @tool("search_attachments")
        async def search_attachments(
            query: str,
            attachment_ids: list[str] | None = None,
            limit: int = 6,
        ) -> str:
            """Search indexed uploaded documents for relevant passages.

            Use this before reading an entire large PDF, DOCX, text, or code file.
            attachment_ids can restrict search to specific uploaded files.
            """
            hits = await index.search(
                conversation_id=conversation_id,
                query=query,
                attachment_ids=attachment_ids,
                limit=min(max(1, limit), index._config.top_k),
            )
            if not hits:
                return "No indexed attachment passages matched the query."

            rendered: list[str] = []
            used = 0
            for hit in hits:
                content = str(hit.get("content") or "")
                header = (
                    f"[attachment={hit.get('attachment_id')} "
                    f"chunk={hit.get('chunk_index')} source={hit.get('source')}]"
                )
                block = f"{header}\n{content}\n"
                if used + len(block) > index._config.max_tool_chars:
                    break
                rendered.append(block)
                used += len(block)
            return "\n---\n".join(rendered)

        return search_attachments

    def _physical_path(self, virtual_path: str) -> Path:
        prefix = "/uploads/"
        if not virtual_path.startswith(prefix):
            raise ValueError("Attachment path is outside /uploads/")
        relative = virtual_path[len(prefix):]
        path = (self._root / relative).resolve()
        if self._root not in path.parents and path != self._root:
            raise ValueError("Attachment path escapes uploads root")
        return path
