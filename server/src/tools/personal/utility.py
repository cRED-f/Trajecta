"""Deterministic utility tools that do not require an LLM or external API."""

from __future__ import annotations

import ast
import base64
import hashlib
import math
import operator
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo


_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_ALLOWED_FUNCS = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "min": min,
    "max": max,
}
_ALLOWED_CONSTS = {"pi": math.pi, "e": math.e}


def _eval(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in _ALLOWED_CONSTS:
        return _ALLOWED_CONSTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_FUNCS:
        return _ALLOWED_FUNCS[node.func.id](*[_eval(arg) for arg in node.args])
    raise ValueError("Unsupported expression")


class UtilityTools:
    @staticmethod
    def calculate(expression: str) -> dict[str, Any]:
        tree = ast.parse(expression, mode="eval")
        return {"expression": expression, "result": _eval(tree)}

    @staticmethod
    def uuid_generate() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def hash_text(text: str, algorithm: str = "sha256") -> str:
        try:
            digest = hashlib.new(algorithm)
        except ValueError as exc:
            raise ValueError(f"Unsupported hash algorithm: {algorithm}") from exc
        digest.update(text.encode("utf-8"))
        return digest.hexdigest()

    @staticmethod
    def base64_codec(value: str, operation: str = "encode") -> str:
        if operation == "encode":
            return base64.b64encode(value.encode("utf-8")).decode("ascii")
        if operation == "decode":
            return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")
        raise ValueError("operation must be encode or decode")

    @staticmethod
    def url_codec(value: str, operation: str = "encode") -> str:
        if operation == "encode":
            return quote(value)
        if operation == "decode":
            return unquote(value)
        raise ValueError("operation must be encode or decode")

    @staticmethod
    def timezone_convert(timestamp: str, from_timezone: str, to_timezone: str) -> str:
        source = ZoneInfo(from_timezone)
        target = ZoneInfo(to_timezone)
        value = datetime.fromisoformat(timestamp)
        if value.tzinfo is None:
            value = value.replace(tzinfo=source)
        else:
            value = value.astimezone(source)
        return value.astimezone(target).isoformat()

    @staticmethod
    def date_add(timestamp: str, *, days: int = 0, hours: int = 0, minutes: int = 0) -> str:
        value = datetime.fromisoformat(timestamp)
        return (value + timedelta(days=days, hours=hours, minutes=minutes)).isoformat()

    @staticmethod
    def now(timezone: str = "UTC") -> str:
        return datetime.now(ZoneInfo(timezone)).isoformat()

    @staticmethod
    def unit_convert(value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
        f = from_unit.strip().lower()
        t = to_unit.strip().lower()

        groups = [
            {"m": 1.0, "meter": 1.0, "meters": 1.0, "km": 1000.0, "cm": 0.01, "mm": 0.001, "mi": 1609.344, "ft": 0.3048, "in": 0.0254},
            {"kg": 1.0, "g": 0.001, "mg": 0.000001, "lb": 0.45359237, "oz": 0.028349523125},
            {"b": 1.0, "kb": 1000.0, "mb": 1_000_000.0, "gb": 1_000_000_000.0, "kib": 1024.0, "mib": 1024.0**2, "gib": 1024.0**3},
            {"s": 1.0, "sec": 1.0, "min": 60.0, "h": 3600.0, "hr": 3600.0, "day": 86400.0},
        ]
        for group in groups:
            if f in group and t in group:
                converted = float(value) * group[f] / group[t]
                return {"value": value, "from": from_unit, "to": to_unit, "result": converted}

        if f in {"c", "celsius", "f", "fahrenheit", "k", "kelvin"} and t in {
            "c", "celsius", "f", "fahrenheit", "k", "kelvin"
        }:
            if f in {"c", "celsius"}:
                c = float(value)
            elif f in {"f", "fahrenheit"}:
                c = (float(value) - 32.0) * 5.0 / 9.0
            else:
                c = float(value) - 273.15
            if t in {"c", "celsius"}:
                result = c
            elif t in {"f", "fahrenheit"}:
                result = c * 9.0 / 5.0 + 32.0
            else:
                result = c + 273.15
            return {"value": value, "from": from_unit, "to": to_unit, "result": result}

        raise ValueError(f"Unsupported conversion: {from_unit} -> {to_unit}")

    @staticmethod
    def text_diff(before: str, after: str) -> str:
        import difflib

        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="before",
                tofile="after",
            )
        )

    @staticmethod
    def regex_find(pattern: str, text: str, *, flags: str = "") -> list[dict[str, Any]]:
        value = 0
        if "i" in flags:
            value |= re.IGNORECASE
        if "m" in flags:
            value |= re.MULTILINE
        compiled = re.compile(pattern, value)
        return [
            {"match": match.group(0), "groups": list(match.groups()), "start": match.start(), "end": match.end()}
            for match in list(compiled.finditer(text))[:200]
        ]
