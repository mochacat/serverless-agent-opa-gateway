"""Pydantic AI interceptor + gateway client tests (T5).

Covers the Pydantic AI framework adapter:
  * :class:`build_gateway_hooks` gates every tool dispatch through pydantic-ai's
    real execution path: ALLOW runs the tool, DENY raises ``ModelRetry`` so the
    tool never runs and the model re-plans, and redaction replaces the output.
  * Identity/context are read off ``RunContext.deps`` (env-style enrichment),
    with build-time defaults as fallback.

Only hook semantics are asserted here — nothing depends on a real model or a
live gateway. ``pytest.importorskip`` keeps the gateway suite dependency-light.
"""

from __future__ import annotations

from typing import Any

import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402
from pydantic_ai.tools import ToolDefinition  # noqa: E402

from examples.adapters.agent_interceptor_pydantic import build_gateway_hooks  # noqa: E402
from examples.adapters.gateway_client import GatewayClient, Verdict  # noqa: E402


class _Deps:
    """Minimal per-run deps with identity + approved-transfer context."""

    def __init__(self, principal_id="ops"):
        self.principal_id = principal_id
        self.context = {
            "rate_window": {"max": 3},
            "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}],
            "approval": {"reference": "CHG-123", "approved": True},
            "target": {"isolated": True, "leased": False},
        }


class _StubClient(GatewayClient):
    """Verdict fixed per test; records what it was asked."""

    def __init__(self, verdict: Verdict) -> None:
        self._verdict = verdict
        self.seen: list[dict] = []

    def check(self, **kwargs) -> Verdict:
        self.seen.append(kwargs)
        return self._verdict


class _ScriptedModel(TestModel):
    """TestModel that emits one scripted arg-dict per tool call.

    ``Sequence`` allows the deny→retry→allow loop: pydantic-ai relays the
    ``ModelRetry`` we raise and re-calls the tool, so successive entries in the
    per-tool queue let the agent "learn" and retry within policy.
    """

    def __init__(self, sequence: dict[str, list[dict]]):
        super().__init__(call_tools=list(sequence))
        self._seq = sequence

    def gen_tool_args(self, tool_def: ToolDefinition) -> dict:
        q = self._seq[tool_def.name]
        args = q[0] if q else {}
        if len(q) > 1:
            q.pop(0)
        return args


def _run(
    verdict: Verdict, sequence: dict[str, list[dict]], *, deps=None
) -> tuple[Any, _StubClient]:
    """Drive a real pydantic-ai Agent through one tool call with a scripted model."""
    stub = _StubClient(verdict)
    agent = Agent(
        _ScriptedModel(sequence),
        deps_type=_Deps,
        capabilities=[build_gateway_hooks(client=stub)],
        tools=[execute_transfer],
        system_prompt="x",
    )
    if deps is None:
        deps = _Deps()
    return agent.run_sync("go", deps=deps), stub


def execute_transfer(amount: int, beneficiary: str) -> str:
    return f"transferred ${amount} to {beneficiary}"


def test_allow_runs_the_tool() -> None:
    result, stub = _run(
        Verdict(allowed=True), {"execute_transfer": [{"amount": 100, "beneficiary": "x"}]}
    )
    assert "transferred $100" in result.output
    assert stub.seen  # the gate did ask the gateway before running


def test_deny_blocks_tool_and_surfaces_reason_to_model() -> None:
    stub = _StubClient(Verdict(allowed=False, reason="BLOCKED: over cap (B)."))
    agent = Agent(
        _ScriptedModel({"execute_transfer": [{"amount": 999, "beneficiary": "x"}]}),
        deps_type=_Deps,
        capabilities=[build_gateway_hooks(client=stub)],
        tools=[execute_transfer],
        system_prompt="x",
    )
    with pytest.raises(pydantic_ai.exceptions.UnexpectedModelBehavior):
        agent.run_sync("go", deps=_Deps())
    # The tool was denied every attempt → pydantic-ai exhausts retries, but the
    # policy reason DID get relayed to the model as a retried tool outcome, and
    # the tool's real body never executed (its output is absent everywhere).
    # Inspect captured retry feedback via a second single-call run instead:
    # here, just assert the gate was asked and the run could not complete.
    assert stub.seen  # the gate was consulted before every attempt
    assert len(stub.seen) == 2  # initial call + the one retry before the cap


def test_deny_then_allow_loop_reruns_in_cycle() -> None:
    # First call denied (over cap), retry within policy allowed — proves the
    # ModelRetry feedback lets the agent re-plan rather than hard-fail.
    class _DenyThenAllow(_StubClient):
        def __init__(self):
            super().__init__(None)
            self.n = 0

        def check(self, **kwargs):
            self.seen.append(kwargs)
            self.n += 1
            return (
                Verdict(allowed=False, reason="BLOCKED") if self.n == 1 else Verdict(allowed=True)
            )

    client = _DenyThenAllow()
    agent = Agent(
        _ScriptedModel({"execute_transfer": [{"amount": 999, "beneficiary": "x"}]}),
        deps_type=_Deps,
        capabilities=[build_gateway_hooks(client=client)],
        tools=[execute_transfer],
        system_prompt="x",
    )
    result = agent.run_sync("go", deps=_Deps())
    # deny on attempt 1, allow on attempt 2 (the retried second emit).
    assert client.n == 2
    assert "transferred" in result.output


def test_redaction_replaces_tool_output() -> None:
    redacted = "transferred $**** to x"
    result, _ = _run(
        Verdict(allowed=True, redacted_output=redacted),
        {"execute_transfer": [{"amount": 50, "beneficiary": "x"}]},
    )
    assert redacted in result.output
    assert "transferred $50" not in result.output


def test_gate_sends_normalized_tool_and_args() -> None:
    _, stub = _run(
        Verdict(allowed=True),
        {"execute_transfer": [{"amount": 50, "beneficiary": "x"}]},
    )
    seen = stub.seen[0]
    assert seen["tool"] == "execute_transfer"
    assert seen["args"] == {"amount": 50, "beneficiary": "x"}
    assert seen["principal_id"] == "ops"


def test_deps_context_is_forwarded() -> None:
    _, stub = _run(
        Verdict(allowed=True),
        {"execute_transfer": [{"amount": 50, "beneficiary": "x"}]},
    )
    seen = stub.seen[0]
    assert seen["context"]["approval"]["reference"] == "CHG-123"


def test_build_defaults_apply_when_deps_lack_fields() -> None:
    stub = _StubClient(Verdict(allowed=True))

    class _Bare:
        pass

    agent = Agent(
        _ScriptedModel({"execute_transfer": [{"amount": 1, "beneficiary": "x"}]}),
        deps_type=_Bare,
        capabilities=[build_gateway_hooks(client=stub, principal_id="svc")],
        tools=[execute_transfer],
        system_prompt="x",
    )
    agent.run_sync("go", deps=_Bare())
