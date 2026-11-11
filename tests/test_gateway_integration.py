"""Gateway integration tests (TASKS T4.2) — safe vs unsafe, end to end.

Every case runs the REAL path: JSON-RPC request -> GatewayApp (schema-validate
-> server-side identity enrich -> **real in-process Rego eval via regopy** +
session store) -> allow/deny (+ G redaction) -> audit. Only cloud *services*
are substituted (in-memory audit/metric sinks, in-memory session store) — the
policy evaluation is the real thing (N6). Zero AWS credentials.

Fixtures live in ``tests/testdata/*.json`` (T4.1) and declare the request plus
the expected outcome. Per REQUIREMENTS §5.1 the hero capabilities (B/E/F/G)
carry the most and hardest scenarios.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.audit.audit import InMemoryAuditSink
from src.engine.policy_engine import PolicyEngine
from src.engine.session_store import InMemorySessionStore
from src.gateway.app import GatewayApp, GatewayConfig
from src.gateway.redact import REDACTED
from src.metrics.metrics import InMemoryMetricsSink

TESTDATA = Path(__file__).parent / "testdata"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
def _enrich_principal(tool: str, params: dict) -> dict:
    """Server-side identity enrichment. The agent contributes no credentials;
    a fixture's principal_hint is what the *gateway* would resolve from its own
    identity source (e.g. scoped token)."""
    principal = {"id": "svc-agent", "authenticated": True}
    principal.update(params.get("_principal_hint") or {})
    return principal


def _make_app() -> tuple[GatewayApp, InMemoryAuditSink, InMemoryMetricsSink, InMemorySessionStore]:
    audit = InMemoryAuditSink()
    metrics = InMemoryMetricsSink()
    store = InMemorySessionStore()
    app = GatewayApp(
        engine=PolicyEngine(),
        config=GatewayConfig(
            audit_sink=audit,
            metrics_sink=metrics,
            session_store=store,
            enrich_principal=_enrich_principal,
        ),
    )
    return app, audit, metrics, store


def _request_payload(request: dict[str, Any], principal_hint: dict | None) -> dict[str, Any]:
    params = {
        "tool": request["tool"],
        "industry": request["industry"],
        "args": request.get("args", {}),
        "context": request.get("context", {}),
    }
    if "output" in request:
        params["output"] = request["output"]
    if principal_hint:
        # Not sent by a real agent here — stands in for what the gateway
        # resolves server-side (kept out of the wire envelope in production).
        params["_principal_hint"] = principal_hint
    return {"jsonrpc": "2.0", "id": "it-1", "method": "tools/call", "params": params}


def _assert_outcome(response: dict[str, Any], expect: dict[str, Any], output: Any = None) -> None:
    if expect.get("allowed"):
        res = response.get("result")
        assert res is not None, f"expected allow, got: {response.get('error')}"
        assert res["allowed"] is True
        if "redacted_fields" in expect:
            redacted = res["redacted_output"]
            for field in expect["redacted_fields"]:
                assert redacted[field] == REDACTED, f"{field} not redacted"
            for field in expect.get("kept_fields", []):
                assert redacted[field] == output[field], f"{field} wrongly altered"
    else:
        err = response.get("error")
        assert err is not None, f"expected deny, got allow: {response.get('result')}"
        assert err["code"] == -32000
        if "reason_contains" in expect:
            assert expect["reason_contains"] in err["data"]["reason"], (
                f"reason {err['data']['reason']!r} missing {expect['reason_contains']!r}"
            )


# ---------------------------------------------------------------------------
# Simple single-shot fixtures
# ---------------------------------------------------------------------------
SIMPLE_FIXTURES = [
    "safe_read",
    "unapproved_destructive",
    "production_delete",
    "phi_query",
    "phi_redaction",
    "prompt_injection_coercion",
    "secops_target_injection",
    "rate_budget",
]


@pytest.mark.parametrize("fixture_name", SIMPLE_FIXTURES)
def test_fixture_end_to_end(fixture_name: str) -> None:
    fixture = json.loads((TESTDATA / f"{fixture_name}.json").read_text())
    app, audit, _metrics, _store = _make_app()

    payload = _request_payload(fixture["request"], fixture["request"].get("principal_hint"))
    response = app.handle(json.dumps(payload))

    _assert_outcome(response, fixture["expect"], output=fixture["request"].get("output"))

    # N3: every call — allow or deny — produced exactly one audit record.
    assert len(audit) == 1
    assert audit.records[0]["tool"] == fixture["request"]["tool"]


# ---------------------------------------------------------------------------
# Multi-step fixture: trajectory (D) enforced through the session store
# ---------------------------------------------------------------------------
def test_trajectory_sequence_enforced_through_store() -> None:
    fixture = json.loads((TESTDATA / "trajectory_sequence.json").read_text())
    app, audit, _metrics, store = _make_app()
    session_id = "sess-trajectory"

    for step in fixture["steps"]:
        for tool_name in step["record_events"]:
            store.record_event(session_id, tool_name)

        params = {
            "tool": fixture["tool"],
            "industry": fixture["industry"],
            "args": fixture["args"],
            "context": fixture["context"],
            "session_id": session_id,
        }
        payload = {"jsonrpc": "2.0", "id": "trj", "method": "tools/call", "params": params}
        response = app.handle(json.dumps(payload))
        _assert_outcome(response, step["expect"])

    # First attempt denied, second allowed → two audited decisions.
    assert len(audit) == 2
    assert audit.records[0]["decision"]["allow"] is False
    assert audit.records[1]["decision"]["allow"] is True


# ---------------------------------------------------------------------------
# Hero-heavy: argument-inspection (B) does not leak through as an allow
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "amount,beneficiary,reason_fragment",
    [
        (50000, "payroll", "amount"),  # over cap
        (100, "unknown_llc", "allowlist"),  # bad beneficiary
    ],
)
def test_fintech_argument_inspection_denies(
    amount: int, beneficiary: str, reason_fragment: str
) -> None:
    app, _audit, _metrics, _store = _make_app()
    payload = _request_payload(
        {
            "tool": "execute_transfer",
            "industry": "fintech",
            "args": {"amount": amount, "beneficiary": beneficiary},
            "context": {
                "target": {"isolated": True, "leased": False},
                "approval": {"reference": "CHG-1", "approved": True},
                "rate_window": {"max": 3},
                "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}],
            },
        },
        None,
    )
    response = app.handle(json.dumps(payload))
    assert response["error"]["code"] == -32000
    assert reason_fragment in response["error"]["data"]["reason"]


def test_fully_approved_transfer_is_allowed() -> None:
    """Contrast case: same hero F/B/E/D path, all guards satisfied → allow."""
    app, _audit, _metrics, _store = _make_app()
    payload = _request_payload(
        {
            "tool": "execute_transfer",
            "industry": "fintech",
            "args": {"amount": 100, "beneficiary": "payroll"},
            "context": {
                "target": {"isolated": True, "leased": False},
                "approval": {"reference": "CHG-1", "approved": True},
                "rate_window": {"max": 3},
                "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}],
            },
        },
        None,
    )
    response = app.handle(json.dumps(payload))
    assert response["result"]["allowed"] is True


def test_denied_call_is_still_audited_with_reason() -> None:
    app, audit, _metrics, _store = _make_app()
    payload = _request_payload(
        {
            "tool": "execute_transfer",
            "industry": "fintech",
            "args": {"amount": 100, "beneficiary": "payroll"},
            "context": {"target": {"isolated": True, "leased": False}},
        },
        None,
    )
    response = app.handle(json.dumps(payload))
    assert response["error"]["code"] == -32000
    record = audit.records[0]
    assert record["decision"]["allow"] is False
    assert "change ticket" in record["reason"]
    assert record["policy_version"] == "dev"
