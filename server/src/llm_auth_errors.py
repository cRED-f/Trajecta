"""Actionable, credential-safe error messages for LLM gateway failures."""
from __future__ import annotations

from typing import Any


def describe_gateway_failure(exc: Exception) -> str:
    """Distinguish 9Router upstream 401 from Bifrost virtual-key 401.

    Keep unknown errors unchanged for diagnostics, but don't echo any response
    payload from known credential errors (they can contain account details).
    """
    body: Any = getattr(exc, "body", None)
    if not isinstance(body, dict):
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                body = response.json()
            except (ValueError, AttributeError):
                pass
    detail = str(exc)
    data = body if isinstance(body, dict) else {}
    extra = data.get("extra_fields") if isinstance(data.get("extra_fields"), dict) else {}
    provider = str(extra.get("provider") or "").lower()
    error_type = str(extra.get("error_type") or "").lower()
    error_code = str(data.get("type") or "").lower()
    # OpenAI-compatible clients sometimes stringify JSON/Python dictionaries
    # instead of exposing a structured body. Match the supplied gateway codes.
    lower = detail.lower()
    downstream = (
        data.get("is_bifrost_error") is False
        or "'is_bifrost_error': false" in lower
        or '"is_bifrost_error": false' in lower
    )
    is_401 = (getattr(exc, "status_code", None) == 401 or
              data.get("status_code") == 401 or
              "error code: 401" in lower or "status_code': 401" in lower)
    is_9router = provider == "9router" or "'provider': '9router'" in lower or '"provider": "9router"' in lower
    revoked = (error_code == "access_not_found" or error_type == "policy_access_denied" or
               "access_not_found" in lower or "policy_access_denied" in lower)
    if is_401 and downstream and is_9router and revoked:
        return (
            "9Router credential rejected (HTTP 401: access_not_found). "
            "Bifrost forwarded the request, but 9Router or its upstream model "
            "account denied access. In Bifrost Dashboard > Providers > 9router, "
            "verify the 9Router API key; in the 9Router dashboard, verify "
            "the model's provider account is connected and authorized. "
            "A different Trajecta/Bifrost virtual key cannot repair a "
            "revoked 9Router or upstream credential."
        )
    if is_401 and ("virtual key" in lower or "invalid_virtual_key" in lower):
        return (
            "Bifrost rejected Trajecta's virtual key (HTTP 401). "
            "For the managed gateway, reinstall Trajecta to provision its "
            "persistent key and restart both services. For an external "
            "gateway, register the BIFROST_VIRTUAL_KEY there."
        )
    return detail


def is_non_retryable_gateway_auth_failure(exc: Exception) -> bool:
    """Avoid retrying revoked credentials in the durable learning queue."""
    message = describe_gateway_failure(exc)
    return (message.startswith("9Router credential rejected") or
            message.startswith("Bifrost rejected Trajecta's virtual key"))
