"""Engine unit tests (TASKS T2.4) — determinism, single-load, decision/reason.

Feeds the real in-process Rego engine (same policies the Lambda embeds —
``policies/*.rego`` loaded via regopy) and asserts the stable output contract.
No AWS, no live gateway: just engine → decision.
"""

from __future__ import annotations

import threading

import pytest

from src.engine.policy_engine import (
    FAIL_CLOSED_REASON,
    Decision,
    PolicyEngine,
    PolicyEngineError,
    get_engine,
)

# A fully-satisfied destructive context: approval (F) + isolated/unleased (C)
# + trajectory with describe+dry_run (D) + rate under budget (E).
APPROVED_CTX = {
    "target": {"isolated": True, "leased": False},
    "approval": {"reference": "CHG-42", "approved": True},
    "rate_window": {"max": 3},
    "trajectory": [
        {"tool": "describe_cluster", "at": 1.0},
        {"tool": "dry_run", "at": 2.0},
    ],
}

PRINCIPAL = {"id": "svc", "authenticated": True}


@pytest.fixture(scope="module")
def engine() -> PolicyEngine:
    """A single engine instance shared across the module (single-load focus)."""
    return PolicyEngine()


# ---------------------------------------------------------------------------
# Loading / determinism
# ---------------------------------------------------------------------------
def test_policy_files_load_zero_errors(engine: PolicyEngine) -> None:
    decisions = [
        getattr(engine, "_interp", None) is not None,
        engine is not None,
    ]
    assert all(decisions)


def test_same_input_same_decision_is_deterministic(engine: PolicyEngine) -> None:
    """NFR4: identical input must reproduce an identical decision, repeated."""
    kwargs = dict(
        tool="execute_transfer",
        args={"amount": 100, "beneficiary": "payroll"},
        context=APPROVED_CTX,
        principal=PRINCIPAL,
        extra={"industry": "fintech"},
    )
    first, second, third = (
        engine.evaluate(**kwargs),
        engine.evaluate(**kwargs),
        engine.evaluate(**kwargs),
    )
    assert first == second == third
    assert first.allow is True


def test_engine_is_reused_across_invocations() -> None:
    """The module-level singleton returns one interpreter — cold-start cost is
    paid once, warm invocations reuse it (NFR2)."""
    assert get_engine() is get_engine()


# ---------------------------------------------------------------------------
# Healthcare (B hero: PHI content-gating)
# ---------------------------------------------------------------------------
def test_healthcare_read_allowed(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        "get_lab_results",
        {},
        {"target": {"leased": False}},
        PRINCIPAL,
        extra={"industry": "healthcare"},
    )
    assert d.allow is True
    # G: these read tools redact PHI identifiers from output.
    assert d.redact == ("patient_mrn", "ssn", "patient_name", "dob", "email")


def test_healthcare_phi_catchall_scope_denied(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        "query_phi",
        {"scope": "all"},
        {},
        {"id": "svc", "authenticated": True, "authorized_scopes": "all"},
        extra={"industry": "healthcare"},
    )
    assert d.allow is False
    assert "PHI query scope" in d.reason


def test_healthcare_destructive_requires_approval(engine: PolicyEngine) -> None:
    no_ticket = {**APPROVED_CTX, "approval": {}}
    d = engine.evaluate(
        "delete_patient_record", {}, no_ticket, PRINCIPAL, extra={"industry": "healthcare"}
    )
    assert d.allow is False
    assert "change ticket" in d.reason


def test_healthcare_destructive_allowed_with_full_conditions(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        "bulk_export_records",
        {"dry_run": False},
        APPROVED_CTX,
        PRINCIPAL,
        extra={"industry": "healthcare"},
    )
    assert d.allow is True


# ---------------------------------------------------------------------------
# Fintech (F/E/B heroes: ticket, rate, amount+beneficiary)
# ---------------------------------------------------------------------------
def test_fintech_transfer_allowed(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        "execute_transfer",
        {"amount": 100, "beneficiary": "payroll"},
        APPROVED_CTX,
        PRINCIPAL,
        extra={"industry": "fintech"},
    )
    assert d.allow is True
    assert d.redact is None  # get_* reads redact; execute_transfer does not
    assert "approved by policy engine" in d.reason


def test_fintech_transfer_exceeds_amount_cap(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        "execute_transfer",
        {"amount": 999999, "beneficiary": "payroll"},
        APPROVED_CTX,
        PRINCIPAL,
        extra={"industry": "fintech"},
    )
    assert d.allow is False
    assert "amount exceeds the single-transfer cap" in d.reason  # B


def test_fintech_transfer_beneficiary_not_allowlisted(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        "execute_transfer",
        {"amount": 100, "beneficiary": "unknown_acct"},
        APPROVED_CTX,
        PRINCIPAL,
        extra={"industry": "fintech"},
    )
    assert d.allow is False
    assert "allowlist" in d.reason  # B


def test_fintech_reads_redact_sensitive_fields(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        "get_balance", {}, {"target": {"leased": False}}, PRINCIPAL, extra={"industry": "fintech"}
    )
    assert d.allow is True
    assert d.redact == ("account_number", "card_number", "ssn", "routing_number")


# ---------------------------------------------------------------------------
# Secops (B hero: hostname/IP target validation)
# ---------------------------------------------------------------------------
def test_secops_revoke_allowed_with_real_target(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        "revoke_token",
        {"target": "10.0.0.5"},
        APPROVED_CTX,
        PRINCIPAL,
        extra={"industry": "secops"},
    )
    assert d.allow is True


def test_secops_wildcard_target_denied(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        "block_ip", {"target": "*"}, APPROVED_CTX, PRINCIPAL, extra={"industry": "secops"}
    )
    assert d.allow is False
    assert "well-formed hostname" in d.reason  # B


def test_secops_rate_limit_denied_when_over_budget(engine: PolicyEngine) -> None:
    # Two prior destructive ops already consumed the window before this call.
    over_ctx = {
        **APPROVED_CTX,
        "rate_window": {"max": 1},
        "trajectory": [
            {"tool": "describe_cluster", "at": 1.0},
            {"tool": "dry_run", "at": 2.0},
            {"tool": "block_ip", "at": 3.0},
            {"tool": "revoke_token", "at": 4.0},
        ],
    }
    d = engine.evaluate(
        "kill_process", {"target": "db-01"}, over_ctx, PRINCIPAL, extra={"industry": "secops"}
    )
    assert d.allow is False
    assert "rate limit" in d.reason  # E


# ---------------------------------------------------------------------------
# Default-deny / fail-closed (H)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("industry", ["", "gaming", "unknown"])
def test_unknown_or_missing_industry_fails_closed(engine: PolicyEngine, industry: str) -> None:
    d = engine.evaluate("get_balance", {}, {}, PRINCIPAL, extra={"industry": industry})
    assert d.allow is False


def test_unknown_tool_fails_closed_even_for_known_industry(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        "run_arbitrary_command", {}, {}, PRINCIPAL, extra={"industry": "healthcare"}
    )
    assert d.allow is False
    assert "verb-class" in d.reason


def test_unauthenticated_principal_denied(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        "get_balance",
        {},
        {"target": {"leased": False}},
        {"id": "svc", "authenticated": False},
        extra={"industry": "fintech"},
    )
    assert d.allow is False
    assert "not authenticated" in d.reason


def test_fail_closed_helper_reason() -> None:
    d = Decision.fail_closed()
    assert d == Decision(allow=False, reason=FAIL_CLOSED_REASON)
    assert d.redact is None


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------
def test_missing_policy_dir_raises(tmp_path) -> None:
    empty = tmp_path / "nope"
    empty.mkdir()
    with pytest.raises(PolicyEngineError):
        PolicyEngine(empty)


def test_concurrent_evaluation_is_deterministic(engine: PolicyEngine) -> None:
    """The engine must be safe to call from concurrent gateway workers."""
    errors: list[Exception] = []
    results: list[Decision] = []

    def call() -> None:
        try:
            results.append(
                engine.evaluate(
                    "execute_transfer",
                    {"amount": 100, "beneficiary": "payroll"},
                    APPROVED_CTX,
                    PRINCIPAL,
                    extra={"industry": "fintech"},
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(results) == 12
    assert all(r == results[0] and r.allow for r in results)
