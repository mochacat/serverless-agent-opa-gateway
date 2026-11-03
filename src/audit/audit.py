"""Audit record builder + injected sink — the per-action witness (DESIGN N5).

Every allowed AND denied tool dispatch produces one structured audit record:
``{tool, args, principal, context, decision, policy_version, reason, trace_id}``.

The gateway writes the record through an **injected** :class:`AuditSink` (O5 /
testability). Production uses CloudWatch; the local demo and every test tier
substitute a stdout or in-memory sink. The audit record is the mandatory
compliance artifact (N5) — it is written on the decision path for every call,
and OTel traces/metrics are an observability enhancement on top (never a
replacement, never on the critical path).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Mapping
from typing import Any, Protocol


def build_audit_record(
    *,
    tool: str,
    args: Mapping[str, Any],
    principal: Mapping[str, Any],
    context: Mapping[str, Any],
    decision: Mapping[str, Any],
    policy_version: str,
    reason: str,
    trace_id: str,
    ts: float | None = None,
) -> dict[str, Any]:
    """Assemble the immutable audit record for one dispatch (allow OR deny).

    Returns a plain dict so any sink (CloudWatch, stdout, in-memory, JSON
    logger) can serialize it. ``ts`` is injectable for deterministic tests.
    """
    return {
        "tool": tool,
        "args": dict(args or {}),
        "principal": dict(principal or {}),
        "context": dict(context or {}),
        "decision": dict(decision or {}),
        "policy_version": policy_version,
        "reason": reason,
        "trace_id": trace_id,
        "ts": ts if ts is not None else time.time(),
    }


class AuditSink(Protocol):
    """Writes one audit record. Implemented by CloudWatch/local/stdio sinks."""

    def record(self, record: Mapping[str, Any]) -> None:
        """Persist/emit a single audit record (called on allow AND deny)."""
        ...


class StdoutAuditSink:
    """Deterministic, zero-dependency sink for the local demo (N6).

    Emits one JSON object per line to a stream so it is greppable and safe to
    redirect anywhere. ``stream`` is injectable for tests.
    """

    def __init__(self, stream=sys.stdout) -> None:
        self._stream = stream

    def record(self, record: Mapping[str, Any]) -> None:
        import json

        self._stream.write(json.dumps(record, sort_keys=True) + "\n")
        self._stream.flush()


class InMemoryAuditSink:
    """Captures records for tests/assertions (T3/T4/T5 injected fake)."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, record: Mapping[str, Any]) -> None:
        self.records.append(dict(record))

    def __len__(self) -> int:
        return len(self.records)


__all__ = [
    "AuditSink",
    "InMemoryAuditSink",
    "StdoutAuditSink",
    "build_audit_record",
]
