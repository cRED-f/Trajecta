"""Deterministic outcome verification.

The verifier looks at observable post-run state.

It does NOT inspect hidden model reasoning and does NOT trust the
assistant's final response.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from server.src.skills.evaluation.fixtures import (
    ReplayFixtureFile,
    ReplayFixtureManifest,
    ReplayFixtureStore,
)
from server.src.skills.representation.skill import (
    OutcomeAssertion,
    OutcomeAssertionType,
    SkillEvalCase,
)


class AssertionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    path: str | None = None
    required: bool
    weight: float
    passed: bool | None
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    strength: float = Field(default=0.0, ge=0.0)
    reason: str
    actual: Any = None
    expected: Any = None


class OutcomeVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool = False
    passed: bool = False
    decisive: bool = False
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    evaluated_assertions: int = 0
    required_failures: int = 0
    evidence_strength: float = 0.0
    assertions: list[AssertionResult] = Field(default_factory=list)


class OutcomeVerifier:
    """Verify state after an evaluation run.

    Strong deterministic evidence can decide the evaluation without an
    LLM judge.
    """

    _STRENGTH = {
        OutcomeAssertionType.FILE_EXISTS: 0.45,
        OutcomeAssertionType.FILE_NOT_EXISTS: 1.00,
        OutcomeAssertionType.FILE_SHA256: 1.00,
        OutcomeAssertionType.FILE_CHANGED_FROM_FIXTURE: 0.60,
        OutcomeAssertionType.FILE_TEXT_SIMILARITY: 1.00,
        OutcomeAssertionType.FILE_JSON_EQUALS: 1.00,
        OutcomeAssertionType.SQLITE_QUERY_EQUALS: 1.00,
        OutcomeAssertionType.SQLITE_ROW_COUNT: 0.80,
    }

    def __init__(self, fixtures: ReplayFixtureStore) -> None:
        self._fixtures = fixtures

    async def verify(
        self,
        *,
        case: SkillEvalCase,
        workspace_root: str | Path,
        uploads_root: str | Path,
    ) -> OutcomeVerificationResult:
        assertions = case.outcome_assertions

        if not assertions:
            return OutcomeVerificationResult(available=False)

        workspace = Path(workspace_root).resolve()
        uploads = Path(uploads_root).resolve()

        fixture = None
        fixture_id = case.metadata.get("replay_fixture_id")
        if fixture_id:
            fixture = await self._fixtures.get(str(fixture_id))

        results: list[AssertionResult] = []

        for assertion in assertions:
            try:
                result = await self._verify_one(
                    assertion=assertion,
                    fixture=fixture,
                    workspace=workspace,
                    uploads=uploads,
                )
            except Exception as exc:
                result = AssertionResult(
                    type=assertion.type.value,
                    path=assertion.path,
                    required=assertion.required,
                    weight=assertion.weight,
                    passed=None,
                    score=0.0,
                    strength=0.0,
                    reason=(
                        "verification error: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )

            results.append(result)

        evaluated = [item for item in results if item.passed is not None]

        if not evaluated:
            return OutcomeVerificationResult(
                available=False,
                assertions=results,
            )

        required_failures = sum(
            1
            for item in evaluated
            if item.required and item.passed is False
        )

        required_unknown = any(
            item.required and item.passed is None for item in results
        )

        total_weight = sum(item.weight for item in evaluated)
        weighted_score = sum(item.weight * item.score for item in evaluated)
        score = weighted_score / total_weight if total_weight > 0 else 0.0

        strength = sum(
            item.strength for item in evaluated if item.passed
        )

        passed = required_failures == 0

        # One strong deterministic assertion, or a combination of smaller
        # assertions, can fully establish the outcome.
        decisive = passed and not required_unknown and strength >= 1.0

        return OutcomeVerificationResult(
            available=True,
            passed=passed,
            decisive=decisive,
            score=max(0.0, min(1.0, score)),
            evaluated_assertions=len(evaluated),
            required_failures=required_failures,
            evidence_strength=strength,
            assertions=results,
        )

    # ------------------------------------------------------------------
    # Per-assertion verification
    # ------------------------------------------------------------------

    async def _verify_one(
        self,
        *,
        assertion: OutcomeAssertion,
        fixture: ReplayFixtureManifest | None,
        workspace: Path,
        uploads: Path,
    ) -> AssertionResult:
        kind = assertion.type
        strength = self._STRENGTH.get(kind, 0.5)

        path: Path | None = None
        if assertion.path:
            path = self._resolve_path(assertion.path, workspace=workspace, uploads=uploads)

        if kind == OutcomeAssertionType.FILE_EXISTS:
            assert path is not None
            exists = path.is_file() or path.is_dir()
            return self._result(
                assertion,
                passed=exists,
                score=1.0 if exists else 0.0,
                strength=strength,
                actual=exists,
                expected=True,
                reason="path exists" if exists else "path does not exist",
            )

        if kind == OutcomeAssertionType.FILE_NOT_EXISTS:
            assert path is not None
            missing = not path.exists()
            return self._result(
                assertion,
                passed=missing,
                score=1.0 if missing else 0.0,
                strength=strength,
                actual=path.exists(),
                expected=False,
                reason="path is absent" if missing else "path still exists",
            )

        if kind == OutcomeAssertionType.FILE_SHA256:
            assert path is not None
            expected = assertion.expected_sha256
            if not expected:
                raise ValueError("file_sha256 requires expected_sha256")

            if not path.is_file():
                return self._result(
                    assertion,
                    passed=False,
                    score=0.0,
                    strength=strength,
                    expected=expected,
                    actual=None,
                    reason="expected file does not exist",
                )

            actual = self._sha256(path)
            passed = actual == expected
            return self._result(
                assertion,
                passed=passed,
                score=1.0 if passed else 0.0,
                strength=strength,
                expected=expected,
                actual=actual,
                reason="file hash matches" if passed else "file hash differs",
            )

        if kind == OutcomeAssertionType.FILE_CHANGED_FROM_FIXTURE:
            assert path is not None
            if fixture is None:
                raise ValueError("changed-from-fixture assertion requires initial fixture")

            expected_initial = self._fixture_file(fixture, assertion.path or "")
            if expected_initial is None:
                raise ValueError("path was not present in initial fixture")

            if not path.is_file():
                return self._result(
                    assertion,
                    passed=False,
                    score=0.0,
                    strength=strength,
                    expected="different from " + expected_initial.sha256,
                    actual=None,
                    reason="file is missing",
                )

            actual = self._sha256(path)
            changed = actual != expected_initial.sha256
            return self._result(
                assertion,
                passed=changed,
                score=1.0 if changed else 0.0,
                strength=strength,
                expected="different from " + expected_initial.sha256,
                actual=actual,
                reason="file changed" if changed else "file remained unchanged",
            )

        if kind == OutcomeAssertionType.FILE_TEXT_SIMILARITY:
            assert path is not None
            digest = assertion.expected_object_sha256
            if not digest:
                raise ValueError("text similarity requires expected_object_sha256")

            if not path.is_file():
                return self._result(
                    assertion,
                    passed=False,
                    score=0.0,
                    strength=strength,
                    reason="expected text file missing",
                )

            expected_bytes = await self._fixtures.read_object(digest)
            expected_text = expected_bytes.decode("utf-8")
            actual_text = path.read_text(encoding="utf-8")

            similarity = SequenceMatcher(
                None,
                self._normalise_text(expected_text),
                self._normalise_text(actual_text),
                autojunk=False,
            ).ratio()

            passed = similarity >= assertion.similarity_threshold
            return self._result(
                assertion,
                passed=passed,
                score=similarity,
                strength=strength,
                expected={
                    "minimum_similarity": assertion.similarity_threshold,
                    "object": digest,
                },
                actual={"similarity": similarity},
                reason=f"text similarity {similarity:.3f}",
            )

        if kind == OutcomeAssertionType.FILE_JSON_EQUALS:
            assert path is not None
            digest = assertion.expected_object_sha256
            if not digest:
                raise ValueError("json equality requires expected_object_sha256")

            if not path.is_file():
                return self._result(
                    assertion,
                    passed=False,
                    score=0.0,
                    strength=strength,
                    reason="expected JSON file missing",
                )

            expected_raw = await self._fixtures.read_object(digest)
            expected = json.loads(expected_raw.decode("utf-8"))
            actual = json.loads(path.read_text(encoding="utf-8"))

            passed = actual == expected
            return self._result(
                assertion,
                passed=passed,
                score=1.0 if passed else 0.0,
                strength=strength,
                expected=expected,
                actual=actual,
                reason="JSON structure matches" if passed else "JSON structure differs",
            )

        if kind == OutcomeAssertionType.SQLITE_QUERY_EQUALS:
            assert path is not None
            if not assertion.query:
                raise ValueError("sqlite_query_equals requires query")

            rows = self._sqlite_query(path, assertion.query)
            expected = assertion.expected_rows or []
            passed = rows == expected
            return self._result(
                assertion,
                passed=passed,
                score=1.0 if passed else 0.0,
                strength=strength,
                expected=expected,
                actual=rows,
                reason=(
                    "SQLite query matches expected rows"
                    if passed
                    else "SQLite query returned different rows"
                ),
            )

        if kind == OutcomeAssertionType.SQLITE_ROW_COUNT:
            assert path is not None
            if not assertion.query:
                raise ValueError("sqlite_row_count requires query")

            expected_count = assertion.expected_row_count
            if expected_count is None:
                raise ValueError("sqlite_row_count requires expected_row_count")

            rows = self._sqlite_query(path, assertion.query)
            actual_count = len(rows)
            passed = actual_count == expected_count
            return self._result(
                assertion,
                passed=passed,
                score=1.0 if passed else 0.0,
                strength=strength,
                expected=expected_count,
                actual=actual_count,
                reason=(
                    "SQLite row count matches"
                    if passed
                    else "SQLite row count differs"
                ),
            )

        raise ValueError(f"unsupported outcome assertion: {kind}")

    @staticmethod
    def _result(
        assertion: OutcomeAssertion,
        *,
        passed: bool,
        score: float,
        strength: float,
        reason: str,
        actual: Any = None,
        expected: Any = None,
    ) -> AssertionResult:
        return AssertionResult(
            type=assertion.type.value,
            path=assertion.path,
            required=assertion.required,
            weight=assertion.weight,
            passed=passed,
            score=max(0.0, min(1.0, score)),
            strength=strength if passed else 0.0,
            reason=reason,
            actual=actual,
            expected=expected,
        )

    @staticmethod
    def _fixture_file(
        fixture: ReplayFixtureManifest,
        virtual_path: str,
    ) -> ReplayFixtureFile | None:
        normalised = OutcomeVerifier._normalise_virtual_path(virtual_path)
        for item in fixture.files:
            if OutcomeVerifier._normalise_virtual_path(item.virtual_path) == normalised:
                return item
        return None

    @staticmethod
    def _resolve_path(
        virtual_path: str,
        *,
        workspace: Path,
        uploads: Path,
    ) -> Path:
        value = OutcomeVerifier._normalise_virtual_path(virtual_path)

        if value.startswith("/workspace/"):
            root = workspace
            relative = value[len("/workspace/") :]
        elif value.startswith("/uploads/"):
            root = uploads
            relative = value[len("/uploads/") :]
        else:
            raise ValueError("verification path must be under /workspace or /uploads")

        resolved = (root / relative).resolve()

        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("verification path escapes evaluation root") from exc

        return resolved

    @staticmethod
    def _sqlite_query(path: Path, query: str) -> list[list[Any]]:
        if not path.is_file():
            raise FileNotFoundError(str(path))

        cleaned = query.strip().lstrip("(").strip().lower()

        if not cleaned.startswith(("select", "with", "pragma")):
            raise ValueError("verification SQLite queries must be read-only")

        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro",
            uri=True,
        )

        try:
            cursor = connection.execute(query)
            rows = cursor.fetchall()
            return [
                [OutcomeVerifier._json_safe(value) for value in row]
                for row in rows
            ]
        finally:
            connection.close()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _normalise_text(value: str) -> str:
        return "\n".join(
            line.rstrip()
            for line in value.replace("\r\n", "\n").replace("\r", "\n").strip().splitlines()
        )

    @staticmethod
    def _normalise_virtual_path(value: str) -> str:
        value = str(value).replace("\\", "/").strip()
        if not value.startswith("/"):
            value = "/" + value
        while "//" in value:
            value = value.replace("//", "/")
        return value

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, bytes):
            return {"__bytes_hex__": value.hex()}
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)