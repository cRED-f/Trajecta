from __future__ import annotations

import asyncio
import json
from contextlib import aclosing

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

            if isinstance(
                value,
                BaseException,
            ):
                raise value

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

    finally:
        if not producer.done():
            producer.cancel()

        try:
            await producer

        except asyncio.CancelledError:
            pass
