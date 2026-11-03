"""Gateway app unit tests (TASKS T3.4) — schema, enrichment, allow/deny, audit.

Exercises :class:`GatewayApp` directly (no HTTP): feeds it raw JSON-RPC bodies
and asserts the response envelope plus the injected audit/metric sink contents.
Runs against the REAL in-process Rego engine — only the sinks are fakes (O5).
"""

from __future__ import annotations

import json

import pytest

from src.audit.audit import InMemoryAuditSink
from src.engine.policy_engine import PolicyEngine
from src.gateway.app import (
    CODE_INVALID_PARAMS,
    CODE_INVALID_REQUEST,
    CODE_METHOD_NOT_FOUND,
    CODE_PARSE_ERROR,
    CODE_TOOL_DENIED,
    GatewayApp,
    GatewayConfig,
)
from src.metrics.metrics import InMemoryMetricsSink

APPROVED_CTX = {
    "target": {"isolated": True, "leased": False},
    "approval": {"reference": "CHG-42", "approved": True},
    "rate_window": {"max": 3},
    "trajectory": [
        {"tool": "describe_cluster", "at": 1.0},
        {"tool": "dry_run", "at": 2.0},
    ],
}


@pytest.fixture()
def app() -> tuple[GatewayApp, InMemoryAuditSink, InMemoryMetricsSink]:
    audit = InMemoryAuditSink()
    metrics = InMemoryMetricsSink()
    app = GatewayApp(
        engine=PolicyEngine(),
        config=GatewayConfig(audit_sink=audit, metrics_sink=metrics),
    )
    return app, audit, metrics


def _call(
    app: GatewayApp,
    *,
    tool: str,
    industry: str,
    args=None,
    context=None,
    output=None,
    session_id=None,
    id=1,
    traceparent=None,
) -> dict:
    params: dict = {"tool": tool, "industry": industry}
    if args is not None:
        params["args"] = args
    if context is not None:
        params["context"] = context
    if output is not None:
        params["output"] = output
    if session_id is not None:
        params["session_id"] = session_id
    payload = {"jsonrpc": "2.0", "id": id, "method": "tools/call", "params": params}
    return app.handle(json.dumps(payload), traceparent)


# ---------------------------------------------------------------------------
# Schema validation / JSON-RPC envelope
# ---------------------------------------------------------------------------
def test_malformed_body_is_parse_error(app) -> None:
    app, _, _ = app
    response = app.handle("{not json")
    assert response["error"]["code"] == CODE_PARSE_ERROR
    assert "trace_id" in response["error"]["data"]


def test_body_missing_jsonrpc_is_invalid_request(app) -> None:
    app, _, _ = app
    response = app.handle(json.dumps({"id": 1, "method": "tools/call", "params": {}}))
    assert response["error"]["code"] == CODE_INVALID_REQUEST


def test_unknown_method_is_method_not_found(app) -> None:
    app, _, _ = app
    response = app.handle(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/nope", "params": {}})
    )
    assert response["error"]["code"] == CODE_METHOD_NOT_FOUND


def test_missing_tool_is_invalid_params(app) -> None:
    app, _, _ = app
    response = app.handle(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"industry": "healthcare"},
            }
        )
    )
    assert response["error"]["code"] == CODE_INVALID_PARAMS
    assert "'tool'" in response["error"]["data"]["error"]


def test_non_object_args_is_invalid_params(app) -> None:
    app, _, _ = app
    response = app.handle(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"tool": "get_balance", "args": "oops"},
            }
        )
    )
    assert response["error"]["code"] == CODE_INVALID_PARAMS


def test_parse_error_still_emits_no_audit(app) -> None:
    _, audit, _ = app
    # A malformed envelope never reaches evaluation, so no decision is audited.
    assert len(audit.records) == 0


# ---------------------------------------------------------------------------
# Allow / deny
# ---------------------------------------------------------------------------
def test_allow_returns_action_and_allowed(app) -> None:
    app, _, _ = app
    response = _call(app, tool="get_balance", industry="fintech", id=7)
    assert response["result"]["allowed"] is True
    assert response["result"]["action"] == {"tool": "get_balance", "args": {}}
    assert "error" not in response
    assert response["id"] == 7  # JSON-RPC id is echoed from the envelope


def test_deny_echoes_request_id(app) -> None:
    app, _, _ = app
    response = _call(
        app,
        tool="execute_transfer",
        industry="fintech",
        args={"amount": 100, "beneficiary": "payroll"},
        id="abc",
    )
    assert response["id"] == "abc"


def test_deny_returns_structured_error(app) -> None:
    app, _, _ = app
    # Destructive transfer without a change ticket → deny (feature F).
    response = _call(
        app,
        tool="execute_transfer",
        industry="fintech",
        args={"amount": 100, "beneficiary": "payroll"},
    )
    assert response["error"]["code"] == CODE_TOOL_DENIED
    assert "change ticket" in response["error"]["data"]["reason"]


def test_unknown_industry_fails_closed(app) -> None:
    app, _, _ = app
    response = _call(app, tool="get_balance", industry="gaming")
    assert response["error"]["code"] == CODE_TOOL_DENIED


# ---------------------------------------------------------------------------
# Identity enrichment
# ---------------------------------------------------------------------------
def test_enrich_principal_is_injected_and_audited(app) -> None:
    """The agent's caller identity is resolved server-side (no attacker creds)."""

    def enrich(tool: str, params) -> dict:
        assert "authenticated" not in params  # agent never self-declares auth
        return {"id": "iam:service-role", "authenticated": True, "roles": ["ops"]}

    audit = InMemoryAuditSink()
    app = GatewayApp(
        engine=PolicyEngine(),
        config=GatewayConfig(audit_sink=audit, enrich_principal=enrich),
    )
    _call(app, tool="get_balance", industry="fintech")
    assert audit.records[0]["principal"] == {
        "id": "iam:service-role",
        "authenticated": True,
        "roles": ["ops"],
    }


def test_default_principal_is_autonomous_service_identity(app) -> None:
    app, audit, _ = app
    _call(app, tool="get_balance", industry="fintech")
    assert audit.records[0]["principal"]["authenticated"] is True


# ---------------------------------------------------------------------------
# Audit record (N3: allow AND deny; every field present)
# ---------------------------------------------------------------------------
def test_allow_and_deny_are_both_audited(app) -> None:
    app, audit, _ = app
    _call(app, tool="get_balance", industry="fintech")  # allow
    _call(
        app,
        tool="execute_transfer",
        industry="fintech",
        args={"amount": 100, "beneficiary": "payroll"},
    )  # deny
    assert len(audit.records) == 2
    assert audit.records[0]["decision"]["allow"] is True
    assert audit.records[1]["decision"]["allow"] is False


def test_audit_record_has_all_required_fields(app) -> None:
    app, audit, _ = app
    traceparent = "00-90210abc1234567890abcdef12345678-0011223344556677-01"
    _call(app, tool="get_rate", industry="fintech", traceparent=traceparent)
    record = audit.records[0]
    for field in (
        "tool",
        "args",
        "principal",
        "context",
        "decision",
        "policy_version",
        "reason",
        "trace_id",
    ):
        assert field in record, f"missing audit field: {field}"
    assert record["tool"] == "get_rate"
    assert record["trace_id"] == "90210abc1234567890abcdef12345678"  # from traceparent


def test_audit_trace_id_generated_when_no_traceparent(app) -> None:
    app, audit, _ = app
    _call(app, tool="get_rate", industry="fintech")
    assert len(audit.records[0]["trace_id"]) == 32  # a fresh uuid4 hex


# ---------------------------------------------------------------------------
# G — output redaction
# ---------------------------------------------------------------------------
def test_redaction_strips_phi_fields_from_allowed_output(app) -> None:
    app, _, _ = app
    output = {"patient_mrn": "MRN-1234", "value": 7, "ssn": "111-22-3333", "ok": "y"}
    response = _call(app, tool="get_lab_results", industry="healthcare", output=output)
    redacted = response["result"]["redacted_output"]
    assert redacted["patient_mrn"] == "REDACTED"
    assert redacted["ssn"] == "REDACTED"
    assert redacted["ok"] == "y"
    assert redacted["value"] == 7


def test_no_redaction_when_output_absent(app) -> None:
    app, _, _ = app
    response = _call(app, tool="get_lab_results", industry="healthcare")
    assert "redacted_output" not in response["result"]


def test_non_sensitive_output_unchanged(app) -> None:
    app, _, _ = app
    # get_rate is a fintech read with a redact list, but a benign output has
    # nothing to strip — only listed fields are masked.
    response = _call(
        app, tool="get_rate", industry="fintech", output={"rate": 1.2, "currency": "USD"}
    )
    assert response["result"]["redacted_output"] == {"rate": 1.2, "currency": "USD"}


# ---------------------------------------------------------------------------
# Metrics (injected sink)
# ---------------------------------------------------------------------------
def test_metrics_count_decisions_and_denials(app) -> None:
    app, _, metrics = app
    _call(app, tool="get_balance", industry="fintech")  # allow
    _call(
        app,
        tool="execute_transfer",
        industry="fintech",
        args={"amount": 100, "beneficiary": "payroll"},
    )  # deny
    assert metrics.counter("decisions") == 2
    assert metrics.counter("denials") == 1


def test_metrics_record_eval_latency(app) -> None:
    app, _, metrics = app
    _call(app, tool="get_balance", industry="fintech")
    samples = metrics.gauge_by_name("policy_eval_ms")
    assert samples, "expected a policy_eval_ms gauge"
    assert all(s >= 0 for s in samples)
