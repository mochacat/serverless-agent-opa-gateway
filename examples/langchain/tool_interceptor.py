#!/usr/bin/env python3
"""LangChain reference demo — a real agent's tools gated by the gateway.

Unlike ``examples/verify/demo_agent.py`` (which posts raw JSON-RPC), this wires
the **actual LangChain integration**: real ``langchain_core`` tools wrapped by
:class:`src.agent_interceptor.OPAGuardrailTool`. Every tool call is gated by
the running gateway (real Rego) before the tool runs. On DENY the policy reason
flows back and a (scripted) agent re-plans — the loop an LLM would drive.

Prereqs:
  * ``pip install -e .[langchain]`` (the optional interceptor)
  * a gateway running locally: ``make run-gateway`` (another terminal)

Usage:  python3 examples/langchain/tool_interceptor.py [--url http://127.0.0.1:8080]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as `python examples/langchain/tool_interceptor.py` from the repo root
# without installing the package: put the repo root on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from langchain_core.tools import tool

from src.agent_gateway_client import GatewayClient
from src.agent_interceptor import DENIED_PREFIX, OPAGuardrailTool


# --- real LangChain tools (the agent's capabilities) -----------------------
@tool
def get_balance(account: str) -> str:
    """Read an account balance."""
    return f"balance for {account}: $4,200"


@tool
def execute_transfer(amount: int, beneficiary: str) -> str:
    """Move money to a beneficiary."""
    return f"transferred ${amount} to {beneficiary}"


def _gated(tools, client: GatewayClient) -> dict[str, OPAGuardrailTool]:
    """Wrap every tool so the agent cannot bypass the gateway."""
    return {
        t.name: OPAGuardrailTool.wrap(t, client=client, industry="fintech", principal_id="ops")
        for t in tools
    }


def main(gw_url: str) -> int:
    client = GatewayClient(gw_url)
    tools = _gated([get_balance, execute_transfer], client)

    print(f"\n  LangChain agent -> gated tools -> gateway {gw_url}\n")

    # An "agent turn": try a benign read, then an over-cap transfer.
    steps = [
        ("get_balance", {"account": "acct_123"}),
        ("execute_transfer", {"amount": 999999, "beneficiary": "offshore-llc"}),
    ]

    failures = 0
    for name, args in steps:
        print(f"  agent calls {name}({args})")
        result = tools[name].invoke(args)
        if isinstance(result, str) and result.startswith(DENIED_PREFIX):
            print(f"    x gateway DENY -> {result}")
            print("    agent re-plans (would ask the user for a change ticket)")
            failures += 1
        else:
            print(f"    v allow -> tool returned: {result}")

    print("\n  Done — every call was decided by the gateway before the tool ran.\n")
    # A demo where nothing is ever denied means the gate isn't wired; surface it.
    return 0 if failures else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LangChain gated-agent demo")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="gateway base URL")
    ns = parser.parse_args()
    try:
        sys.exit(main(ns.url))
    except Exception as exc:  # noqa: BLE001 — friendly message for a demo
        print(f"demo failed: {exc}", file=sys.stderr)
        print(
            "is the gateway running?  make run-gateway   (and pip install -e .[langchain])",
            file=sys.stderr,
        )
        sys.exit(2)
