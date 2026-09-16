from __future__ import annotations

import asyncio
import tempfile

from pathlib import Path

from server.src.chat.models import (
    ConversationCreate,
)

from server.src.chat.service import (
    build_chat_service,
)

from server.src.config import Settings

from server.src.memory.provider import (
    MemoryProvider,
)


async def _run() -> None:
    root = Path(
        tempfile.mkdtemp()
    )

    settings = Settings.model_validate(
        {
            "chat": {
                "uploads_path": str(
                    root / "uploads"
                ),
                "default_model": (
                    "openai/gpt-4o-mini"
                ),
            },

            "memory": {
                "db_path": str(
                    root / "trajecta.db"
                ),

                "langgraph_db_path": str(
                    root / "langgraph.db"
                ),

                "vector_store": {
                    "enabled": False,
                    "path": str(
                        root / "qdrant"
                    ),
                },
            },

            "tools": {
                "mcp_servers": {},
            },
        }
    )

    memory = MemoryProvider(
        settings
    )

    await memory.open()

    try:
        chat = build_chat_service(
            settings,
            memory,
        )

        conversation = (
            await chat.create_conversation(
                ConversationCreate(
                    title="Verification chat",
                    model="openai/gpt-4o-mini",
                )
            )
        )

        assert conversation.id
        assert conversation.thread_id

        detail = await chat.get_conversation(
            conversation.id
        )

        assert detail.id == conversation.id
        assert detail.messages == []

        print(
            "  ok  create/read conversation"
        )

        conversations = (
            await chat.list_conversations()
        )

        assert any(
            item.id == conversation.id
            for item in conversations
        )

        print(
            "  ok  list conversations"
        )

    finally:
        await memory.close()

    print(
        "  ok  chat persistence verified"
    )


def main() -> None:
    asyncio.run(
        _run()
    )


if __name__ == "__main__":
    main()
