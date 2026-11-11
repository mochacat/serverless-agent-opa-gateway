"""The gateway's policy engine — evaluates OPA Rego in-process (DESIGN §4.4).

Loads every ``policy_engine``-relevant ``.rego`` under ``policies/`` once via
``regopy`` at module import (Lambda cold start) and exposes a stable,
portable contract::

    decision = PolicyEngine().evaluate(tool, args, context, principal)

No OPA server, no sidecar, no network hop (D2/D2a/D5). OPA stays pure and
stateless — all cross-call state (trajectory ``D``, rate ``E``) arrives via
``context`` from a swappable server-side :class:`SessionStore`. The engine's
only job is "same input → same decision, always" (NFR4 determinism).

The Rego input document is built from the four structured parameters. For
policy-specific fields (e.g. a dispatch key like ``industry``), pass them
via the ``extra`` dict — they are merged into ``input`` verbatim so the
policy can use them without the engine knowing their meaning.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import regopy

log = logging.getLogger(__name__)

# Policy source directory, resolved from this file so the engine works
# regardless of the process CWD (repo root, tests/, a Lambda bundle root).
POLICIES_DIR = Path(__file__).resolve().parent.parent.parent / "policies"

# The dispatch package (policy.rego) is queried for the stable decision object.
DECISION_QUERY = "data.ai.guardrails.decision"

FAIL_CLOSED_REASON = "BLOCKED: default-deny — policy decision could not be evaluated."


@dataclass(frozen=True)
class Decision:
    """A single deterministic policy verdict (DESIGN §4.3 output contract).

    ``redact`` is ``None`` when no output redaction applies (feature G); when
    set, it lists the sensitive field names the gateway must strip from the
    tool result before returning it to the LLM.
    """

    allow: bool
    reason: str
    redact: tuple[str, ...] | None = None

    @classmethod
    def fail_closed(cls, reason: str = FAIL_CLOSED_REASON) -> Decision:
        return cls(allow=False, reason=reason)


class PolicyEngineError(Exception):
    """Raised when the policy cannot be loaded or evaluated."""


class PolicyEngine:
    """In-process OPA/Rego engine bound to ``policies/``.

    Instantiated once (module-level singleton below) so the Rego is compiled
    a single time — a cold-start (NFR2) cost paid once, reused across warm
    invocations. Stateless and thread-safe to call: it never mutates policy,
    only the per-evaluation input document.
    """

    def __init__(self, policies_dir: Path = POLICIES_DIR) -> None:
        self._interp = regopy.Interpreter()
        self._load_policies(policies_dir)
        # Fast-path fallback if a policy file is ever missing: never allow.
        self._policies_dir = policies_dir
        # The regopy Interpreter mutates a single input document per call and
        # is NOT thread-safe: concurrent set_input/query segfaults. Serialize
        # evaluations so the single compiled interpreter is reusable while
        # staying safe under concurrent gateway workers (Lambda environments
        # are single-request each, but the local server may run threads).
        self._eval_lock = threading.Lock()

    # -- loading ------------------------------------------------------------
    def _load_policies(self, policies_dir: Path) -> None:
        # Only production policy modules are embedded in the runtime. The
        # `*_test.rego` files are Rego unit tests run by `opa test` (T1) — they
        # declare rules in the SAME packages (OPA test convention) and would
        # shadow the production `decision`, so they are never loaded here.
        rego_files = sorted(
            p for p in policies_dir.glob("*.rego") if not p.name.endswith("_test.rego")
        )
        if not rego_files:
            raise PolicyEngineError(f"no .rego policies found under {policies_dir}")
        for path in rego_files:
            try:
                self._interp.add_module(path.name, path.read_text(encoding="utf-8"))
                log.debug("loaded policy module %s", path.name)
            except regopy.RegoError as exc:  # pragma: no cover - parser contract
                raise PolicyEngineError(f"failed to parse {path.name}: {exc}") from exc

    # -- evaluation ---------------------------------------------------------
    def evaluate(
        self,
        tool: str,
        args: Mapping[str, Any] | None,
        context: Mapping[str, Any] | None,
        principal: Mapping[str, Any] | None,
        extra: Mapping[str, Any] | None = None,
    ) -> Decision:
        """Return the deterministic verdict for one tool dispatch.

        ``tool``, ``args``, ``context`` (trajectory/rate/approval/target) and
        ``principal`` are the policy's input contract (§4.3). ``extra`` is an
        optional dict of policy-specific fields merged into the Rego ``input``
        document — the engine does not interpret them; they are for the policy
        to use (e.g. an example dispatch key like ``industry``).
        """
        payload: dict[str, Any] = {
            "tool": tool,
            "args": dict(args or {}),
            "principal": dict(principal or {}),
            "context": dict(context or {}),
        }
        if extra:
            payload.update(extra)
        # Serialize around the mutable interpreter (see __init__ note).
        with self._eval_lock:
            self._interp.set_input(payload)
            try:
                results = self._interp.query(DECISION_QUERY).results
            except regopy.RegoError as exc:  # pragma: no cover - defensive
                log.exception("policy evaluation failed")
                raise PolicyEngineError(f"policy evaluation failed: {exc}") from exc

        # Undefined decision => default-deny (H). OPA `assignment` returns the
        # canonical singleton document for `data.ai.guardrails.decision`.
        if not results:
            return Decision.fail_closed()

        decision = results[0][0]
        return self._coerce(decision)

    @staticmethod
    def _coerce(raw: Mapping[str, Any]) -> Decision:
        allow = bool(raw.get("allow"))
        reason = str(raw.get("reason") or FAIL_CLOSED_REASON)
        redact = raw.get("redact")
        redact_tuple = tuple(redact) if isinstance(redact, list) else None
        # Belts-and-braces: any parsed decision that isn't a clear allow is a
        # deny — never let a malformed payload open the gate.
        if not allow:
            return Decision(allow=False, reason=reason, redact=redact_tuple)
        return Decision(allow=True, reason=reason, redact=redact_tuple)


# ---- cold-start singleton ------------------------------------------------
# Module-level interpreter: loaded once when the Lambda initializes, reused
# across warm invocations (NFR2). The gateway and handler import this.
_engine: PolicyEngine | None = None


def get_engine() -> PolicyEngine:
    """Return the process-wide engine, loading policies once on first use."""
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
    return _engine


__all__ = ["Decision", "PolicyEngine", "PolicyEngineError", "get_engine"]
