"""AWS Lambda entrypoint — API Gateway (HTTP API, payload v2.0) → gateway app.

A thin adapter (REQUIREMENTS §4.5, DESIGN §4.5): it maps an API Gateway event
dict onto the very same transport-agnostic :class:`GatewayApp` the local server
drives, so **the deployed Lambda runs the same code as `make run-gateway`**.

Cold start (NFR2): the engine + Rego are loaded once at module import and
reused across warm invocations — this module only wires the request shape.

The real Lambda is a deploy artifact, not a test dependency: tests feed this a
faithful mock API Gateway event and assert the response (zero AWS).
"""

from __future__ import annotations

import base64
import json
from typing import Any

from src.audit.audit import StdoutAuditSink
from src.gateway.app import GatewayApp, GatewayConfig
from src.metrics.metrics import InMemoryMetricsSink

# Module-level app: constructed once per Lambda execution environment. The
# engine inside loads policies once (cold start); warm invocations reuse it.
_app: GatewayApp | None = None


def _get_app() -> GatewayApp:
    global _app
    if _app is None:
        # In the deployed runtime the audit sink writes JSON lines that
        # CloudWatch Logs collects (the OpenTofu module ships a log group with
        # retention). Metrics default to in-memory; an OTLP exporter is a
        # drop-in MetricsSink configured per environment.
        _app = GatewayApp(
            config=GatewayConfig(
                audit_sink=StdoutAuditSink(),
                metrics_sink=InMemoryMetricsSink(),
            )
        )
    return _app


def _response(status: int, payload: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
        "isBase64Encoded": False,
    }


def _extract_body(event: dict[str, Any]) -> str | bytes | None:
    body = event.get("body")
    if body is None:
        return None
    if event.get("isBase64Encoded"):
        try:
            return base64.b64decode(body)
        except (ValueError, TypeError):
            return None
    return body


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Lambda handler: adapt API Gateway HTTP-API v2 event → gateway → response.

    ``context`` (the Lambda runtime context) is accepted per the AWS contract
    but deliberately unused — the decision is a pure function of the request.
    """
    request_context = event.get("requestContext", {}) or {}
    http = request_context.get("http", {}) or {}
    method = (http.get("method") or event.get("httpMethod") or "POST").upper()
    path = event.get("rawPath") or event.get("path") or "/"

    # Health probe (API Gateway may route GET /health here too).
    if path in ("/health", "/healthz") and method == "GET":
        return _response(200, {"status": "ok"})

    if method != "POST" or path != "/tools/call":
        return _response(404, {"error": "not found"})

    body = _extract_body(event)
    if body is None:
        return _response(
            400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        )

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    traceparent = headers.get("traceparent")

    response = _get_app().handle(body, traceparent)
    # JSON-RPC over HTTP: a well-formed request (allow OR deny) is HTTP 200;
    # the JSON-RPC envelope carries the structured decision/denial.
    return _response(200, response)
