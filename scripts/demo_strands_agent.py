#!/usr/bin/env python3
"""Strands Agents SDK reference demo — tool dispatch gated by the gateway.

This wires the **actual Strands integration**: a real Strands tool function
registered on a :class:`strands.Agent`, with the gateway gate attached as a
``HookProvider`` (``GatewayGuardrail``). The agent runs its real loop — the
model decides which tool to call, and our hook intercepts *before* the tool
executes to ask the running gateway (real Rego).

On DENY the hook sets ``cancel_tool``: the tool never runs and the model
receives the policy reason as an error-status ``ToolResult`` and can re-plan.

Since Strands targets Bedrock by default and requires a real model for a full
agent loop, this demo uses a **scripted approach**: it constructs the hook
provider and manually simulates the events the agent loop would fire, showing
the allow/deny/redact flow. A real agent integration would just pass the
``GatewayGuardrail`` in the ``hooks`` list.

Prereqs:
  * ``pip install -e .[strands]`` (the optional interceptor)
  * a gateway running locally: ``make run-gateway`` (another terminal)

Usage:  python3 scripts/demo_strands_agent.py [--url http://127.0.0.1:8080]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as `python scripts/demo_strands_agent.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent
from strands.types.tools import ToolResult, ToolUse

from src.agent_gateway_client import GatewayClient
from src.agent_interceptor_strands import GatewayGuardrail

GRN = "\033[92m"
RED = "\033[91m"
CYN = "\033[96m"
DIM = "\033[2m"
RST = "\033[0m"


def make_tool_use(name: str, tid: str, input: dict) -> ToolUse:
    return ToolUse(name=name, toolUseId=tid, input=input)


class _StubAgent:
    """Minimal stub satisfying the ``LocalAgent`` protocol for event construction."""

    _is_strands_local_agent: True = True  # type: ignore[assignment]
    agent_id: str = "demo-agent"
    name: str = "Demo Agent"
    description: str | None = None
    messages: list = []
    state: dict = {}
    hooks: object = None
    model: object = None
    system_prompt: str | None = None
    tool_registry: object = None
    event_loop_metrics: object = None


_STUB_AGENT = _StubAgent()


def run_scenario(
    label: str,
    guard: GatewayGuardrail,
    tool_uses: list[ToolUse],
) -> None:
    """Drive one scenario through the gate and redaction hooks."""
    print(f"\n  {CYN}── {label} ──{RST}\n")
    for tu in tool_uses:
        before = BeforeToolCallEvent(
            agent=_STUB_AGENT,
            selected_tool=None,  # type: ignore[arg-type]
            tool_use=tu,
            invocation_state={},
            cancel_tool=False,
        )
        guard._gate(before)

        tid = tu["toolUseId"]
        verdict_stashed = guard._verdicts.get(tid)

        if before.cancel_tool is not False:
            print(f"    {RED}✗ DENY{RST}  {tu['name']}({tu['input']})")
            print(f"    {DIM}  → {before.cancel_tool}{RST}")
            # On deny, the tool never runs; we still fire AfterToolCallEvent
            # (Strands does this internally) with the cancel message.
            after = AfterToolCallEvent(
                agent=_STUB_AGENT,
                selected_tool=None,  # type: ignore[arg-type]
                tool_use=tu,
                invocation_state={},
                result=ToolResult(
                    content=[{"text": str(before.cancel_tool)}],
                    status="error",
                    toolUseId=tid,
                ),
                cancel_message=str(before.cancel_tool),
                duration=None,
            )
        else:
            # Simulate the tool running and producing a result
            tool_output = f"executed {tu['name']}({tu['input']})"
            print(f"    {GRN}✓ ALLOW{RST} {tu['name']}({tu['input']})")
            print(f"    {DIM}  → {tool_output}{RST}")
            after = AfterToolCallEvent(
                agent=_STUB_AGENT,
                selected_tool=None,  # type: ignore[arg-type]
                tool_use=tu,
                invocation_state={},
                result=ToolResult(
                    content=[{"text": tool_output}],
                    status="success",
                    toolUseId=tid,
                ),
                duration=0.3,
            )

        guard._redact(after)

        if verdict_stashed and verdict_stashed.redacted_output is not None:
            redacted_text = after.result["content"][0].get("text", "")
            print(f"    {CYN}  ✄ redacted →{RST} {redacted_text}")


def main(gw_url: str) -> int:
    client = GatewayClient(gw_url)

    # --- Scenario 1: Fintech — transfer within/over cap -----------------------
    fintech = GatewayGuardrail(
        client=client,
        industry="fintech",
        principal_id="ops",
        session_id="demo-session",
        context={
            "rate_window": {"max": 3},
            "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}],
            "approval": {"reference": "CHG-123", "approved": True},
            "target": {"isolated": True, "leased": False},
        },
    )
    run_scenario(
        "Fintech: small transfer (ALLOW) vs over-cap transfer (DENY)",
        fintech,
        [
            make_tool_use("get_balance", "call_1", {"account": "checking"}),
            make_tool_use("execute_transfer", "call_2", {"amount": 500, "beneficiary": "alice"}),
            make_tool_use(
                "execute_transfer", "call_3", {"amount": 10_000_000, "beneficiary": "vendor_alpha"}
            ),
        ],
    )

    # --- Scenario 2: Healthcare — PHI query with redaction --------------------
    healthcare = GatewayGuardrail(
        client=client,
        industry="healthcare",
        principal_id="dr_smith",
        session_id="demo-session-2",
    )
    run_scenario(
        "Healthcare: PHI query with redaction (B+G)",
        healthcare,
        [
            make_tool_use(
                "query_patient_records", "call_4", {"patient_id": "P-12345", "query": "diagnosis"}
            ),
        ],
    )

    # --- Scenario 3: SecOps — destructive action without approval -------------
    secops = GatewayGuardrail(
        client=client,
        industry="secops",
        principal_id="soc_analyst",
        session_id="demo-session-3",
    )
    run_scenario(
        "SecOps: destructive action (DENY - F)",
        secops,
        [
            make_tool_use("delete_cluster", "call_5", {"cluster_id": "prod-us-east-1"}),
        ],
    )

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strands gated-tool demo")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="gateway base URL")
    ns = parser.parse_args()
    try:
        sys.exit(main(ns.url))
    except Exception as exc:  # noqa: BLE001 — friendly message for a demo
        print(f"{RED}demo failed: {exc}{RST}", file=sys.stderr)
        print(
            "is the gateway running?  make run-gateway   (and pip install -e .[strands])",
            file=sys.stderr,
        )
        sys.exit(2)
