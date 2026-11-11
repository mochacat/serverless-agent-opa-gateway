#!/usr/bin/env python3
"""In-process policy load smoke (CI layer 3, REQUIREMENTS.md S5.6).

Loads policies through the **real PolicyEngine** — the exact same
``src.engine.policy_engine`` module the Lambda imports at cold start —
and evaluates a fixture decision per example policy surface. Asserts the
deployed evaluation path parses the Rego and returns a well-formed decision.

This is a SMOKE check (proves the runtime loads + evaluates), not the full
semantic test suite — `opa test policies/` (T1) owns semantic correctness.

Usage: python3 examples/verify/policy_smoke.py [policies_dir]
Exits non-zero on any failure (gate-keeping for CI).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python examples/verify/policy_smoke.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.engine.policy_engine import PolicyEngine


def main() -> int:
    # Use the supplied dir or default to the repo-relative path.
    policies_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("policies")
    engine = PolicyEngine(policies_dir)

    failures: list[str] = []

    def check(name: str, decision, expect_allow: bool) -> None:
        if decision.allow is not expect_allow:
            failures.append(
                f"{name}: expected allow={expect_allow}, got {decision.allow}"
                f" (reason: {decision.reason})"
            )
        else:
            print(f"  ok  {name}: allow={decision.allow} :: {decision.reason}")

    P = {"id": "svc", "authenticated": True}

    def with_industry(name: str) -> dict:
        """Example-only dispatch key passed via ``extra`` (see policy.rego)."""
        return {"industry": name}

    # Baseline per-example decisions (benign allow + a blast-radius deny).
    check(
        "healthcare/read-allowed",
        engine.evaluate(
            "get_lab_results",
            {},
            {"target": {"leased": False}},
            P,
            extra=with_industry("healthcare"),
        ),
        True,
    )
    check(
        "healthcare/phi-catchall-denied",
        engine.evaluate(
            "query_phi",
            {"scope": "all"},
            {},
            {"id": "svc", "authenticated": True, "authorized_scopes": "all"},
            extra=with_industry("healthcare"),
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
        engine.evaluate(
            "execute_transfer",
            {"amount": 100, "beneficiary": "payroll"},
            approved,
            P,
            extra=with_industry("fintech"),
        ),
        True,
    )
    check(
        "fintech/transfer-no-ticket-denied",
        engine.evaluate(
            "execute_transfer",
            {"amount": 100, "beneficiary": "payroll"},
            {
                "target": {"isolated": True, "leased": False},
                "rate_window": {"mutations": 0, "max": 3},
                "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}],
            },
            P,
            extra=with_industry("fintech"),
        ),
        False,
    )

    check(
        "secops/revoke-allowed",
        engine.evaluate(
            "revoke_token", {"target": "10.0.0.5"}, approved, P, extra=with_industry("secops")
        ),
        True,
    )
    check(
        "secops/wildcard-target-denied",
        engine.evaluate(
            "revoke_token", {"target": "*"}, approved, P, extra=with_industry("secops")
        ),
        False,
    )

    # Fail-closed: unknown / empty example key must NOT produce allow=true.
    check(
        "failclosed/unknown-industry",
        engine.evaluate("get_balance", {}, {}, P, extra={"industry": "gaming"}),
        False,
    )
    check(
        "failclosed/empty-industry",
        engine.evaluate("get_balance", {}, {}, P, extra={"industry": ""}),
        False,
    )

    if failures:
        print("\nSMOKE FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nSMOKE PASS: all decisions correct via real PolicyEngine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
