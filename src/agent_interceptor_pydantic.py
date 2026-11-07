"""Pydantic AI capability — the second framework adapter (T5).

A pydantic-ai ``Agent`` declares its tools as plain async functions and the
library calls them itself; there is no external caller we can wrap the way
``OPAGuardrailTool`` wraps a LangChain ``BaseTool``. The idiomatic pydantic-ai
interception points are **tool lifecycle hooks**, provided by the :class:`Hooks`
capability (``@hooks.on.before_tool_execute`` / ``@hooks.on.after_tool_execute``).
That is the hook family pydantic-ai documents for "observability, auditing, or
light interception" — exactly an authorization gate.

This module builds such a capability and asks the gateway "may this principal
perform this tool with these arguments?" before the tool runs:

  * ``@hooks.on.before_tool_execute`` — fires with the **validated** call args
    immediately before execution (after pydantic schema validation). ALLOW →
    return the args unchanged so the tool runs. DENY → raise
    :class:`pydantic_ai.exceptions.ModelRetry` with the policy reason, which
    pydantic-ai hands back to the model so it can re-plan. The tool never runs.
  * ``@hooks.on.after_tool_execute`` — if the gateway returned a *redacted*
    output (feature G), substitute it for the tool's real return value before
    the agent sees it.

The redaction verdict is threaded between the two hooks via a
:class:`contextvars.ContextVar`. That is race-free here because pydantic-ai
runs each concurrent tool call in its own ``asyncio`` task (own context), and
the before → execute → after hooks for one call are awaited sequentially in the
same task.

Wire it up as a capability — the same object is a reusable bundle you attach to
every agent an org runs (the gateway's "one control plane, agent-agnostic"
story, DESIGN §4.1a):

    from src.agent_interceptor_pydantic import build_gateway_hooks
    agent = Agent(model, capabilities=[build_gateway_hooks(industry="fintech")])

``pydantic-ai`` is an **optional** extra (``pip install -e .[pydantic]``),
imported lazily; the gateway core stays framework-free. Import this module only
when ``pydantic-ai`` is installed.
"""

from __future__ import annotations

import contextvars
from collections.abc import Mapping
from typing import Any

# lazy: import this module only when pydantic-ai is installed
from pydantic_ai import RunContext
from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from src.agent_gateway_client import GatewayClient, Verdict

# Prefix that marks a policy denial in the ModelRetry reason sent back to the
# model, so it is unambiguous this was the gateway, not a thrown tool error.
DENIED_PREFIX = "POLICY DENIED"

_verdict_stash: contextvars.ContextVar[Verdict | None] = contextvars.ContextVar(
    "gateway_verdict", default=None
)


def _industry_of(deps: Any, default: str) -> str:
    from_deps = getattr(deps, "industry", None) if deps is not None else None
    return str(from_deps or default)


def _field_of(deps: Any, name: str, default: str | None) -> str | None:
    value = getattr(deps, name, None) if deps is not None else None
    return default if value is None else value


def _context_of(deps: Any, default: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    value = getattr(deps, "context", None) if deps is not None else None
    return value if value is not None else default


def build_gateway_hooks(
    *,
    client: GatewayClient | None = None,
    industry: str = "",
    principal_id: str | None = None,
    session_id: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> Hooks:
    """Build a :class:`Hooks` capability that gates every tool dispatch.

    Args:
        client: The :class:`GatewayClient` (defaults to ``127.0.0.1:8080``).
        industry: Industry label the gateway's policy dispatcher expects. A
            ``deps.industry`` attribute on the run takes precedence.
        principal_id: Identity passed as ``principal_id`` to the gateway. A
            ``deps.principal_id`` attribute on the run takes precedence (the
            agent itself holds no credentials — identity is enriched at the
            gateway, DESIGN §4.1a).
        session_id: Cross-call session for trajectory/rate rules (features
            D/E). A ``deps.session_id`` attribute on the run takes precedence.
        context: Optional cross-call ``input.context`` (e.g. an approved change
            ticket for feature F, an isolated target for feature C). A
            ``deps.context`` attribute on the run takes precedence.
    """
    client = client or GatewayClient()

    hooks = Hooks()

    @hooks.on.before_tool_execute()
    async def gate(
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        verdict = client.check(
            tool=call.tool_name,
            args=args,
            industry=_industry_of(ctx.deps, industry),
            principal_id=_field_of(ctx.deps, "principal_id", principal_id),
            session_id=_field_of(ctx.deps, "session_id", session_id),
            context=_context_of(ctx.deps, context),
        )
        _verdict_stash.set(verdict)
        if not verdict.allowed:
            raise ModelRetry(f"{DENIED_PREFIX}: {verdict.reason}")
        return args

    @hooks.on.after_tool_execute()
    async def redact(
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        verdict = _verdict_stash.get()
        return (
            result
            if verdict is None or verdict.redacted_output is None
            else verdict.redacted_output
        )

    return hooks
