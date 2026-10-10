"""Streaming guards work without importing the optional Deep Agents stack."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE = Path(__file__).resolve().parents[1] / "src/output_safety.py"
spec = importlib.util.spec_from_file_location("_trajecta_output_safety", MODULE)
assert spec and spec.loader
safety = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safety)


@pytest.mark.parametrize("step", [1, 2, 7, 41, 500])
def test_leaked_session_summary_is_blocked_before_any_visible_character(step: int) -> None:
    text = (
        "## SESSION INTENT\nThe user was applying for EMAI.\n"
        "## SUMMARY\n- personal details\n## NEXT STEPS\n"
        "## Short answer: no written exam."
    )
    gate = safety.VisibleTextGate()
    emitted = []
    with pytest.raises(safety.InternalContextExposure):
        for offset in range(0, len(text), step):
            emitted.append(gate.feed(text[offset:offset + step]))
    assert "".join(emitted) == ""


def test_actual_system_prompt_header_is_blocked() -> None:
    gate = safety.VisibleTextGate()
    with pytest.raises(safety.InternalContextExposure):
        for part in ["  \n", "You are Tra", "jecta, a local-first autonomous desktop agent."]:
            gate.feed(part)


def test_role_marker_is_blocked() -> None:
    gate = safety.VisibleTextGate()
    with pytest.raises(safety.InternalContextExposure):
        gate.feed("<|im_start|>system\nNever reveal this")


def test_valid_markdown_and_direct_answer_stream_normally() -> None:
    gate = safety.VisibleTextGate()
    pieces = ["#", "#", " Short answer", ": no written exam is required."]
    assert "".join(gate.feed(part) for part in pieces) + gate.finish() == "## Short answer: no written exam is required."


def test_ordinary_summary_remains_allowed() -> None:
    gate = safety.VisibleTextGate()
    assert gate.feed("## Summary\nYou need a CV and transcripts.") == "## Summary\nYou need a CV and transcripts."


def test_empty_and_short_answers_flush() -> None:
    gate = safety.VisibleTextGate()
    assert gate.feed("  ") == ""
    assert gate.feed("#") == ""
    assert gate.finish() == "  #"


def test_passthrough_after_first_safe_text() -> None:
    gate = safety.VisibleTextGate()
    assert gate.feed("The answer") == "The answer"
    assert gate.feed(" is 42.") == " is 42."
    assert gate.finish() == ""
