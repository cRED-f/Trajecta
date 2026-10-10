"""Regression checks for Stop/restart. Run without optional agent dependencies."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

CHAT_DIR = Path(__file__).resolve().parents[2] / "src" / "chat"


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, CHAT_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runs = load_module("isolated_chat_runs_v27", "runs.py")
streaming = load_module("isolated_chat_streaming_v27", "streaming.py")


def test_stop_waits_until_original_owner_has_released_lock():
    async def scenario():
        reg = runs.ChatRunRegistry()
        signal = await reg.register("chat", "r1")
        started = asyncio.Event()

        async def producer():
            assert await reg.bind_owner("chat", "r1")
            started.set()
            try:
                await signal.wait()
                await asyncio.sleep(0.015)  # model/stream cleanup
            finally:
                await reg.unregister("chat", "r1")

        task = asyncio.create_task(producer())
        await started.wait()
        stop = asyncio.create_task(reg.cancel_and_wait("chat", grace_seconds=0.5))
        await asyncio.sleep(0)
        assert not stop.done()
        assert await stop is True
        await task
        assert not await reg.is_active("chat")
        await reg.register("chat", "r2")
        await reg.unregister("chat", "r2")

    asyncio.run(scenario())


def test_stop_before_sse_start_prevents_late_stream():
    async def scenario():
        reg = runs.ChatRunRegistry()
        event = await reg.register("chat", "first")
        assert await reg.cancel_and_wait("chat") is True
        assert event.is_set()
        assert not await reg.bind_owner("chat", "first")
        await reg.register("chat", "second")
        assert await reg.is_active("chat")
        await reg.unregister("chat", "second")

    asyncio.run(scenario())


def test_stop_during_preflight_cancels_reservation():
    async def scenario():
        reg = runs.ChatRunRegistry()
        await reg.reserve("chat")
        stop = asyncio.create_task(reg.cancel_and_wait("chat", grace_seconds=0.5))
        await asyncio.sleep(0)
        with pytest.raises(runs.RunAlreadyActive, match="cancelled"):
            await reg.register("chat", "old")
        assert await stop is True
        assert not await reg.is_active("chat")
        await reg.reserve("chat")
        await reg.register("chat", "new")
        await reg.unregister("chat", "new")

    asyncio.run(scenario())


def test_owner_failure_cannot_leave_run_stuck():
    async def scenario():
        reg = runs.ChatRunRegistry()
        await reg.register("chat", "old")

        async def broken():
            assert await reg.bind_owner("chat", "old")
            raise RuntimeError("unexpected worker crash")

        task = asyncio.create_task(broken())
        with pytest.raises(RuntimeError):
            await task
        # add_done_callback schedules unregister on unexpected task exits.
        await asyncio.sleep(0.01)
        assert not await reg.is_active("chat")

    asyncio.run(scenario())


class FakeEvent(BaseModel):
    type: str
    conversation_id: str
    run_id: str


def test_sse_consumer_disconnection_closes_source():
    async def scenario():
        source_closed = asyncio.Event()

        async def source():
            try:
                yield FakeEvent(type="run.started", conversation_id="chat", run_id="old")
                await asyncio.Event().wait()
            finally:
                source_closed.set()

        stream = streaming.sse_stream(source(), heartbeat_seconds=0.01)
        assert "run.started" in await anext(stream)
        await stream.aclose()
        assert source_closed.is_set()

    asyncio.run(scenario())


def test_force_cancels_a_nonresponsive_stream_owner():
    async def scenario():
        reg = runs.ChatRunRegistry()
        await reg.register("chat", "old")
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def stuck():
            assert await reg.bind_owner("chat", "old")
            entered.set()
            try:
                await asyncio.Event().wait()  # ignores graceful stop event
            finally:
                cancelled.set()
                await reg.unregister("chat", "old")

        task = asyncio.create_task(stuck())
        await entered.wait()
        assert await reg.cancel_and_wait("chat", grace_seconds=0.01, force_seconds=0.5)
        assert cancelled.is_set()
        await asyncio.gather(task, return_exceptions=True)
        assert not await reg.is_active("chat")

    asyncio.run(scenario())
