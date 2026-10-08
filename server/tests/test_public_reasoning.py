"""Test reasoning *transport*, not private or inferred reasoning."""

from langchain_core.messages import AIMessageChunk

from server.src.chat.runtime import _reasoning_from_chunk


def test_reasoning_content_block_is_separate_from_answer() -> None:
    chunk = AIMessageChunk(
        content=[
            {"type": "reasoning", "summary": [{"text": "Checking facts."}]},
            {"type": "text", "text": "The answer."},
        ]
    )
    assert _reasoning_from_chunk(chunk) == "Checking facts."


def test_plain_answer_has_no_reasoning() -> None:
    assert _reasoning_from_chunk(AIMessageChunk(content="The answer.")) == ""


def test_provider_specific_reasoning_is_used_only_if_preserved() -> None:
    chunk = AIMessageChunk(
        content="",
        additional_kwargs={"reasoning_content": "Provider-supplied summary."},
    )
    assert _reasoning_from_chunk(chunk) == "Provider-supplied summary."--- a/apps/desktop/src/types/chat.ts
