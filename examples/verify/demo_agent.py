#!/usr/bin/env python3
"""Local demo — shows the gateway deciding safe vs unsafe tool calls.

Run against the local server (``make run-gateway`` in another terminal), then
this script sends a series of tool-call requests. For each you'll see:

  TOOL           -> ALLOW / DENY [+ reason]

Uses plain HTTP (``requests``) — no LangChain, no agent framework. The gateway
itself is the product.

Usage:  python3 examples/verify/demo_agent.py [--url http://127.0.0.1:8080]
"""

from __future__ import annotations

import json
import sys
from urllib.request import Request, urlopen


def main(gw_url: str) -> None:
    calls = [
        ("safe read", "fintech", "get_balance", {}),
        ("PHI query — over-broad scope denied (B)", "healthcare", "query_phi", {"scope": "all"}),
        (
            "PHI query — permitted scope allowed + output redacted (B+G)",
            "healthcare",
            "query_phi",
            {
                "scope": "diagnosis",
                "_principal": "clinician",
                "_mock_output": {
                    "patient_mrn": "MRN-1",
                    "ssn": "[SSN]",
                    "diagnosis_code": "E11.9",
                    "note": "follow-up",
                },
            },
        ),
        (
            "money transfer — no change ticket denied (F)",
            "fintech",
            "execute_transfer",
            {"amount": 100, "beneficiary": "payroll"},
        ),
        (
            "money transfer — amount over cap denied (B)",
            "fintech",
            "execute_transfer",
            {"amount": 999999, "beneficiary": "payroll"},
        ),
        (
            "block IP — shell injection in target denied (B)",
            "secops",
            "block_ip",
            {"target": "*; rm -rf /"},
        ),
        (
            "block IP — valid IP with all conditions allowed",
            "secops",
            "block_ip",
            {
                "target": "10.0.0.55",
                "_context": {
                    "target": {"isolated": True, "leased": False},
                    "approval": {"reference": "CHG-42", "approved": True},
                    "rate_window": {"max": 3},
                    "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}],
                },
            },
        ),
        (
            "delete patient record — no trajectory denied (D)",
            "healthcare",
            "delete_patient_record",
            {
                "_context": {
                    "target": {"isolated": True, "leased": False},
                    "approval": {"reference": "CHG-1", "approved": True},
                    "rate_window": {"max": 3},
                    "trajectory": [],
                }
            },
        ),
        ("unknown industry -> fail-closed", "gaming", "get_balance", {}),
    ]

    for label, industry, tool, extras in calls:
        context = extras.pop("_context", {})
        mock_output = extras.pop("_mock_output", None)
        # Default to an authenticated operator service identity; a call that
        # needs extra scopes (clinician -> PHI) supplies its own _principal.
        principal_id = extras.pop("_principal", "ops")
        params = {"tool": tool, "industry": industry, "args": extras}
        if context:
            params["context"] = context
        if mock_output:
            params["output"] = mock_output
        if principal_id:
            params["principal_id"] = principal_id

        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
        resp = _post(gw_url, payload)

        if "result" in resp:
            r = resp["result"]
            flag = "ALLOW"
            extra = ""
            if r.get("redacted_output"):
                extra = f"  (redacted: {r['redacted_output']})"
            print(f"  {flag}  {label}{extra}")
        else:
            reason = (
                resp.get("error", {})
                .get("data", {})
                .get("reason", resp.get("error", {}).get("message", "?"))
            )
            print(f"  DENY  {label}")
            print(f"         -> {reason}")

    print("\n  Done — the gateway decided all calls locally, no AWS involved.")


def _post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = Request(
        f"{url}/tools/call", data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urlopen(req) as resp:
        return json.loads(resp.read())


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
    print(f"\n  agent gateway demo — POST /tools/call {url}\n")
    main(url)
