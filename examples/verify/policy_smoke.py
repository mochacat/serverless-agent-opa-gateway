#!/usr/bin/env python3
"""In-process policy load smoke (CI layer 3, REQUIREMENTS.md S5.6).

Loads every `.rego` in `policies/` with regopy — the same engine the Lambda
embeds — and evaluates a fixture decision per industry. Asserts the deployed
evaluation path parses the Rego and returns a well-formed decision.

This is a SMOKE check (proves the runtime loads + evaluates), not the full
semantic test suite — `opa test policies/` (T1) owns semantic correctness.

Usage: python3 examples/verify/policy_smoke.py [policies_dir]
Exits non-zero on any failure (gate-keeping for CI).
"""

from __future__ import annotations

import sys
from pathlib import Path

import regopy

POLICIES_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("policies")
REGO_FILES = ["base.rego", "healthcare.rego", "fintech.rego", "secops.rego", "policy.rego"]


def _load() -> regopy.Interpreter:
    interp = regopy.Interpreter()
    for name in REGO_FILES:
        path = POLICIES_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"expected policy file missing: {path}")
        interp.add_module(name, path.read_text())
    return interp


def _decide(
    interp: regopy.Interpreter, industry: str, tool: str, args: dict, principal: dict, ctx: dict
) -> dict | None:
    interp.set_input(
        {
            "industry": industry,
            "tool": tool,
            "args": args,
            "principal": principal,
            "context": ctx,
        }
    )
    results = interp.query("data.ai.guardrails.decision").results
    if not results:
        return None
    return results[0][0]


def main() -> int:
    interp = _load()
    failures: list[str] = []

    def check(name: str, decision: dict | None, expect_allow: bool) -> None:
        if decision is None:
            failures.append(f"{name}: decision is UNDEFINED (fail-open risk)")
        elif decision.get("allow") is not expect_allow:
            failures.append(
                f"{name}: expected allow={expect_allow}, got {decision.get('allow')}"
                f" (reason: {decision.get('reason')})"
            )
        else:
            print(f"  ok  {name}: allow={decision.get('allow')} :: {decision.get('reason')}")

    P = {"id": "svc", "authenticated": True}

    # Baseline per-industry decisions (benign allow + a blast-radius deny).
    check(
        "healthcare/read-allowed",
        _decide(interp, "healthcare", "get_lab_results", {}, P, {"target": {"leased": False}}),
        True,
    )
    check(
        "healthcare/phi-catchall-denied",
        _decide(
            interp,
            "healthcare",
            "query_phi",
            {"scope": "all"},
            {"id": "svc", "authenticated": True, "authorized_scopes": "all"},
            {},
        ),
        False,
    )

    approved = {
        "target": {"isolated": True, "leased": False},
        "approval": {"reference": "CHG-1", "approved": True},
        "rate_window": {"mutations": 0, "max": 3},
        "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}],
    }
    check(
        "fintech/transfer-allowed",
        _decide(
            interp,
            "fintech",
            "execute_transfer",
            {"amount": 100, "beneficiary": "payroll"},
            P,
            approved,
        ),
        True,
    )
    check(
        "fintech/transfer-no-ticket-denied",
        _decide(
            interp,
            "fintech",
            "execute_transfer",
            {"amount": 100, "beneficiary": "payroll"},
            P,
            {
                "target": {"isolated": True, "leased": False},
                "rate_window": {"mutations": 0, "max": 3},
                "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}],
            },
        ),
        False,
    )

    check(
        "secops/revoke-allowed",
        _decide(interp, "secops", "revoke_token", {"target": "10.0.0.5"}, P, approved),
        True,
    )
    check(
        "secops/wildcard-target-denied",
        _decide(interp, "secops", "revoke_token", {"target": "*"}, P, approved),
        False,
    )

    # Fail-closed: unknown / empty industry must NOT produce allow=true.
    check("failclosed/unknown-industry", _decide(interp, "gaming", "get_balance", {}, P, {}), False)
    check("failclosed/empty-industry", _decide(interp, "", "get_balance", {}, P, {}), False)

    if failures:
        print("\nSMOKE FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nSMOKE PASS: {len(REGO_FILES)} policy files loaded via regopy; all decisions correct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
