"""Strands Agents SDK interceptor + gateway client tests.

Covers the Strands framework adapter (:class:`GatewayGuardrail`):

  * ``BeforeToolCallEvent``: ALLOW leaves the event untouched (tool runs);
    DENY sets ``event.cancel_tool`` so the tool never executes and the model
    re-plans.
  * ``AfterToolCallEvent``: when the gateway returned a ``redacted_output``,
    the hook replaces ``event.result`` with the redacted value.
  * Identity/context are passed at construction (matching Strands' pattern;
    unlike pydantic-ai, Strands has no ``deps`` equivalent).
  * The verdict is correctly threaded between the paired events via
    ``toolUseId``.

Tested at the ``HookProvider`` level — we construct the events ourselves and
assert the provider's behavior, rather than running a full agent loop (Strands
has no test model equivalent to pydantic-ai's ``TestModel``). This matches the
project's zero-AWS-credentials testing rule.

``pytest.importorskip`` keeps the gateway suite dependency-light.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("strands")

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent  # noqa: E402
from strands.types.tools import ToolResult, ToolUse  # noqa: E402

from src.agent_gateway_client import GatewayClient, Verdict  # noqa: E402
from src.agent_interceptor_strands import DENIED_PREFIX, GatewayGuardrail  # noqa: E402


class _StubAgent:
    """Minimal stub satisfying the ``LocalAgent`` protocol for event construction."""

    _is_strands_local_agent: True = True  # type: ignore[assignment]
    agent_id: str = "test-agent"
    name: str = "Test Agent"
    description: str | None = None
    messages: list = []
    state: dict = {}
    hooks: Any = None
    model: Any = None
    system_prompt: str | None = None
    tool_registry: Any = None
    event_loop_metrics: Any = None


_STUB_AGENT = _StubAgent()


def _make_tool_use(
    name: str = "test_tool",
    toolUseId: str = "call_001",
    input: Any = None,
) -> ToolUse:
    return ToolUse(
        name=name,
        toolUseId=toolUseId,
        input=input or {},
    )


def _make_before_event(
    tool_use: ToolUse | None = None,
    cancel_tool: bool | str = False,
) -> BeforeToolCallEvent:
    return BeforeToolCallEvent(
        agent=_STUB_AGENT,
        selected_tool=None,  # type: ignore[arg-type]
        tool_use=tool_use or _make_tool_use(),
        invocation_state={},
        cancel_tool=cancel_tool,
    )


def _make_after_event(
    tool_use: ToolUse | None = None,
    result: ToolResult | None = None,
) -> AfterToolCallEvent:
    tu = tool_use or _make_tool_use()
    return AfterToolCallEvent(
        agent=_STUB_AGENT,
        selected_tool=None,  # type: ignore[arg-type]
        tool_use=tu,
        invocation_state={},
        result=result
        or ToolResult(
            content=[{"text": "original result"}], status="success", toolUseId=tu["toolUseId"]
        ),
        duration=0.5,
    )


class _StubClient(GatewayClient):
    """Verdict fixed per test; records what it was asked."""

    def __init__(self, verdict: Verdict) -> None:
        self._verdict = verdict
        self.seen: list[dict] = []

    def check(self, **kwargs: Any) -> Verdict:
        self.seen.append(kwargs)
        return self._verdict


# -- BeforeToolCallEvent: allow / deny ----------------------------------------


def test_allow_leaves_event_untouched() -> None:
    """ALLOW verdict → cancel_tool stays False, tool runs normally."""
    guard = GatewayGuardrail(client=_StubClient(Verdict(allowed=True)))
    event = _make_before_event()

    guard._gate(event)

    assert event.cancel_tool is False  # unchanged
    assert not event.selected_tool  # not touched


def test_deny_sets_cancel_tool_with_reason() -> None:
    """DENY verdict → cancel_tool is set with the policy reason."""
    guard = GatewayGuardrail(client=_StubClient(Verdict(allowed=False, reason="over cap (B)")))
    event = _make_before_event()

    guard._gate(event)

    assert event.cancel_tool is not False
    assert DENIED_PREFIX in str(event.cancel_tool)
    assert "over cap (B)" in str(event.cancel_tool)


def test_deny_does_not_clear_selected_tool() -> None:
    """DENY sets cancel_tool but leaves selected_tool intact (Strands handles cancellation)."""
    guard = GatewayGuardrail(client=_StubClient(Verdict(allowed=False, reason="nope")))
    event = _make_before_event()

    guard._gate(event)

    assert event.cancel_tool is not False
    # selected_tool is left as-is — Strands uses cancel_tool to produce the
    # error-status ToolResult without us needing to null it out.


def test_gate_sends_normalized_tool_and_args() -> None:
    """The gateway client receives the tool name and args from the event."""
    stub = _StubClient(Verdict(allowed=True))
    guard = GatewayGuardrail(client=stub)
    tu = _make_tool_use(name="execute_transfer", input={"amount": 50, "beneficiary": "x"})
    event = _make_before_event(tool_use=tu)

    guard._gate(event)

    assert len(stub.seen) == 1
    seen = stub.seen[0]
    assert seen["tool"] == "execute_transfer"
    assert seen["args"] == {"amount": 50, "beneficiary": "x"}


def test_gate_sends_industry_principal_session() -> None:
    """Construction-time identity/industry/session are forwarded to the gateway."""
    stub = _StubClient(Verdict(allowed=True))
    guard = GatewayGuardrail(
        client=stub,
        industry="healthcare",
        principal_id="svc-billing",
        session_id="sess_abc",
    )
    event = _make_before_event()

    guard._gate(event)

    seen = stub.seen[0]
    assert seen["industry"] == "healthcare"
    assert seen["principal_id"] == "svc-billing"
    assert seen["session_id"] == "sess_abc"


def test_gate_forwards_context() -> None:
    """Cross-call context is forwarded to the gateway."""
    stub = _StubClient(Verdict(allowed=True))
    context = {"approval": {"reference": "CHG-123", "approved": True}}
    guard = GatewayGuardrail(client=stub, context=context)
    event = _make_before_event()

    guard._gate(event)

    assert stub.seen[0]["context"] == context


def test_gate_sends_defaults_when_not_provided() -> None:
    """Defaults (empty strings / None) are sent for omitted construction args."""
    stub = _StubClient(Verdict(allowed=True))
    guard = GatewayGuardrail(client=stub)
    event = _make_before_event()

    guard._gate(event)

    seen = stub.seen[0]
    assert seen["industry"] == ""
    assert seen["principal_id"] is None
    assert seen["session_id"] is None


def test_gate_handles_non_dict_tool_input() -> None:
    """When tool input is not a dict (e.g. bare string), wrap it under ``input``."""
    stub = _StubClient(Verdict(allowed=True))
    guard = GatewayGuardrail(client=stub)
    tu = _make_tool_use(name="echo", input="hello world")
    event = _make_before_event(tool_use=tu)

    guard._gate(event)

    assert stub.seen[0]["args"] == {"input": "hello world"}


# -- Verdict threading (toolUseId) --------------------------------------------


def test_verdict_stashed_by_toolUseId() -> None:
    """The verdict is stashed keyed by toolUseId for the redaction hook."""
    guard = GatewayGuardrail(client=_StubClient(Verdict(allowed=True)))
    tu = _make_tool_use(toolUseId="call_42")
    event = _make_before_event(tool_use=tu)

    guard._gate(event)

    assert "call_42" in guard._verdicts
    assert guard._verdicts["call_42"].allowed is True


def test_verdict_consumed_by_redact() -> None:
    """The redaction hook consumes (pops) the stashed verdict."""
    guard = GatewayGuardrail(client=_StubClient(Verdict(allowed=True, redacted_output="redacted")))
    tu = _make_tool_use(toolUseId="call_99")
    before = _make_before_event(tool_use=tu)
    after = _make_after_event(tool_use=tu)

    guard._gate(before)
    assert "call_99" in guard._verdicts

    guard._redact(after)

    assert "call_99" not in guard._verdicts  # popped


# -- AfterToolCallEvent: redaction --------------------------------------------


def test_redact_replaces_result_when_redacted_output() -> None:
    """When the gateway returned a redacted output, replace the tool result."""
    redacted_text = "transferred $**** to x"
    guard = GatewayGuardrail(
        client=_StubClient(Verdict(allowed=True, redacted_output=redacted_text))
    )
    tu = _make_tool_use(toolUseId="call_r1")
    before = _make_before_event(tool_use=tu)
    after = _make_after_event(tool_use=tu)

    guard._gate(before)
    guard._redact(after)

    # The redacted output is wrapped in a ToolResult with the text content.
    assert after.result["content"][0]["text"] == redacted_text
    assert after.result["status"] == "success"


def test_redact_does_nothing_without_redacted_output() -> None:
    """When no redacted_output, the original result is preserved."""
    guard = GatewayGuardrail(client=_StubClient(Verdict(allowed=True)))
    tu = _make_tool_use(toolUseId="call_r2")
    before = _make_before_event(tool_use=tu)
    after = _make_after_event(tool_use=tu)
    original = dict(after.result)  # copy

    guard._gate(before)
    guard._redact(after)

    assert after.result == original


def test_redact_does_nothing_when_no_verdict_stashed() -> None:
    """If _gate was never called (no verdict stashed), redact is a no-op."""
    guard = GatewayGuardrail(client=_StubClient(Verdict(allowed=True)))
    after = _make_after_event()
    original = dict(after.result)

    guard._redact(after)

    assert after.result == original


def test_redact_preserves_original_result_on_deny() -> None:
    """On DENY, the verdict has no redacted_output so the original result stands."""
    guard = GatewayGuardrail(client=_StubClient(Verdict(allowed=False, reason="blocked")))
    tu = _make_tool_use(toolUseId="call_r3")
    before = _make_before_event(tool_use=tu)
    after = _make_after_event(tool_use=tu)
    original = dict(after.result)

    guard._gate(before)
    guard._redact(after)

    assert after.result == original


def test_redact_with_dict_redacted_output_containing_content() -> None:
    """When redacted_output is a dict with a 'content' key, use it as-is."""
    redacted = {
        "content": [{"json": {"balance": "****"}}],
        "status": "success",
        "toolUseId": "call_r4",
    }
    guard = GatewayGuardrail(client=_StubClient(Verdict(allowed=True, redacted_output=redacted)))
    tu = _make_tool_use(toolUseId="call_r4")
    before = _make_before_event(tool_use=tu)
    after = _make_after_event(tool_use=tu)

    guard._gate(before)
    guard._redact(after)

    assert after.result["content"][0]["json"] == {"balance": "****"}


# -- Multi-tool isolation -----------------------------------------------------


def test_multi_tool_verdicts_isolated_by_toolUseId() -> None:
    """Multiple concurrent tool calls have their verdicts correctly isolated."""
    stub = _StubClient(Verdict(allowed=True))
    guard = GatewayGuardrail(client=stub)

    # Simulate two tool calls interleaved
    tu1 = _make_tool_use(name="tool_a", toolUseId="call_a")
    tu2 = _make_tool_use(name="tool_b", toolUseId="call_b")

    event_a = _make_before_event(tool_use=tu1)
    event_b = _make_before_event(tool_use=tu2)

    guard._gate(event_a)
    guard._gate(event_b)

    assert "call_a" in guard._verdicts
    assert "call_b" in guard._verdicts
    assert guard._verdicts["call_a"].allowed is True
    assert guard._verdicts["call_b"].allowed is True

    # Redact in reverse order
    after_b = _make_after_event(tool_use=tu2)
    after_a = _make_after_event(tool_use=tu1)

    guard._redact(after_b)
    assert "call_b" not in guard._verdicts
    assert "call_a" in guard._verdicts  # not affected

    guard._redact(after_a)
    assert "call_a" not in guard._verdicts


# -- HookProvider protocol compliance -----------------------------------------


def test_implements_hook_provider_protocol() -> None:
    """GatewayGuardrail satisfies the HookProvider protocol."""
    guard = GatewayGuardrail()
    assert hasattr(guard, "register_hooks")
    from strands.hooks import HookProvider

    assert isinstance(guard, HookProvider)
