from __future__ import annotations

import asyncio
import json
import logging
from contextlib import aclosing

logger = logging.getLogger(__name__)

from collections.abc import (
    AsyncIterator,
)

from pydantic import BaseModel


def encode_sse(
    event: str,
    data: dict,
) -> str:
    payload = json.dumps(
        data,
        # ASCII escapes round-trip perfectly through JSON.parse() on the
        # frontend, while keeping the SSE transport safe on cp1252 Windows.
        ensure_ascii=True,
        default=str,
    )

    return (
        f"event: {event}\n"
        f"data: {payload}\n\n"
    )


async def sse_stream(
    source: AsyncIterator[BaseModel],
    *,
    heartbeat_seconds: float = 15.0,
    conversation_id: str = "",
    run_id: str = "",
) -> AsyncIterator[str]:
    """
    Keep the HTTP stream alive even while a long tool call
    is running and the agent emits no tokens.
    """

    queue: asyncio.Queue[
        BaseModel | BaseException | None
    ] = asyncio.Queue()

    async def produce() -> None:
        try:
            # Canceling the producer must close the nested source generator,
            # otherwise its run-registry finally block can be skipped.
            async with aclosing(source) as events:
                async for event in events:
                    await queue.put(event)

        except BaseException as exc:
            await queue.put(exc)

        finally:
            await queue.put(None)

    producer = asyncio.create_task(
        produce()
    )
    terminal_sent = False

    try:
        while True:
            try:
                value = await asyncio.wait_for(
                    queue.get(),
                    timeout=heartbeat_seconds,
                )

            except TimeoutError:
                # Valid SSE comment / heartbeat.
                yield ": ping\n\n"
                continue

            if value is None:
                break

            if isinstance(value, BaseException):
                if terminal_sent:
                    # An auxiliary cleanup hook can fail after completion;
                    # never replace the already-delivered terminal event.
                    logger.error(
                        "Chat SSE cleanup failed after terminal event",
                        exc_info=(type(value), value, value.__traceback__),
                    )
                    break
                # Cancellation/system-exit must propagate; an unexpected
                # application error can still be reported if FastAPI is alive.
                if not isinstance(value, Exception):
                    raise value
                logger.error(
                    "Chat SSE producer failed (conversation=%s run=%s)",
                    conversation_id, run_id,
                    exc_info=(type(value), value, value.__traceback__),
                )
                # Never dump arbitrary exceptions to the client: they can
                # contain paths, SQL parameters, or authentication material.
                yield encode_sse(
                    "run.error",
                    {
                        "conversation_id": conversation_id,
                        "run_id": run_id,
                        "data": {
                            "error": (
                                "Trajecta encountered an unexpected backend error "
                                "while streaming. Please retry; if it repeats, "
                                "inspect the FastAPI backend log."
                            ),
                            "code": "chat_stream_internal_error",
                            "retryable": True,
                        },
                    },
                )
                break

            payload = value.model_dump(
                mode="json"
            )

            event_name = str(
                payload.pop(
                    "type",
                    "message",
                )
            )

            yield encode_sse(
                event_name,
                payload,
            )
            if event_name in {"run.error", "run.cancelled", "run.interrupted", "message.completed"}:
                terminal_sent = True

    finally:
        if not producer.done():
            producer.cancel()

        try:
            await producer

        except asyncio.CancelledError:
            pass
