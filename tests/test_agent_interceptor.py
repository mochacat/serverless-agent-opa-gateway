"""LangChain interceptor + gateway client tests (T5.1).

Covers the reference framework adapter:
  * :class:`OPAGuardrailTool` gates a real LangChain tool: ALLOW runs it, DENY
    returns the policy reason without executing, redaction replaces output.
  * :class:`GatewayClient` maps a normalized ``tools/call`` onto the gateway
    and fails closed when the gateway is unreachable.

LangChain is an OPTIONAL extra; the whole module skips cleanly when absent so
the gateway test suite stays dependency-light.
"""

from __future__ import annotations

import asyncio

import pytest

langchain_core = pytest.importorskip("langchain_core")

from langchain_core.tools import tool  # noqa: E402

from examples.adapters.agent_interceptor import DENIED_PREFIX, OPAGuardrailTool  # noqa: E402
from examples.adapters.gateway_client import GatewayClient, Verdict  # noqa: E402


@tool
def get_balance(account: str) -> str:
    """Read an account balance."""
    return f"balance for {account}: $100"


@tool
def execute_transfer(amount: int, beneficiary: str) -> str:
    """Move money."""
    return f"transferred {amount} to {beneficiary}"


class _StubClient(GatewayClient):
    """Gateway client whose verdict is fixed; records what it was asked."""

    def __init__(self, verdict: Verdict) -> None:  # noqa: D107
        self._verdict = verdict
        self.seen: list[dict] = []

    def check(self, **kwargs) -> Verdict:  # noqa: D102
        self.seen.append(kwargs)
        return self._verdict


def test_allow_runs_the_wrapped_tool() -> None:
    gw = OPAGuardrailTool.wrap(get_balance, client=_StubClient(Verdict(allowed=True)))
    assert gw.invoke({"account": "acct_1"}) == "balance for acct_1: $100"


def test_deny_returns_policy_reason_without_running_tool() -> None:
    reason = "BLOCKED: money movement requires an approved change ticket (F)."
    gw = OPAGuardrailTool.wrap(
        execute_transfer,
        client=_StubClient(Verdict(allowed=False, reason=reason)),
    )
    out = gw.invoke({"amount": 100, "beneficiary": "payroll"})
    assert out == f"{DENIED_PREFIX}: {reason}"
    # The tool must NOT have run — the return is the denial, not a transfer.
    assert "transferred" not in out


def test_deny_is_async_safe() -> None:
    reason = "BLOCKED: denied (async path)."
    gw = OPAGuardrailTool.wrap(
        get_balance, client=_StubClient(Verdict(allowed=False, reason=reason))
    )
    out = asyncio.run(gw.ainvoke({"account": "acct_1"}))
    assert out.startswith(DENIED_PREFIX)


def test_redacted_output_replaces_result() -> None:
    redacted = {"account": "REDACTED", "balance": "100"}
    gw = OPAGuardrailTool.wrap(
        get_balance, client=_StubClient(Verdict(allowed=True, redacted_output=redacted))
    )
    assert gw.invoke({"account": "acct_1"}) == redacted


def test_gate_receives_normalized_tool_and_args() -> None:
    stub = _StubClient(Verdict(allowed=True))
    gw = OPAGuardrailTool.wrap(get_balance, client=stub, industry="fintech")
    gw.invoke({"account": "acct_9"})
    seen = stub.seen[0]
    assert seen["tool"] == "get_balance"
    assert seen["args"] == {"account": "acct_9"}
    assert seen["industry"] == "fintech"


def test_wrapper_preserves_name_and_schema() -> None:
    gw = OPAGuardrailTool.wrap(get_balance, client=_StubClient(Verdict(allowed=True)))
    assert gw.name == "get_balance"
    assert gw.args_schema is get_balance.args_schema


# ---------------------------------------------------------- GatewayClient --
def test_client_maps_payload_and_returns_allow(monkeypatch) -> None:
    captured = {}

    def fake_post(self, payload):
        captured["payload"] = payload
        return {"jsonrpc": "2.0", "id": payload["id"], "result": {"allowed": True}}

    monkeypatch.setattr(GatewayClient, "_post", fake_post)
    client = GatewayClient("http://gw.test")
    verdict = client.check(tool="get_balance", args={"account": "a"}, industry="fintech")

    assert verdict.allowed is True
    params = captured["payload"]["params"]
    assert captured["payload"]["method"] == "tools/call"
    assert params == {"tool": "get_balance", "industry": "fintech", "args": {"account": "a"}}


def test_client_maps_deny_to_reason(monkeypatch) -> None:
    def fake_post(self, payload):
        return {
            "jsonrpc": "2.0",
            "id": payload["id"],
            "error": {"code": -32000, "data": {"reason": "BLOCKED: nope"}},
        }

    monkeypatch.setattr(GatewayClient, "_post", fake_post)
    verdict = GatewayClient("http://gw.test").check(tool="x", industry="fintech")
    assert verdict.allowed is False
    assert verdict.reason == "BLOCKED: nope"


def test_client_fails_closed_when_gateway_unreachable() -> None:
    # Port 1 is closed → urllib raises URLError → client must deny, not raise.
    client = GatewayClient("http://127.0.0.1:1", timeout=0.5)
    verdict = client.check(tool="get_balance", industry="fintech")
    assert verdict.allowed is False
    assert "unreachable" in verdict.reason.lower()


def test_client_include_optional_fields(monkeypatch) -> None:
    captured = {}

    def fake_post(self, payload):
        captured["params"] = payload["params"]
        return {"result": {"allowed": True}}

    monkeypatch.setattr(GatewayClient, "_post", fake_post)
    GatewayClient("http://gw.test").check(
        tool="query_phi",
        args={"scope": "diagnosis"},
        industry="healthcare",
        context={"trajectory": []},
        principal_id="clinician",
        output={"patient_mrn": "MRN-1"},
        session_id="s1",
    )
    p = captured["params"]
    assert p["principal_id"] == "clinician"
    assert p["context"] == {"trajectory": []}
    assert p["output"] == {"patient_mrn": "MRN-1"}
    assert p["session_id"] == "s1"
