"""Strands Agents SDK adapter — a HookProvider that gates every tool dispatch.

Strands Agents SDK provides a ``HookProvider`` protocol (``register_hooks``)
that lets reusable components subscribe to agent lifecycle events. The gateway
gate subscribes to ``BeforeToolCallEvent`` — fires with the **model-decided**
tool name and parameters immediately before the tool runs. ALLOW → the tool
executes normally. DENY → set ``event.cancel_tool`` with the policy reason,
which Strands surfaces as an error-status ``ToolResult`` back to the model so
it can re-plan. The tool's real implementation never runs on a deny.

Redaction (feature G) is applied in ``AfterToolCallEvent``: when the gateway
returned a redacted output, the hook replaces ``event.result`` before the
agent sees it.

The verdict is threaded between the two events via a per-provider dict keyed
on ``toolUseId`` (Strands' unique identifier per tool call), which is
guaranteed stable across the paired events for one tool invocation.

Wire it up by passing a ``GatewayGuardrail`` instance in the ``hooks`` list:

    from examples.adapters.agent_interceptor_strands import GatewayGuardrail
    agent = Agent(
        model=model,
        tools=[my_tool],
        hooks=[GatewayGuardrail(industry="fintech")],
    )

``strands-agents`` is an **optional** extra (``pip install -e .[strands]``),
imported lazily; the gateway core stays framework-free. Import this module only
when ``strands-agents`` is installed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from strands.hooks import (
    AfterToolCallEvent,
    BeforeToolCallEvent,
    HookOrder,
    HookProvider,
    HookRegistry,
)
from strands.types.tools import ToolResult

from examples.adapters.gateway_client import GatewayClient, Verdict

# Prefix that marks a policy denial in the cancel_tool message sent back to the
# agent loop, so it is unambiguous this was the gateway, not a tool error.
DENIED_PREFIX = "POLICY DENIED"


class GatewayGuardrail(HookProvider):
    """A Strands ``HookProvider`` that gates every tool dispatch through the gateway.

    Construct once and pass to ``Agent(hooks=[...])``. Every tool the agent
    calls is checked against the gateway before execution. On DENY the tool
    never runs and the model receives the policy reason for re-planning.

    Args:
        client: The :class:`GatewayClient` (defaults to ``127.0.0.1:8080``).
        industry: Industry label the gateway's policy dispatcher expects.
        principal_id: Identity passed as ``principal_id`` to the gateway. The
            agent itself holds no credentials — identity is enriched at the
            gateway (DESIGN §4.1a).
        session_id: Cross-call session for trajectory/rate rules (features
            D/E).
        context: Optional cross-call ``input.context`` (e.g. an approved change
            ticket for feature F, an isolated target for feature C).
    """

    def __init__(
        self,
        *,
        client: GatewayClient | None = None,
        industry: str = "",
        principal_id: str | None = None,
        session_id: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        self._client = client or GatewayClient()
        self._industry = industry
        self._principal_id = principal_id
        self._session_id = session_id
        self._context = dict(context) if context is not None else None
        # Stash verdicts between BeforeToolCallEvent and AfterToolCallEvent,
        # keyed by toolUseId (stable across the paired events for one tool call).
        self._verdicts: dict[str, Verdict] = {}

    def register_hooks(self, registry: HookRegistry) -> None:
        """Register the gate and redaction callbacks.

        The gate runs at default order (0) — any SDK-internal hooks run first.
        The redaction hook runs at SDK_LAST so other hooks can observe the
        original result before we substitute it.
        """
        registry.add_callback(BeforeToolCallEvent, self._gate)
        registry.add_callback(AfterToolCallEvent, self._redact, order=HookOrder.SDK_LAST)

    # -- hook callbacks -------------------------------------------------------

    def _gate(self, event: BeforeToolCallEvent) -> None:
        """Ask the gateway before the tool runs.

        Sets ``event.cancel_tool`` on DENY so the tool never executes and the
        model sees the policy reason.
        """
        tool_name = event.tool_use["name"]
        tool_args = event.tool_use.get("input", {})

        verdict = self._client.check(
            tool=tool_name,
            args=tool_args if isinstance(tool_args, dict) else {"input": tool_args},
            industry=self._industry,
            principal_id=self._principal_id,
            session_id=self._session_id,
            context=self._context,
        )

        tid = event.tool_use["toolUseId"]
        self._verdicts[tid] = verdict

        if not verdict.allowed:
            event.cancel_tool = f"{DENIED_PREFIX}: {verdict.reason}"

    def _redact(self, event: AfterToolCallEvent) -> None:
        """Apply G-redaction to the tool result if the gateway ordered it.

        Reads the verdict stashed by ``_gate`` for this tool call. If the
        gateway returned a ``redacted_output``, replace ``event.result`` with
        it before the agent sees the tool's return value.
        """
        tid = event.tool_use["toolUseId"]
        verdict = self._verdicts.pop(tid, None)
        if verdict is None or verdict.redacted_output is None:
            return

        redacted = verdict.redacted_output
        # Wrap the redacted value in the ToolResult TypedDict Strands expects.
        if isinstance(redacted, dict) and "content" in redacted:
            event.result = ToolResult(redacted)  # type: ignore[arg-type]
        else:
            event.result = ToolResult(
                content=[{"text": str(redacted)}],
                status="success",
                toolUseId=tid,
            )
