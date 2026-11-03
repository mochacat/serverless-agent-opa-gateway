"""Swappable session store supplying cross-call state (DESIGN D/E, §4.3/§4.4).

OPA stays pure and stateless — the *in-process policy engine never reads or
writes cross-call state*. Instead the gateway asks this store for the session's
trajectory (feature ``D``, look-before-you-leap) and rate window (feature ``E``,
deny-window cap) and injects them into ``input.context`` before evaluation.

The store is *injected* — the gateway depends on the protocol, not a backing
technology (O5/testability). The default is an in-memory implementation fit for
the local demo (N6, zero AWS); production swaps in a Db/Redis-backed one that
implements the same protocol. Nothing in the engine or policy cares which.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# The seam. Any production store (DynamoDB, Redis, Postgres) implements this.
# ---------------------------------------------------------------------------
class SessionStore(Protocol):
    """Read/append a session's tool trajectory, plus its rate config."""

    def trajectory_for(self, session_id: str) -> list[dict[str, Any]]:
        """Return the ordered list of past tool events for the session.

        Each entry carries at least ``{"tool": <name>}``; optionally
        ``{"at": <epoch-secs>}``. Used by the policy to build ``context.
        trajectory`` (feature D).
        """
        ...

    def record_event(self, session_id: str, tool: str, **attrs: Any) -> None:
        """Append one tool dispatch to the session history (called AFTER a
        decision, so the trajectory reflects actions actually taken)."""
        ...

    def rate_config_for(self, session_id: str) -> dict[str, Any]:
        """Return the rate-window budget for the session: ``{"max": <int>}``.
        The engine injects it as ``context.rate_window`` (feature E)."""
        ...


@dataclass
class InMemorySessionStore:
    """Thread-safe, dependency-free store for the local demo (N6).

    Holds trajectory + rate config per session in-process. Not durable across
    restarts by design — it demonstrates the *interface contract*, and
    production replaces it with a database-backed implementation.
    """

    rate_max_default: int = 3
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _trajectory: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _rates: dict[str, int] = field(default_factory=dict)

    def trajectory_for(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._trajectory.get(session_id, []))

    def record_event(self, session_id: str, tool: str, **attrs: Any) -> None:
        entry: dict[str, Any] = {"tool": tool, **attrs}
        entry.setdefault("at", time.time())
        with self._lock:
            self._trajectory.setdefault(session_id, []).append(entry)

    def rate_config_for(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            max_ops = self._rates.get(session_id, self.rate_max_default)
        return {"max": max_ops}

    def set_rate_max(self, session_id: str, max_ops: int) -> None:
        """Tune a session's destructive-op budget (feature E)."""
        with self._lock:
            self._rates[session_id] = max_ops


def enrich_context_with_session(
    context: Mapping[str, Any] | None,
    store: SessionStore,
    session_id: str,
) -> dict[str, Any]:
    """Layer the session's trajectory + rate window onto a request context.

    This is how the gateway hands the policy its D/E state *without* OPA ever
    touching the store: the store is read here, once, and the resulting plain
    data is injected into ``input.context`` (DESIGN §4.4 / N4 determinism).
    Existing context keys win over defaults so callers can pin/override.
    """
    merged: dict[str, Any] = dict(context or {})
    merged.setdefault("trajectory", store.trajectory_for(session_id))
    merged.setdefault("rate_window", store.rate_config_for(session_id))
    return merged


__all__ = [
    "SessionStore",
    "InMemorySessionStore",
    "enrich_context_with_session",
]
