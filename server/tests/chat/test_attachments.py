from __future__ import annotations

from io import BytesIO
from typing import Any

import pytest
from starlette.datastructures import Headers, UploadFile

from server.src.chat.attachments import AttachmentService
from server.src.chat.rag import AttachmentRAGIndex
from server.src.chat.models import MessageRole
from server.src.chat.repository import ChatRepository
from server.src.config import ChatRagConfig
from server.src.memory.storage.sqlite import SQLiteDatabase


class FakeVector:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}

    def upsert(
        self,
        namespace: str,
        doc_id: str,
        text: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.rows[(namespace, doc_id)] = {
            "doc_id": doc_id,
            "text": text,
            "score": 1.0,
            "payload": payload or {},
        }

    def delete(self, namespace: str, doc_id: str) -> None:
        self.rows.pop((namespace, doc_id), None)

    def upsert_many(self, namespace: str, items: list[dict[str, Any]]) -> int:
        for item in items:
            payload = item.get("payload")
            self.upsert(
                namespace,
                str(item["doc_id"]),
                str(item.get("text") or ""),
                payload=payload if isinstance(payload, dict) else None,
            )
        return len(items)

    def search(self, namespace: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        tokens = {token.lower() for token in query.split()}
        scored: list[dict[str, Any]] = []
        for (ns, _), row in self.rows.items():
            if ns != namespace:
                continue
            text = row["text"].lower()
            if any(token in text for token in tokens):
                scored.append(row)
        return scored[:limit]


@pytest.mark.asyncio
async def test_large_text_upload_is_indexed_and_searchable(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()
    try:
        repo = ChatRepository(db)
        conversation = await repo.create_conversation(title=None, model="test/model")
        attachments = AttachmentService(
            repo,
            uploads_root=str(tmp_path / "uploads"),
            max_upload_mb=5,
            max_extracted_chars=100_000,
        )
        vector = FakeVector()
        rag = AttachmentRAGIndex(
            repo,
            db,
            vector,  # type: ignore[arg-type]
            uploads_root=str(tmp_path / "uploads"),
            config=ChatRagConfig(
                enabled=True,
                auto_index_min_chars=100,
                chunk_size_chars=600,
                chunk_overlap_chars=100,
                top_k=6,
            ),
        )

        text = (
            "Trajecta uses verified skill learning and durable branching. " * 80
            + "The unique retrieval marker is helios-memory-anchor."
        )
        upload = UploadFile(
            filename="research.txt",
            file=BytesIO(text.encode("utf-8")),
            headers=Headers({"content-type": "text/plain"}),
        )
        attachment = await attachments.save(
            conversation_id=conversation.id,
            upload=upload,
        )
        attachment = await rag.index_attachment(attachment)

        assert attachment.extracted_virtual_path is not None
        assert attachment.metadata["rag_indexed"] is True
        assert attachment.metadata["rag_chunk_count"] >= 2

        hits = await rag.search(
            conversation_id=conversation.id,
            query="helios memory anchor",
            attachment_ids=[attachment.id],
            limit=5,
        )
        assert hits
        assert any("helios-memory-anchor" in hit["content"] for hit in hits)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_attachment_can_be_reused_across_message_branches(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()
    try:
        repo = ChatRepository(db)
        conversation = await repo.create_conversation(title=None, model="test/model")
        branch = await repo.get_active_branch(conversation.id)
        assert branch is not None

        attachments = AttachmentService(
            repo,
            uploads_root=str(tmp_path / "uploads"),
            max_upload_mb=5,
            max_extracted_chars=10_000,
        )
        upload = UploadFile(
            filename="note.txt",
            file=BytesIO(b"hello"),
            headers=Headers({"content-type": "text/plain"}),
        )
        attachment = await attachments.save(
            conversation_id=conversation.id,
            upload=upload,
        )

        one = await repo.add_message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="one",
            branch_id=branch.id,
        )
        two = await repo.add_message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="two",
            branch_id=branch.id,
        )
        await repo.bind_attachments(attachment_ids=[attachment.id], message_id=one.id)
        await repo.bind_attachments(attachment_ids=[attachment.id], message_id=two.id)

        assert [a.id for a in await repo.get_message_attachments(one.id)] == [attachment.id]
        assert [a.id for a in await repo.get_message_attachments(two.id)] == [attachment.id]
    finally:
        await db.close()
