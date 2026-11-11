"""The gateway app — JSON-RPC ``tools/call`` gate over the policy engine.

This is *the product* (REQUIREMENTS §4.3, DESIGN §4.5): it sits between an
agent and tool *execution* and decides, deterministically, whether a tool
dispatch is allowed — evaluating real OPA Rego in-process via :mod:`regopy`.

Per ``tools/call``:
  1. schema-validate the JSON-RPC payload (reject malformed/unknown),
  2. enrich server-side identity (the agent contributes no credentials),
  3. evaluate via the injected policy engine with ``args + context``,
  4. DENY → JSON-RPC error with the structured policy ``reason``; ALLOW →
     return the action to run and apply G redaction to any supplied output,
  5. write an audit record (allow AND deny) to the injected sink.

The gateway gates **tool dispatch**, never the model request (DESIGN §3.3a);
cross-call state (D/E) is injected from the swappable :class:`SessionStore`;
audit + metrics go to injected sinks. The class is transport-agnostic — the
stdlib HTTP server (T3.3) and the Lambda handler (T5) both call :meth:`handle`.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.audit.audit import AuditSink, InMemoryAuditSink, build_audit_record
from src.engine.policy_engine import Decision, PolicyEngine, get_engine
from src.engine.session_store import InMemorySessionStore, SessionStore, enrich_context_with_session
from src.gateway.redact import sanitize_output
from src.metrics.metrics import InMemoryMetricsSink, MetricsSink

# Methods the gateway understands. Everything else is Method Not Found.
METHOD_TOOLS_CALL = "tools/call"
SUPPORTED_METHODS = frozenset({METHOD_TOOLS_CALL})

# JSON-RPC standard + application codes.
CODE_PARSE_ERROR = -32700
CODE_INVALID_REQUEST = -32600
CODE_METHOD_NOT_FOUND = -32601
CODE_INVALID_PARAMS = -32602
# Application-level: policy denied the dispatch.
CODE_TOOL_DENIED = -32000

# Trace-context "00-<32hex-trace_id>-<16hex-span>-<flags>" (W3C traceparent).
_TRACEPARENT_RE = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")

# Fallback policy version when none is configured (git-versioned, NFR7).
# The Lambda handler (handler.py) overrides this via src.config.policy_version()
# which reads the POLICY_VERSION env var set by lambda.tf, so the default
# below is only used in local demo / test environments.
DEFAULT_POLICY_VERSION = "dev"


@dataclass
class GatewayConfig:
    """Server-side wiring the app needs (injected, zero AWS by default)."""

    policy_version: str = DEFAULT_POLICY_VERSION
    session_store: SessionStore = field(default_factory=InMemorySessionStore)
    audit_sink: AuditSink = field(default_factory=InMemoryAuditSink)
    metrics_sink: MetricsSink = field(default_factory=InMemoryMetricsSink)
    # Server-side identity enrichment. The agent supplies no credentials; the
    # gateway resolves the caller's principal from its own knowledge (config,
    # IAM, a token service) — least-privilege posture (REQUIREMENTS §4.7).
    enrich_principal: Callable[[str, Mapping[str, Any]], dict[str, Any]] | None = None


class GatewayApp:
    """Transport-agnostic JSON-RPC tool-dispatch gate. Thread-safe (no mutable
    per-call state beyond the injected engine, which serializes itself)."""

    def __init__(
        self,
        engine: PolicyEngine | None = None,
        config: GatewayConfig | None = None,
    ) -> None:
        self._engine = engine or get_engine()
        self._config = config or GatewayConfig()

    # -- public entrypoint --------------------------------------------------
    def handle(
        self, body: str | bytes | Mapping[str, Any], traceparent: str | None = None
    ) -> dict[str, Any]:
        """Process one raw JSON-RPC request (string, bytes, or dict) and return
        the response envelope. Deterministic; never raises on bad input."""
        trace_id = self._trace_id(traceparent)
        try:
            if isinstance(body, bytes):
                body = body.decode("utf-8")
            payload = json.loads(body) if isinstance(body, str | bytes) else body
        except (ValueError, TypeError):
            return self._error(None, CODE_PARSE_ERROR, "Parse error", data={"trace_id": trace_id})

        return self._dispatch(payload, trace_id)

    # -- dispatch / routing -------------------------------------------------
    def _dispatch(self, payload: Any, trace_id: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return self._error(
                None, CODE_INVALID_REQUEST, "Invalid Request", data={"trace_id": trace_id}
            )

        request_id = payload.get("id")
        if payload.get("jsonrpc") != "2.0" or "method" not in payload:
            return self._error(
                request_id, CODE_INVALID_REQUEST, "Invalid Request", data={"trace_id": trace_id}
            )

        method = payload.get("method")
        if method not in SUPPORTED_METHODS:
            return self._error(
                request_id, CODE_METHOD_NOT_FOUND, "Method not found", data={"trace_id": trace_id}
            )

        if method == METHOD_TOOLS_CALL:
            result = self._handle_tools_call(payload.get("params"), trace_id, request_id)
            return result

        # Unreachable given SUPPORTED_METHODS, kept for safety.
        return self._error(
            request_id, CODE_METHOD_NOT_FOUND, "Method not found", data={"trace_id": trace_id}
        )

    # -- tools/call ---------------------------------------------------------
    def _handle_tools_call(self, params: Any, trace_id: str, request_id: Any) -> dict[str, Any]:
        validation = self._validate_params(params)
        if validation is not None:
            return self._error(
                request_id,
                CODE_INVALID_PARAMS,
                "Invalid params",
                data=validation | {"trace_id": trace_id},
            )

        tool = params["tool"]
        args = params.get("args") or {}
        output = params.get("output")
        session_id = params.get("session_id")

        # 2. Enrich server-side identity (agent contributes no credentials).
        principal = self._resolve_principal(tool, params)

        # Build context: caller-supplied, layered with session trajectory/rate.
        context = enrich_context_with_session(
            params.get("context"), self._config.session_store, session_id or ""
        )

        # 3. Evaluate the real policy in-process.
        # Any extra params the gateway doesn't understand (e.g. example-only
        # fields like "industry") are passed through to the Rego input so the
        # policy can use them without the gateway knowing their meaning.
        known_keys = {"tool", "args", "output", "session_id", "context", "principal"}
        extra = {k: v for k, v in params.items() if k not in known_keys}
        start = time.perf_counter()
        decision = self._engine.evaluate(tool, args, context, principal, extra=extra or None)
        self._config.metrics_sink.incr("decisions", tool=tool)
        self._config.metrics_sink.gauge(
            "policy_eval_ms", (time.perf_counter() - start) * 1000.0, tool=tool
        )

        # Audit allow AND deny (N3: 100% of calls logged).
        self._record_audit(tool, args, principal, context, decision, trace_id)

        if not decision.allow:
            self._config.metrics_sink.incr("denials", tool=tool)
            return self._error(
                request_id,
                CODE_TOOL_DENIED,
                "Tool dispatch denied",
                data={"reason": decision.reason, "trace_id": trace_id},
            )

        # 4b. ALLOW: apply G redaction to any supplied output.
        result: dict[str, Any] = {
            "allowed": True,
            "action": {"tool": tool, "args": args},
        }
        if output is not None:
            result["redacted_output"] = sanitize_output(output, decision.redact)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    # -- helpers ------------------------------------------------------------
    def _validate_params(self, params: Any) -> dict[str, Any] | None:
        if not isinstance(params, dict):
            return {"error": "params must be an object"}
        if "tool" not in params or not isinstance(params["tool"], str) or not params["tool"]:
            return {"error": "'tool' is required and must be a non-empty string"}
        args = params.get("args")
        if args is not None and not isinstance(args, dict):
            return {"error": "'args' must be an object"}
        for key in ("context", "output"):
            if params.get(key) is not None and not isinstance(params[key], dict):
                return {"error": f"'{key}' must be an object"}
        return None

    def _resolve_principal(self, tool: str, params: Mapping[str, Any]) -> dict[str, Any]:
        # Allow server-side injection of a pre-resolved principal (the Lambda
        # handler injects the IAM identity before calling handle()). This takes
        # precedence so the gateway never trusts a caller-supplied identity.
        resolved = params.get("principal")
        if isinstance(resolved, dict) and resolved.get("authenticated"):
            return dict(resolved)
        if self._config.enrich_principal is not None:
            return dict(self._config.enrich_principal(tool, params) or {})
        # Default local posture: a server-side service identity, authenticated.
        return {"id": "svc-agent", "authenticated": True}

    def _record_audit(
        self, tool, args, principal, context, decision: Decision, trace_id: str
    ) -> None:
        record = build_audit_record(
            tool=tool,
            args=args,
            principal=principal,
            context=context,
            decision={
                "allow": decision.allow,
                "reason": decision.reason,
                "redact": decision.redact,
            },
            policy_version=self._config.policy_version,
            reason=decision.reason,
            trace_id=trace_id,
        )
        self._config.audit_sink.record(record)

    @staticmethod
    def _trace_id(traceparent: str | None) -> str:
        if traceparent:
            match = _TRACEPARENT_RE.match(traceparent.strip())
            if match:
                return match.group(1)  # the 32-hex trace id
        return uuid.uuid4().hex

    @staticmethod
    def _error(
        request_id, code: int, message: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}
