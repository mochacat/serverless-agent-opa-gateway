"""Local HTTP transport tests (TASKS T3.3) — stdlib server round-trip.

Starts the real :class:`ThreadingHTTPServer` on an ephemeral port in a
background thread and drives it with a real HTTP client, exercising the same
GatewayApp the Lambda handler uses. Zero AWS; the policy eval is the real one.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from src.audit.audit import InMemoryAuditSink, StdoutAuditSink
from src.engine.policy_engine import PolicyEngine
from src.gateway.app import GatewayApp, GatewayConfig
from src.gateway.server import _default_app, build_server
from src.metrics.metrics import InMemoryMetricsSink


@pytest.fixture()
def running_server():
    audit = InMemoryAuditSink()
    app = GatewayApp(
        engine=PolicyEngine(),
        config=GatewayConfig(
            audit_sink=audit,
            metrics_sink=InMemoryMetricsSink(),
        ),
    )
    server = build_server(app, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", audit
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(url: str, payload: dict, traceparent: str | None = None) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url + "/tools/call", data=data, method="POST")
    if traceparent:
        req.add_header("traceparent", traceparent)
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (local test server)
        return json.loads(resp.read())


def test_health_endpoint(running_server) -> None:
    base, _ = running_server
    with urllib.request.urlopen(base + "/health") as resp:  # noqa: S310
        assert json.loads(resp.read()) == {"status": "ok"}


def test_unknown_get_route_is_404(running_server) -> None:
    base, _ = running_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(base + "/nope")  # noqa: S310
    assert exc.value.code == 404


def test_post_unknown_route_is_404(running_server) -> None:
    base, _ = running_server
    req = urllib.request.Request(base + "/other", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)  # noqa: S310
    assert exc.value.code == 404


def test_tools_call_allow_over_http(running_server) -> None:
    base, audit = running_server
    resp = _post(
        base,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"tool": "get_balance", "industry": "fintech"},
        },
    )
    assert resp["result"]["allowed"] is True
    assert resp["id"] == 1
    assert len(audit) == 1


def test_tools_call_deny_over_http_with_traceparent(running_server) -> None:
    base, audit = running_server
    tp = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    resp = _post(
        base,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "tool": "execute_transfer",
                "industry": "fintech",
                "args": {"amount": 100, "beneficiary": "payroll"},
            },
        },
        traceparent=tp,
    )
    assert resp["error"]["code"] == -32000
    assert audit.records[0]["trace_id"] == "0123456789abcdef0123456789abcdef"


def test_stdout_audit_sink_writes_json_line() -> None:
    import io

    stream = io.StringIO()
    sink = StdoutAuditSink(stream=stream)
    sink.record({"tool": "get_balance", "decision": {"allow": True}})
    line = stream.getvalue().strip()
    assert json.loads(line)["tool"] == "get_balance"


def test_default_app_fails_closed_on_unknown_principal() -> None:
    """Unknown principal_id → anonymous + unauthenticated: PHI is denied."""
    app = _default_app()
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "tool": "query_phi",
            "industry": "healthcare",
            "args": {"scope": "diagnosis"},
            "principal_id": "mystery-user",
        },
    }
    resp = app.handle(json.dumps(body), None)
    assert resp["error"]["code"] == -32000
    assert "not authenticated" in resp["error"]["data"]["reason"]


def test_default_app_allows_clinician_phi_and_redacts() -> None:
    """The demo clinician holds the diagnosis scope → PHI allowed + redacted."""
    app = _default_app()
    body = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "tool": "query_phi",
            "industry": "healthcare",
            "args": {"scope": "diagnosis"},
            "principal_id": "clinician",
            "output": {
                "patient_mrn": "MRN-1",
                "ssn": "555",
                "diagnosis_code": "E11.9",
                "note": "follow-up",
            },
        },
    }
    resp = app.handle(json.dumps(body), None)
    result = resp["result"]
    assert result["allowed"] is True
    redacted = result["redacted_output"]
    assert redacted["patient_mrn"] == "REDACTED"
    assert redacted["ssn"] == "REDACTED"
    assert redacted["diagnosis_code"] == "E11.9"  # non-PHI passes through


def test_principal_store_env_override(monkeypatch) -> None:
    """PRINCIPAL_STORE (env var JSON) replaces the demo identities."""
    monkeypatch.setenv(
        "PRINCIPAL_STORE",
        json.dumps({"ro-bot": {"id": "ro-bot", "authenticated": True, "authorized_scopes": ""}}),
    )
    app = _default_app()
    body = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "tool": "get_balance",
            "industry": "fintech",
            "principal_id": "ro-bot",
        },
    }
    resp = app.handle(json.dumps(body), None)
    assert resp["result"]["allowed"] is True
    # The default `clinician` is gone under the override → fails closed.
    body2 = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "tool": "query_phi",
            "industry": "healthcare",
            "args": {"scope": "diagnosis"},
            "principal_id": "clinician",
        },
    }
    assert app.handle(json.dumps(body2), None)["error"]["code"] == -32000
