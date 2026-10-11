"""Transport disconnect regressions that run without optional Deep Agents."""

from __future__ import annotations

import asyncio
import ast
import importlib.util
import json
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel

from server.src.llm_auth_errors import (
    describe_gateway_failure,
    is_transient_gateway_disconnect,
)

ROOT = Path(__file__).resolve().parents[3]
CHAT = ROOT / "server" / "src" / "chat"


def _load_streaming():
    name = "isolated_streaming_disconnect"
    spec = importlib.util.spec_from_file_location(name, CHAT / "streaming.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Event(BaseModel):
    type: str
    conversation_id: str
    run_id: str


def test_protocol_disconnect_has_actionable_nonfatal_description() -> None:
    request = httpx.Request("POST", "http://localhost:8080/v1/chat/completions")
    exc = httpx.RemoteProtocolError(
        "Server disconnected without sending a response.", request=request,
    )
    assert is_transient_gateway_disconnect(exc)
    message = describe_gateway_failure(exc)
    assert "Bifrost" in message
    assert "retry" in message.lower()
    assert "Server disconnected without sending" not in message


def test_wrapped_openai_connection_errors_are_classified() -> None:
    request = httpx.Request("POST", "http://localhost:8080/v1/chat/completions")
    try:
        raise httpx.RemoteProtocolError("transient", request=request)
    except httpx.RemoteProtocolError as cause:
        try:
            raise RuntimeError("Upstream request failed") from cause
        except RuntimeError as exc:
            assert is_transient_gateway_disconnect(exc)
            assert "model's connection" in describe_gateway_failure(exc)


def test_bad_credentials_are_not_transport_failures() -> None:
    error = RuntimeError("Error code: 401 invalid virtual key")
    assert not is_transient_gateway_disconnect(error)
    assert "virtual key" in describe_gateway_failure(error)


def test_sse_producer_crash_emits_terminal_error_without_leaking_exception() -> None:
    async def scenario() -> list[str]:
        streaming = _load_streaming()

        async def broken():
            yield Event(type="run.started", conversation_id="c1", run_id="r1")
            raise RuntimeError("secret_token_do_not_expose")

        result = []
        async for frame in streaming.sse_stream(
            broken(), heartbeat_seconds=0.01, conversation_id="c1", run_id="r1",
        ):
            result.append(frame)
        return result

    frames = asyncio.run(scenario())
    assert len(frames) == 2
    assert "run.started" in frames[0]
    assert frames[1].startswith("event: run.error\n")
    obj = json.loads(frames[1].split("data: ", 1)[1])
    assert obj["conversation_id"] == "c1"
    assert obj["run_id"] == "r1"
    assert obj["data"]["code"] == "chat_stream_internal_error"
    assert obj["data"]["retryable"] is True
    assert "secret_token_do_not_expose" not in frames[1]


def test_sse_normal_completion_has_no_extra_error() -> None:
    async def scenario() -> list[str]:
        streaming = _load_streaming()

        async def completed():
            yield Event(type="run.started", conversation_id="c1", run_id="r1")
            yield Event(type="message.completed", conversation_id="c1", run_id="r1")

        return [frame async for frame in streaming.sse_stream(completed())]

    frames = asyncio.run(scenario())
    assert len(frames) == 2
    assert "run.error" not in "".join(frames)


def test_model_client_retries_are_bounded_and_no_graph_replay() -> None:
    """Static check: the client retries, not the completed agent/tool graph."""
    module = ast.parse((CHAT / "model.py").read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(module)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == "BifrostReasoningChatOpenAI"]
    assert len(calls) == 1
    retries = [k.value.value for k in calls[0].keywords
               if k.arg == "max_retries" and isinstance(k.value, ast.Constant)]
    assert retries == [2]


def test_cleanup_error_after_terminal_does_not_override_success() -> None:
    async def scenario() -> list[str]:
        streaming = _load_streaming()

        async def finished_then_cleanup_fails():
            yield Event(type="message.completed", conversation_id="c1", run_id="r1")
            raise RuntimeError("cleanup problem")

        return [frame async for frame in streaming.sse_stream(
            finished_then_cleanup_fails(), conversation_id="c1", run_id="r1",
        )]

    frames = asyncio.run(scenario())
    assert len(frames) == 1
    assert "message.completed" in frames[0]
