"""Bounded, process-local content guardrail decision cache.

Neither raw input nor clean input text is stored. An opaque keyed hash is
used for lookup; entries hold only modified output and finding metadata.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.src.guardrails.content import GuardedText, GuardrailFinding


@dataclass(frozen=True)
class _Entry:
    expires_at: float
    replacement: str | None
    findings: tuple["GuardrailFinding", ...]
    size_bytes: int


class GuardrailDecisionCache:
    """Small TTL/LRU cache; all input text is keyed, never retained.

    Cached results are reused only for identical stage, input bytes,
    policy snapshot and effective model. Caller must load current settings
    before computing a cache key.
    """

    def __init__(
        self,
        *,
        max_entries: int = 1024,
        max_bytes: int = 8 * 1024 * 1024,
        ttl_seconds: float = 300.0,
        max_input_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self._entries: OrderedDict[bytes, _Entry] = OrderedDict()
        self._key = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._size_bytes = 0
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._ttl_seconds = ttl_seconds
        self._max_input_bytes = max_input_bytes
        self.hits = 0
        self.misses = 0

    def key(
        self,
        *,
        stage: str,
        policy_json: str,
        model_name: str,
        text: str,
    ) -> bytes | None:
        content = text.encode("utf-8")
        if len(content) > self._max_input_bytes:
            return None
        digest = hmac.new(self._key, digestmod=hashlib.sha256)
        for part in (stage.encode(), policy_json.encode(), model_name.encode(), content):
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
        return digest.digest()

    def get(self, key: bytes, original: str) -> "GuardedText | None":
        from server.src.guardrails.content import GuardedText

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            if entry.expires_at <= time.monotonic():
                self._entries.pop(key)
                self._size_bytes -= entry.size_bytes
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            # Never return a mutable finding object shared with the cache.
            return GuardedText(
                text=original if entry.replacement is None else entry.replacement,
                findings=[finding.model_copy(deep=True) for finding in entry.findings],
            )

    def put(self, key: bytes, original: str, result: "GuardedText") -> None:
        replacement = result.text if result.text != original else None
        findings = tuple(f.model_copy(deep=True) for f in result.findings)
        serialized = json.dumps([f.model_dump(mode="json") for f in findings])
        size = 64 + len(serialized.encode("utf-8"))
        if replacement is not None:
            size += len(replacement.encode("utf-8"))
        if size > self._max_bytes:
            return
        entry = _Entry(time.monotonic() + self._ttl_seconds, replacement, findings, size)
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._size_bytes -= previous.size_bytes
            self._entries[key] = entry
            self._size_bytes += size
            while len(self._entries) > self._max_entries or self._size_bytes > self._max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._size_bytes -= evicted.size_bytes

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._size_bytes = 0
