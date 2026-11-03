"""Session store unit tests (TASKS T2.3) — the D/E state seam.

Asserts the in-memory store's append/read behavior and that the enrichment
helper layers trajectory + rate state onto a request context the exactly way
the gateway needs — always behind the injected protocol, never touching OPA.
"""

from __future__ import annotations

from src.engine.policy_engine import PolicyEngine
from src.engine.session_store import (
    InMemorySessionStore,
    enrich_context_with_session,
)

PRINCIPAL = {"id": "svc", "authenticated": True}


def test_append_then_read_returns_history_in_order() -> None:
    store = InMemorySessionStore()
    store.record_event("s1", "describe_cluster", at=1.0)
    store.record_event("s1", "dry_run", at=2.0)
    history = store.trajectory_for("s1")
    assert [h["tool"] for h in history] == ["describe_cluster", "dry_run"]
    assert history[0]["at"] == 1.0


def test_sessions_are_isolated() -> None:
    store = InMemorySessionStore()
    store.record_event("s1", "block_ip")
    assert store.trajectory_for("s2") == []
    assert store.trajectory_for("s1") != []


def test_record_event_defaults_timestamp() -> None:
    store = InMemorySessionStore()
    store.record_event("s1", "revoke_token")
    assert store.trajectory_for("s1")[0]["at"] is not None


def test_default_rate_config_and_override() -> None:
    store = InMemorySessionStore(rate_max_default=3)
    assert store.rate_config_for("s1") == {"max": 3}
    store.set_rate_max("s1", 1)
    assert store.rate_config_for("s1") == {"max": 1}
    assert store.rate_config_for("other") == {"max": 3}


def test_enrich_does_not_mutate_caller_context() -> None:
    store = InMemorySessionStore()
    original = {"target": {"leased": False}}
    enriched = enrich_context_with_session(original, store, "s1")
    assert original == {"target": {"leased": False}}  # untouched
    assert enriched["trajectory"] == []
    assert enriched["rate_window"] == {"max": 3}


def test_enrich_does_not_overwrite_explicit_context_keys() -> None:
    """Caller-supplied trajectory/rate must win so the gateway can pin them."""
    store = InMemorySessionStore()
    store.record_event("s1", "describe_cluster")
    pinned = {
        "trajectory": [{"tool": "pinned_precheck", "at": 0.0}],
        "rate_window": {"max": 9},
    }
    enriched = enrich_context_with_session(pinned, store, "s1")
    assert enriched["trajectory"] == [{"tool": "pinned_precheck", "at": 0.0}]
    assert enriched["rate_window"] == {"max": 9}


def test_store_drives_engine_trajectory_and_rate_features() -> None:
    """End-to-end: recorded history through the store feeds features D + E."""
    engine = PolicyEngine()
    store = InMemorySessionStore(rate_max_default=1)

    # First destructive call: no trajectory yet → D denies (look-before-you-leap).
    # Supply an isolated/unleased + approved target so C and F pass and D is
    # the specific guard that fails.
    pre_context = {
        "target": {"isolated": True, "leased": False},
        "approval": {"reference": "CHG-42", "approved": True},
    }
    d = engine.evaluate(
        "secops",
        "revoke_token",
        {"target": "10.0.0.5"},
        enrich_context_with_session(pre_context, store, "s1"),
        PRINCIPAL,
    )
    assert d.allow is False
    assert "trajectory" in d.reason

    # Agent performs the pre-checks; record them through the store.
    for tool in ("describe_cluster", "dry_run"):
        store.record_event("s1", tool)

    # Now the destructive op with a real target is allowed. The store supplies
    # the trajectory (now containing describe + dry_run) => feature D passes.
    d = engine.evaluate(
        "secops",
        "revoke_token",
        {"target": "10.0.0.5"},
        enrich_context_with_session(
            {
                "target": {"isolated": True, "leased": False},
                "approval": {"reference": "CHG-42", "approved": True},
            },
            store,
            "s1",
        ),
        PRINCIPAL,
    )
    assert d.allow is True

    # Two more destructive dispatches committed to the session consume the
    # rate budget (max=1), so the next one must be denied by feature E.
    store.record_event("s1", "revoke_token")  # 1st destructive in window
    store.record_event("s1", "revoke_token")  # 2nd destructive in window
    d2 = engine.evaluate(
        "secops",
        "block_ip",
        {"target": "10.0.0.6"},
        enrich_context_with_session(
            {
                "target": {"isolated": True, "leased": False},
                "approval": {"reference": "CHG-42", "approved": True},
            },
            store,
            "s1",
        ),
        PRINCIPAL,
    )
    assert d2.allow is False
    assert "rate limit" in d2.reason
