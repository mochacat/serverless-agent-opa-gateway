"""G — output redaction (HERO): strip sensitive fields before the LLM sees them.

The policy returns a ``redact`` list naming fields to remove from the tool
result that must not reach the model (PHI in healthcare, account/card numbers
in fintech, IOCs/internal IDs in secops — see each industry surface). This
module applies that list to a supplied/simulated tool output *before* the
gateway returns it, covering LLM02 (Sensitive Info Disclosure) and LLM10
(Improper Output Handling).

Used by both the local HTTP server (T3.3) and the Lambda handler (T5) — the
sanitizer lives in the gateway, not in OPA (OPA stays a pure decision, G5).
"""

from __future__ import annotations

from typing import Any

# Marker substituted for a redacted value so the field's presence/position
# survives but its content is guaranteed not to reach the model.
REDACTED = "REDACTED"


def sanitize_output(output: Any, redact: list[str] | tuple[str, ...] | None) -> Any:
    """Return ``output`` with every key named in ``redact`` removed at any depth.

    Nested dicts and lists of dicts are traversed recursively; a matching key's
    value is replaced with :data:`REDACTED`. With an empty/None ``redact`` the
    output is returned unchanged (fast path).
    """
    if not redact:
        return output
    sensitive = set(redact)
    return _redact(output, sensitive)


def _redact(node: Any, sensitive: set[str]) -> Any:
    if isinstance(node, dict):
        return {
            key: (_redact(value, sensitive) if key not in sensitive else REDACTED)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_redact(item, sensitive) for item in node]
    return node


__all__ = ["REDACTED", "sanitize_output"]
