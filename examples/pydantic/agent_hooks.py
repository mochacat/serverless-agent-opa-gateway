#!/usr/bin/env python3
"""Pydantic AI reference demo — an agent's tool loop gated by the gateway.

This wires the **actual Pydantic AI integration**: real tool functions registered
on a :class:`pydantic_ai.Agent`, with the gateway gate attached as a ``Hooks``
capability (``build_gateway_hooks``). The agent runs its real loop — it calls the
tools, and :class:`src.agent_interceptor_pydantic` intercepts *inside* the run to
ask the running gateway (real Rego) before each tool executes.

On DENY the gate raises ``ModelRetry``: the tool never runs and the model sees the
policy reason and re-plans — the exact loop a hosted model would drive. We use
pydantic-ai's ``TestModel`` so the demo needs no API key / hosted model; it
scripts the tool calls and then produces a final text answer.

Prereqs:
  * ``pip install -e .[pydantic]`` (the optional interceptor)
  * a gateway running locally: ``make run-gateway`` (another terminal)

Usage:  python3 examples/pydantic/agent_hooks.py [--url http://127.0.0.1:8080]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as `python examples/pydantic/agent_hooks.py` from the repo root
# without installing the package: put the repo root on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition

from examples.adapters.agent_interceptor_pydantic import DENIED_PREFIX, build_gateway_hooks
from examples.adapters.gateway_client import GatewayClient


class Deps:
    """Per-run identity + context the gateway gate reads off ``deps``.

    ``context`` supplies the approved change ticket (feature F) and the
    isolated, unleased target (feature C) that money movement requires —
    the "legit environment" a human ops run would already hold.
    """

    def __init__(self, principal_id: str, industry: str = "fintech") -> None:
        self.principal_id = principal_id
        self.industry = industry
        # A legit ops run that already inspected the target (trajectory), holds
        # an approved change ticket (F) and a rate budget (E), and targets an
        # isolated, unleased machine (C). The gateway is *stateless*, so this
        # cross-call context is injected here rather than stored in OPA.
        self.context = {
            "rate_window": {"max": 3},
            "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}],
            "approval": {"reference": "CHG-123", "approved": True},
            "target": {"isolated": True, "leased": False},
        }


class ScheduledTestModel(TestModel):
    """``TestModel`` that emits a scripted argument sequence per tool.

    Stock ``TestModel`` fuzzes schema-valid args, which is nondeterministic for
    a demo. This subclass pops a fixed arg-dict off a per-tool queue each call,
    and — crucially — **advances the queue on retry**, so when the gateway
    denies a transfer the next attempt carries a lower (allowed) amount. That
    lets the demo show the real "model sees policy feedback and re-plans" loop
    without a hosted model.
    """

    def __init__(self, sequence: dict[str, list[dict]], *, call_tools: list[str]):
        super().__init__(call_tools=call_tools)
        self._sequence = sequence

    def gen_tool_args(self, tool_def: ToolDefinition) -> dict:
        name = tool_def.name
        queue = self._sequence[name]
        args = queue[0] if queue else {}
        if len(queue) > 1:  # keep the last entry as the stand-still default
            queue.pop(0)
        return args


# --- real Pydantic AI tool functions (the agent's capabilities) -------------
def execute_transfer(amount: int, beneficiary: str) -> str:
    """Move money to a beneficiary."""
    return f"transferred ${amount} to {beneficiary}"


def main(gw_url: str) -> int:
    client = GatewayClient(gw_url)
    hooks = build_gateway_hooks(client=client)

    # Scenario: an agent tries to move $10M offshore. First attempt is over the
    # single-transfer cap and is DENIED; the model reads the policy reason and
    # re-attempts a compliant transfer, which is ALLOWED.
    sequence = {
        "execute_transfer": [
            {"amount": 10_000_000, "beneficiary": "vendor_alpha"},  # 1st: DENY (over $10k cap)
            {"amount": 5_000, "beneficiary": "vendor_alpha"},  # 2nd: ALLOW (within cap)
        ],
    }
    model = ScheduledTestModel(sequence, call_tools=["execute_transfer"])

    agent = Agent(
        model,  # scripts tool calls; no hosted model / API key needed
        deps_type=Deps,
        capabilities=[hooks],
        tools=[execute_transfer],
        system_prompt=(
            "You move money with execute_transfer. If it is blocked, follow the "
            "policy reason and retry within policy."
        ),
    )

    print(f"\n  Pydantic AI agent -> gated tools -> gateway {gw_url}\n")

    result = agent.run_sync(
        "transfer $10,000,000 to vendor_alpha",
        deps=Deps("ops"),
    )

    # Scan the run's message history: a gateway DENY surfaces as a ToolReturnPart
    # whose content carries DENIED_PREFIX (pydantic-ai relays our ModelRetry as a
    # "retried" tool outcome), before the model re-planned and succeeded.
    denied = [
        part
        for m in result.all_messages()
        for part in getattr(m, "parts", [])
        if str(getattr(part, "content", "")).startswith(DENIED_PREFIX)
    ]
    if denied:
        print(f"    x gateway denied the first attempt -> {denied[0].content}")
        print("    model saw the policy reason and retried within policy")
    print(f"    v final: {result.output}")
    print("\n  Done — every tool dispatch was decided by the gateway before the tool ran.\n")
    # A demo where nothing was ever denied means the gate isn't wired; surface it.
    return 0 if denied else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pydantic AI gated-agent demo")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="gateway base URL")
    ns = parser.parse_args()
    try:
        sys.exit(main(ns.url))
    except Exception as exc:  # noqa: BLE001 — friendly message for a demo
        print(f"demo failed: {exc}", file=sys.stderr)
        print(
            "is the gateway running?  make run-gateway   (and pip install -e .[pydantic])",
            file=sys.stderr,
        )
        sys.exit(2)
