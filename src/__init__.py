"""Serverless agent gateway — in-process OPA Rego authorization for tool dispatch.

The package root. Subpackages:
- :mod:`src.engine`  — the ``PolicyEngine`` (regopy) + swappable session store.
- :mod:`src.gateway` — the JSON-RPC ``tools/call`` gate, redaction and HTTP server.
- :mod:`src.audit`   — the audit-record builder + injectable sinks.
- :mod:`src.metrics` — injected decision counters (O5).
- :mod:`handler` — the AWS Lambda entrypoint (API Gateway HTTP API → app).
"""
