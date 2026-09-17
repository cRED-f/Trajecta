"""Value helpers for connector verification: redaction, JSON safety, path lookups and recipes."""

from __future__ import annotations

import json
import re

from collections.abc import Mapping, Sequence

from typing import Any

from langchain_core.messages import ToolMessage


_SECRET_KEY = re.compile(
    r"(password|passwd|secret|token|authorization|api[_-]?key|cookie|credential)",
    re.IGNORECASE,
)


def redact(value: Any) -> Any:
    """Never persist obvious credentials inside action receipts."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SECRET_KEY.search(key_text):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = redact(item)
        return result

    if isinstance(value, list):
        return [redact(item) for item in value]

    if isinstance(value, tuple):
        return [redact(item) for item in value]

    return value


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, ToolMessage):
        return {
            "status": getattr(value, "status", None),
            "content": json_safe(value.content),
            "artifact": json_safe(getattr(value, "artifact", None)),
        }

    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [json_safe(item) for item in value]

    if hasattr(value, "model_dump"):
        try:
            return json_safe(value.model_dump(mode="json"))
        except Exception:
            pass

    return str(value)


def maybe_parse_json(value: Any) -> Any:
    """Convert MCP JSON-as-text/content blocks into structures when possible."""

    safe = json_safe(value)

    if isinstance(safe, str):
        text = safe.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        return safe

    if isinstance(safe, dict):
        content = safe.get("content")

        if isinstance(content, str):
            parsed = maybe_parse_json(content)
            if not isinstance(parsed, str):
                return parsed

        # MCP content blocks.
        if isinstance(content, list):
            texts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in {"text", "output_text"}:
                    text = block.get("text")
                    if isinstance(text, str):
                        texts.append(text)
            if texts:
                combined = "".join(texts)
                parsed = maybe_parse_json(combined)
                if not isinstance(parsed, str):
                    return parsed

        return {key: maybe_parse_json(item) for key, item in safe.items()}

    return safe


def get_path(value: Any, path: str, *, default: Any = None) -> Any:
    """Simple safe dotted-path lookup.

    Supports ``id``, ``message.id`` and ``data.items.0.id``.
    """

    if not path:
        return value

    current = value

    for segment in path.split("."):
        if isinstance(current, Mapping):
            if segment not in current:
                return default
            current = current[segment]
            continue

        if isinstance(current, list):
            try:
                index = int(segment)
            except ValueError:
                return default
            if index < 0 or index >= len(current):
                return default
            current = current[index]
            continue

        return default

    return current


def resolve_expression(
    expression: Any,
    *,
    action_args: dict[str, Any],
    action_result: Any,
    receipt: Any,
    readback_result: Any = None,
) -> Any:
    """Resolve values used by connector verification recipes."""

    if not isinstance(expression, str):
        return expression

    if not expression.startswith("$"):
        return expression

    if expression.startswith("$action.args."):
        return get_path(action_args, expression[len("$action.args.") :])

    if expression == "$action.args":
        return action_args

    if expression.startswith("$action.result."):
        return get_path(action_result, expression[len("$action.result.") :])

    if expression == "$action.result":
        return action_result

    if expression.startswith("$receipt."):
        return get_path(json_safe(receipt), expression[len("$receipt.") :])

    if expression.startswith("$readback."):
        return get_path(readback_result, expression[len("$readback.") :])

    if expression == "$readback":
        return readback_result

    return expression