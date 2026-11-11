"""Environment-based configuration — single source of truth for deploy-time knobs.

The gateway reads its configuration from environment variables at cold start,
with sensible defaults for local development (zero AWS). The Lambda handler
(``handler.py``) calls :func:`from_env` to wire up the :class:`GatewayConfig`
the deployed app needs — matching the env vars that ``lambda.tf`` sets.

All defaults are the same as before; this module just makes them explicit
and env-var-forward instead of invisible hardcoded literals.
"""

from __future__ import annotations

import os
from typing import Any

# Defaults exported for reuse by tests and other consumers.
# When no POLICY_VERSION env var is set, we default to "dev" (not "v1") so
# local/CI runs never incorrectly stamp a release version on unshipped code.
DEFAULT_POLICY_VERSION = "dev"
DEFAULT_LOG_FORMAT = "json"
DEFAULT_LOG_LEVEL = "DEBUG"
DEFAULT_COMPLIANCE_SCOPE = ""


def str_env(key: str, default: str) -> str:
    """Read ``key`` from the environment, falling back to ``default``."""
    return os.environ.get(key, default)


def int_env(key: str, default: int) -> int:
    """Read ``key`` as an int, falling back to ``default``."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def bool_env(key: str, default: bool) -> bool:
    """Read ``key`` as a boolean, falling back to ``default``.

    True values: ``1``, ``true``, ``yes`` (case-insensitive).
    Everything else is False.
    """
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Gateway-level configuration (maps to the env vars lambda.tf sets)
# ---------------------------------------------------------------------------


def policy_version() -> str:
    """Git-versioned policy version for audit records."""
    return str_env("POLICY_VERSION", DEFAULT_POLICY_VERSION)


def audit_sink_type() -> str:
    """Which audit sink implementation to use (e.g. ``cloudwatch``, ``stdout``)."""
    return str_env("AUDIT_SINK", "cloudwatch")


def log_format() -> str:
    """Structured log format (``json`` or ``text``)."""
    return str_env("LOG_FORMAT", DEFAULT_LOG_FORMAT)


def log_level() -> str:
    """Logging level (``DEBUG``, ``INFO``, ``WARNING``, etc.)."""
    return str_env("LOG_LEVEL", DEFAULT_LOG_LEVEL)


def compliance_scope() -> str:
    """Comma-separated compliance frameworks (e.g. ``hipaa,soc2``)."""
    return str_env("COMPLIANCE_SCOPE", DEFAULT_COMPLIANCE_SCOPE)


def get_config_dict() -> dict[str, Any]:
    """Return all gateway env vars as a dict (useful for structured logging /
    debug output)."""
    return {
        "POLICY_VERSION": policy_version(),
        "AUDIT_SINK": audit_sink_type(),
        "LOG_FORMAT": log_format(),
        "LOG_LEVEL": log_level(),
        "COMPLIANCE_SCOPE": compliance_scope(),
    }


# ---------------------------------------------------------------------------
# GatewayClient-level configuration
# ---------------------------------------------------------------------------


def gateway_url() -> str:
    """Default gateway URL for the client (``GatewayClient``)."""
    return str_env("AGENT_GATEWAY_URL", "http://127.0.0.1:8080")


def gateway_client_timeout() -> float:
    """HTTP timeout in seconds for gateway client calls."""
    return float(int_env("GATEWAY_CLIENT_TIMEOUT", 5))


# ---------------------------------------------------------------------------
# Interceptor-level configuration
# ---------------------------------------------------------------------------


def agent_industry() -> str:
    """Default industry label for framework interceptors."""
    return str_env("AGENT_INDUSTRY", "fintech")


def agent_principal_id() -> str:
    """Default principal_id for framework interceptors."""
    return str_env("AGENT_PRINCIPAL_ID", "ops")


__all__ = [
    "DEFAULT_POLICY_VERSION",
    "DEFAULT_LOG_FORMAT",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_COMPLIANCE_SCOPE",
    "str_env",
    "int_env",
    "bool_env",
    "policy_version",
    "audit_sink_type",
    "log_format",
    "log_level",
    "compliance_scope",
    "get_config_dict",
    "gateway_url",
    "gateway_client_timeout",
    "agent_industry",
    "agent_principal_id",
]
