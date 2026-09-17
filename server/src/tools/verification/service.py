"""Connector read-back verification.

Execute mutating connector (MCP) tools, then independently read the resulting
resource back and compare the actual state against the requested state. An
action is never considered VERIFIED from its own return value.

MCP tools are *wrapped* after discovery (langchain.mcp.MCPAdapter itself is not
replaced): configured action tools get verified execution around them while
readback tools remain raw so verification cannot recursively trigger itself.
"""

from __future__ import annotations

import json
import uuid

from datetime import UTC, datetime

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from server.src.config import (
    ConnectorVerificationRuleConfig,
    ReadbackComparisonConfig,
    Settings,
)

from server.src.memory.storage.sqlite import SQLiteDatabase

from server.src.tools.verification.models import (
    ActionReceipt,
    ComparisonResult,
    VerificationStatus,
    VerifiedActionResult,
)

from server.src.tools.verification.values import (
    get_path,
    json_safe,
    maybe_parse_json,
    redact,
    resolve_expression,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ConnectorVerificationService:
    """Execute mutating connector tools, then independently read the result back.

    An action is never considered VERIFIED from its own return value.
    """

    def __init__(
        self,
        settings: Settings,
        db: SQLiteDatabase,
    ) -> None:
        self._settings = settings
        self._db = db
        self._config = settings.tools.connector_verification

    # -------------------------------------------------
    # Tool wrapping
    # -------------------------------------------------

    def wrap_connector_tools(
        self,
        tools: list[BaseTool],
    ) -> list[BaseTool]:
        """Wrap only connector actions with configured verification rules.

        Readback tools remain raw so verification cannot recursively trigger
        itself.
        """

        if not self._config.enabled:
            return tools

        lookup: dict[tuple[str, str], BaseTool] = {}

        for tool in tools:
            server = self._server_name(tool)
            lookup[(server, tool.name)] = tool

        wrapped: list[BaseTool] = []

        for tool in tools:
            server = self._server_name(tool)

            rule = self._find_rule(server, tool.name)

            if rule is None:
                wrapped.append(tool)
                continue

            wrapped.append(
                self._wrap_tool(
                    server=server,
                    tool=tool,
                    rule=rule,
                    lookup=lookup,
                )
            )

        return wrapped

    def interrupt_policy(
        self,
    ) -> dict[str, Any]:
        """MCP mutating actions require explicit approval before they are executed."""

        if not self._settings.guardrails.hitl_enabled:
            return {}

        result: dict[str, Any] = {}

        for rule in self._config.rules.values():
            result[rule.action_tool] = {
                "allowed_decisions": ["approve", "edit", "reject"],
            }

        return result

    def _wrap_tool(
        self,
        *,
        server: str,
        tool: BaseTool,
        rule: ConnectorVerificationRuleConfig,
        lookup: dict[tuple[str, str], BaseTool],
    ) -> BaseTool:
        async def verified_call(**kwargs: Any) -> dict[str, Any]:
            result = await self.execute_and_verify(
                connector=server,
                action_tool=tool,
                action_args=kwargs,
                rule=rule,
                lookup=lookup,
            )
            return result.model_dump(mode="json")

        schema = tool.args_schema or tool.get_input_schema()

        wrapped = StructuredTool.from_function(
            coroutine=verified_call,
            name=tool.name,
            description=(
                (tool.description or "")
                + "\n\n"
                "Trajecta independently reads the affected resource back before "
                "reporting this action as verified."
            ),
            args_schema=schema,
            infer_schema=False,
        )

        wrapped.metadata = {
            **(tool.metadata or {}),
            "trajecta": {
                "connector": server,
                "verified_action": True,
            },
        }

        return wrapped

    # -------------------------------------------------
    # Execution
    # -------------------------------------------------

    async def execute_and_verify(
        self,
        *,
        connector: str,
        action_tool: BaseTool,
        action_args: dict[str, Any],
        rule: ConnectorVerificationRuleConfig,
        lookup: dict[tuple[str, str], BaseTool],
    ) -> VerifiedActionResult:
        receipt = ActionReceipt(
            id=uuid.uuid4().hex,
            connector=connector,
            action_tool=action_tool.name,
            status=VerificationStatus.UNVERIFIED,
            action_args=redact(action_args),
            readback_tool=rule.readback_tool,
            metadata={
                "manual_only": rule.manual_only,
                "verification_required": rule.verification_required,
            },
        )

        await self._save_receipt(receipt)

        # -----------------------------------------
        # Execute mutating action
        # -----------------------------------------

        try:
            raw_action_result = await action_tool.ainvoke(action_args)
        except Exception as exc:
            receipt.status = VerificationStatus.ACTION_FAILED
            receipt.action_result = {
                "error": f"{type(exc).__name__}: {exc}",
            }
            await self._save_receipt(receipt)
            raise

        action_result = maybe_parse_json(raw_action_result)

        receipt.action_result = redact(json_safe(action_result))

        # MCP execution errors can be represented as ToolMessages instead of
        # exceptions.
        if self._is_error_result(raw_action_result):
            receipt.status = VerificationStatus.ACTION_FAILED
            await self._save_receipt(receipt)
            return VerifiedActionResult(
                ok=False,
                verification_status=receipt.status,
                receipt_id=receipt.id,
                action_result=receipt.action_result,
                message="Connector action reported an error.",
            )

        # -----------------------------------------
        # Extract resource identity
        # -----------------------------------------

        resource_id = self._extract_resource_id(action_result, rule.resource_id_paths)
        receipt.resource_id = resource_id

        # -----------------------------------------
        # Manual-only connectors
        # -----------------------------------------

        if rule.manual_only:
            receipt.status = VerificationStatus.NEEDS_REVIEW
            await self._save_receipt(receipt)
            return VerifiedActionResult(
                ok=False,
                verification_status=receipt.status,
                receipt_id=receipt.id,
                resource_id=resource_id,
                action_result=receipt.action_result,
                message="Action executed, but this connector requires manual verification.",
            )

        # -----------------------------------------
        # No readback recipe
        # -----------------------------------------

        if not rule.readback_tool:
            status = (
                VerificationStatus.VERIFICATION_FAILED
                if rule.verification_required and self._config.fail_closed
                else VerificationStatus.UNVERIFIED
            )
            receipt.status = status
            await self._save_receipt(receipt)
            return VerifiedActionResult(
                ok=False,
                verification_status=status,
                receipt_id=receipt.id,
                resource_id=resource_id,
                action_result=receipt.action_result,
                message="Action executed but no independent readback tool is configured.",
            )

        readback_server = rule.readback_server or connector
        readback_tool = lookup.get((readback_server, rule.readback_tool))

        if readback_tool is None:
            receipt.status = VerificationStatus.VERIFICATION_FAILED
            await self._save_receipt(receipt)
            return VerifiedActionResult(
                ok=False,
                verification_status=receipt.status,
                receipt_id=receipt.id,
                resource_id=resource_id,
                action_result=receipt.action_result,
                message=(
                    "Action executed, but configured readback tool "
                    f"{readback_server}.{rule.readback_tool} was not found."
                ),
            )

        # -----------------------------------------
        # Build independent readback args
        # -----------------------------------------

        readback_args: dict[str, Any] = {}

        for key, expression in rule.readback_args.items():
            value = resolve_expression(
                expression,
                action_args=action_args,
                action_result=action_result,
                receipt=receipt,
            )
            readback_args[key] = value

        receipt.readback_args = redact(readback_args)

        # -----------------------------------------
        # Independent readback
        # -----------------------------------------

        try:
            raw_readback = await readback_tool.ainvoke(readback_args)
        except Exception as exc:
            receipt.status = VerificationStatus.VERIFICATION_FAILED
            receipt.readback_result = {
                "error": f"{type(exc).__name__}: {exc}",
            }
            await self._save_receipt(receipt)
            return VerifiedActionResult(
                ok=False,
                verification_status=receipt.status,
                receipt_id=receipt.id,
                resource_id=resource_id,
                action_result=receipt.action_result,
                readback_result=receipt.readback_result,
                message="Action executed, but independent readback failed.",
            )

        readback_result = maybe_parse_json(raw_readback)

        receipt.readback_result = redact(json_safe(readback_result))

        if self._is_error_result(raw_readback):
            receipt.status = VerificationStatus.VERIFICATION_FAILED
            await self._save_receipt(receipt)
            return VerifiedActionResult(
                ok=False,
                verification_status=receipt.status,
                receipt_id=receipt.id,
                resource_id=resource_id,
                action_result=receipt.action_result,
                readback_result=receipt.readback_result,
                message="Action executed, but the readback tool reported an error.",
            )

        # -----------------------------------------
        # Compare requested vs actual state
        # -----------------------------------------

        comparisons = [
            self._compare(
                item,
                action_args=action_args,
                action_result=action_result,
                receipt=receipt,
                readback_result=readback_result,
            )
            for item in rule.comparisons
        ]

        receipt.comparisons = comparisons

        required_failed = any(item.required and not item.passed for item in comparisons)

        # A successful readback with zero comparison rules proves resource
        # existence/readability.
        if required_failed:
            receipt.status = VerificationStatus.VERIFICATION_FAILED
        else:
            receipt.status = VerificationStatus.VERIFIED
            receipt.verified_at = _now()

        await self._save_receipt(receipt)

        return VerifiedActionResult(
            ok=receipt.status == VerificationStatus.VERIFIED,
            verification_status=receipt.status,
            receipt_id=receipt.id,
            resource_id=resource_id,
            action_result=receipt.action_result,
            readback_result=receipt.readback_result,
            comparisons=comparisons,
            message=(
                "Action independently verified by readback."
                if receipt.status == VerificationStatus.VERIFIED
                else "Action executed, but readback did not match the requested state."
            ),
        )

    # -------------------------------------------------
    # Comparison
    # -------------------------------------------------

    @staticmethod
    def _compare(
        config: ReadbackComparisonConfig,
        *,
        action_args: dict[str, Any],
        action_result: Any,
        receipt: ActionReceipt,
        readback_result: Any,
    ) -> ComparisonResult:
        actual = get_path(readback_result, config.actual_path)

        if config.expected_from is not None:
            expected = resolve_expression(
                config.expected_from,
                action_args=action_args,
                action_result=action_result,
                receipt=receipt,
                readback_result=readback_result,
            )
        else:
            expected = config.expected_value

        operator = config.operator

        if operator == "equals":
            passed = actual == expected
        elif operator == "contains":
            if isinstance(actual, str):
                passed = str(expected) in actual
            elif isinstance(actual, (list, tuple, set)):
                passed = expected in actual
            elif isinstance(actual, dict):
                passed = expected in actual
            else:
                passed = False
        elif operator == "exists":
            passed = actual is not None
        elif operator == "not_exists":
            passed = actual is None
        else:
            passed = False

        return ComparisonResult(
            actual_path=config.actual_path,
            operator=operator,
            required=config.required,
            passed=passed,
            expected=redact(expected),
            actual=redact(actual),
            reason="readback matched" if passed else "readback did not match",
        )

    # -------------------------------------------------
    # Rule lookup
    # -------------------------------------------------

    def _find_rule(
        self,
        server: str,
        tool_name: str,
    ) -> ConnectorVerificationRuleConfig | None:
        exact_key = f"{server}.{tool_name}"

        if exact_key in self._config.rules:
            return self._config.rules[exact_key]

        # Optional fallback where config is keyed arbitrarily.
        for rule in self._config.rules.values():
            if rule.action_tool == tool_name:
                return rule

        return None

    @staticmethod
    def _server_name(tool: BaseTool) -> str:
        metadata = tool.metadata or {}

        trajecta = metadata.get("trajecta")
        if isinstance(trajecta, dict):
            # Raw discoveries carry `connector_server`/`mcp_server`; a wrapped
            # verification tool carries `connector` (re-written by _wrap_tool).
            for key in ("connector_server", "mcp_server", "connector"):
                server = trajecta.get(key)
                if server:
                    return str(server)

        mcp = metadata.get("mcp")
        if isinstance(mcp, dict):
            server = mcp.get("server")
            if isinstance(server, dict):
                name = server.get("name")
                if name:
                    return str(name)

        return "unknown"

    @staticmethod
    def _extract_resource_id(
        result: Any,
        paths: list[str],
    ) -> str | None:
        for path in paths:
            value = get_path(result, path)
            if value is None:
                continue
            if isinstance(value, (str, int)):
                return str(value)
        return None

    @staticmethod
    def _is_error_result(value: Any) -> bool:
        status = getattr(value, "status", None)
        if status is not None and str(status).lower() == "error":
            return True

        safe = json_safe(value)

        if isinstance(safe, dict):
            result_status = safe.get("status")
            if (
                isinstance(result_status, str)
                and result_status.lower() in {"error", "failed", "failure"}
            ):
                return True

        return False

    # -------------------------------------------------
    # Persistence
    # -------------------------------------------------

    async def _save_receipt(self, receipt: ActionReceipt) -> None:
        await self._db.execute(
            """
            INSERT INTO action_receipts(
                id,
                connector,
                action_tool,
                resource_type,
                resource_id,
                status,
                action_args,
                action_result,
                readback_tool,
                readback_args,
                readback_result,
                comparison_result,
                created_at,
                verified_at,
                metadata
            )

            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(id)
            DO UPDATE SET

                resource_type = excluded.resource_type,
                resource_id = excluded.resource_id,
                status = excluded.status,
                action_result = excluded.action_result,
                readback_tool = excluded.readback_tool,
                readback_args = excluded.readback_args,
                readback_result = excluded.readback_result,
                comparison_result = excluded.comparison_result,
                verified_at = excluded.verified_at,
                metadata = excluded.metadata
            """,
            (
                receipt.id,
                receipt.connector,
                receipt.action_tool,
                receipt.resource_type,
                receipt.resource_id,
                receipt.status.value,
                json.dumps(redact(receipt.action_args), ensure_ascii=False),
                json.dumps(redact(receipt.action_result), ensure_ascii=False),
                receipt.readback_tool,
                json.dumps(redact(receipt.readback_args), ensure_ascii=False),
                json.dumps(redact(receipt.readback_result), ensure_ascii=False),
                json.dumps(
                    [item.model_dump(mode="json") for item in receipt.comparisons],
                    ensure_ascii=False,
                ),
                receipt.created_at,
                receipt.verified_at,
                json.dumps(redact(receipt.metadata), ensure_ascii=False),
            ),
        )

    async def get_receipt(
        self,
        receipt_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            """
            SELECT *
            FROM action_receipts
            WHERE id = ?
            """,
            (receipt_id,),
        )

        if row is None:
            return None

        result = dict(row)

        for key in (
            "action_args",
            "action_result",
            "readback_args",
            "readback_result",
            "comparison_result",
            "metadata",
        ):
            try:
                result[key] = json.loads(
                    result.get(key)
                    or ("[]" if key == "comparison_result" else "{}")
                )
            except json.JSONDecodeError:
                pass

        return result