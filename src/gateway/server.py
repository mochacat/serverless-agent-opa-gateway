"""Local HTTP server for the gateway — stdlib ``http.server`` only (O4/D7).

Serves the JSON-RPC ``tools/call`` endpoint over plain HTTP so the full
decision path can run as a local process with **zero AWS** (N6): an agent or a
``curl`` sends a request, the gateway schema-validates → enriches → runs the
real in-process Rego eval → returns allow/deny (with G redaction) and audits.

This is a thin transport: it reads the body + ``traceparent`` header, hands
them to :class:`GatewayApp`, and writes the response. The same
:class:`GatewayApp` is what the Lambda handler (T5) drives, so the local demo
is the same code that deploys.

The local server also demonstrates **server-side identity enrichment**: the
agent contributes no credentials; the gateway resolves the caller's identity
(and its least-privilege scopes) from its own store, keyed by an opaque
``principal_id`` the caller presents. ``make demo`` wires this up.

Usage: ``python3 -m src.gateway.server [--port 8080]``  (also ``make run-gateway``)
"""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.gateway.app import GatewayApp, GatewayConfig

# Demo identity store — a stand-in for a directory/KMS-backed lookup (the
# gateway, not the agent, decides what a caller may do). Local-only: this
# never ships to the Lambda (see src/handler.py, which wires no
# enrich_principal and relies on infra IAM instead).
#
# PRINCIPAL_STORE overrides the store from an env var (JSON) so a local run
# never needs credentials in source. Unknown ids fail closed (not
# authenticated): default-deny is the honest local behavior, exactly as OPA
# enforces it.
_DEMO_PRINCIPALS: dict[str, dict] = {
    # Normal operator: authenticated, no PHI scopes — most tool calls work,
    # healthcare PHI is refused unless the caller holds the clinical role.
    "ops": {"id": "ops", "authenticated": True, "authorized_scopes": ""},
    # Clinician: authenticated with the diagnosis PHI scope (hero B + G).
    "clinician": {"id": "clinician", "authenticated": True, "authorized_scopes": "diagnosis"},
}


def _load_principals() -> dict[str, dict]:
    """Identity store, overridable from ``PRINCIPAL_STORE`` (JSON, an env var)
    so secrets/scopes never need to live in source. Empty/invalid → the demo
    defaults; a truly strict deployment can point this at a token service."""
    raw = os.environ.get("PRINCIPAL_STORE")
    if not raw:
        return dict(_DEMO_PRINCIPALS)
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return dict(_DEMO_PRINCIPALS)
    if not isinstance(parsed, dict):
        return dict(_DEMO_PRINCIPALS)
    return parsed


HOST = os.environ.get("GATEWAY_HOST", "127.0.0.1")
PORT = int(os.environ.get("GATEWAY_PORT", "8080"))


class _GatewayHandler(BaseHTTPRequestHandler):
    """Routes ``POST /tools/call`` (+ health) to the shared gateway app."""

    # One app instance shared by every request on this server — the engine
    # inside serializes itself, and the session store holds per-session state.
    app: GatewayApp | None = None

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path in ("/health", "/healthz"):
            self._json({"status": "ok"}, 200)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path != "/tools/call":
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        traceparent = self.headers.get("traceparent")
        response = self.app.handle(raw, traceparent)
        self._json(response, 200)

    def _json(self, payload: dict, status: int) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        # Quiet by default; decisions are audited by the sink, not the HTTP log.
        import sys

        sys.stderr.write(f"[gateway] {self.address_string()} {fmt % args}\n")


def build_server(
    app: GatewayApp | None = None, host: str = HOST, port: int = PORT
) -> ThreadingHTTPServer:
    app = app or _default_app()
    handler = type("BoundHandler", (_GatewayHandler,), {"app": app})
    return ThreadingHTTPServer((host, port), handler)


def _default_app() -> GatewayApp:
    """Gateway with a server-side identity lookup wired in.

    ``principal_id`` arrives in the request params; the gateway maps it to a
    least-privilege identity from its own store (a directory / KMS / token
    service stand-in). An unknown or missing id **fails closed**: the caller
    is treated as anonymous and unauthenticated, so generic calls that need
    skill are denied and PHI stays gated on an explicit clinical role. The
    agent can never self-escalate.
    """

    principals = _load_principals()

    def enrich_principal(tool: str, params: dict) -> dict:
        pid = params.get("principal_id")
        resolved = principals.get(pid)
        if resolved is not None:
            return dict(resolved)
        return {"id": pid or "anonymous", "authenticated": False, "authorized_scopes": ""}

    return GatewayApp(config=GatewayConfig(enrich_principal=enrich_principal))


def main() -> None:
    parser = argparse.ArgumentParser(description="Local agent gateway (zero AWS)")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    server = build_server(port=args.port)
    print(f"🔐 agent gateway listening on http://{HOST}:{args.port}  (tools/call)")
    print("   try:  curl -X POST http://127.0.0.1:8080/tools/call -d '...'")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")


if __name__ == "__main__":
    main()
