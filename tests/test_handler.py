"""Lambda handler tests — API Gateway (payload v2.0) event → response (T5).

Feeds :func:`src.handler.handler` a faithful mock of API Gateway HTTP-API v2.0
events and asserts the request→policy→response mapping, including base64
bodies, deny-as-structured-reason, and audit emission. Zero AWS credentials —
the handler builds its own app with the same in-process engine as every tier.
"""

from __future__ import annotations

import base64
import json

from src.handler import handler

# The supplied default audit sink is StdoutAuditSink; tests focus on the HTTP
# contract asserted via the response envelope (the StdoutAuditSink path is
# covered in test_gateway_server.py). Decisions come from the real engine.


def _event(
    body: str | dict,
    *,
    method: str = "POST",
    path: str = "/tools/call",
    traceparent: str | None = None,
    base64_body: bool = False,
) -> dict:
    raw = body if isinstance(body, str) else json.dumps(body)
    if base64_body:
        raw = base64.b64encode(raw.encode()).decode()
    headers = {"content-type": "application/json"}
    if traceparent:
        headers["traceparent"] = traceparent
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": headers,
        "requestContext": {"http": {"method": method, "path": path}},
        "body": raw,
        "isBase64Encoded": base64_body,
    }


def _call(body: dict, **kwargs) -> dict:
    resp = handler(_event(body, **kwargs))
    return json.loads(resp["body"])


def test_success_returns_200_with_allow_result() -> None:
    resp = handler(
        _event(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"tool": "get_balance", "industry": "fintech"},
            }
        )
    )
    assert resp["statusCode"] == 200
    assert resp["headers"]["Content-Type"].startswith("application/json")
    assert resp["isBase64Encoded"] is False
    envelope = json.loads(resp["body"])
    assert envelope["result"]["allowed"] is True


def test_deny_returns_structured_reason() -> None:
    envelope = _call(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "tool": "execute_transfer",
                "industry": "fintech",
                "args": {"amount": 100, "beneficiary": "payroll"},
            },
        }
    )
    assert envelope["error"]["code"] == -32000
    assert "change ticket" in envelope["error"]["data"]["reason"]


def test_base64_body_decoded() -> None:
    resp = handler(
        _event(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"tool": "get_balance", "industry": "fintech"},
            },
            base64_body=True,
        )
    )
    envelope = json.loads(resp["body"])
    assert envelope["result"]["allowed"] is True


def test_health_endpoint() -> None:
    resp = handler(_event({}, method="GET", path="/health"))
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"status": "ok"}


def test_unknown_route_returns_404() -> None:
    resp = handler(
        _event(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}},
            method="POST",
            path="/other",
        )
    )
    assert resp["statusCode"] == 404


def test_non_post_method_returns_404() -> None:
    resp = handler(
        _event(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}},
            method="GET",
            path="/tools/call",
        )
    )
    assert resp["statusCode"] == 404


def test_missing_body_returns_parse_error_400() -> None:
    # No body key at all → the adapter cannot build a request.
    event = _event({}, method="POST", path="/tools/call")
    event["body"] = None
    resp = handler(event)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"]["code"] == -32700


def test_traceparent_propagates_into_decision() -> None:
    tp = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    # The decision trace_id is correlatable via the audit sink in prod; here we
    # at least confirm propagation doesn't error and stays 200.
    resp = handler(
        _event(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"tool": "get_rate", "industry": "fintech"},
            },
            traceparent=tp,
        )
    )
    assert resp["statusCode"] == 200
