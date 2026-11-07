"""LangChain `BaseTool` interceptor — the reference framework adapter (T5.1).

This is the *thin client-side hook* that makes a real LangChain agent gate every
tool dispatch through the agent gateway. It is NOT the product (the gateway is);
it is the one idiomatic example showing how any framework's tool call maps onto
the gateway's normalized ``tools/call`` schema (DESIGN §4.11).

How it hooks in (idiomatic, version-stable):

  * ``OPAGuardrailTool`` subclasses :class:`langchain_core.tools.BaseTool` and
    wraps a real tool. It overrides the **public** ``invoke()`` — present across
    langchain-core 0.3.x/1.x — rather than the internal ``_run``/``_arun``.
  * On every call it asks the gateway first (``GatewayClient.check``). ALLOW →
    delegate to the wrapped tool and return its result. DENY → return a
    policy-error ``ToolMessage``/string carrying the reason, which LangChain
    hands back to the model so it can re-plan. The tool's real implementation
    never runs on a deny.

``langchain-core`` is an **optional** extra (``pip install -e .[langchain]``),
imported lazily; the gateway core stays LangChain-free. Import this module only
when LangChain is installed.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from src.agent_gateway_client import GatewayClient, Verdict

# Prefix that marks a policy denial in the string/ ToolMessage returned to the
# model, so it is unambiguous this was the gateway, not the tool.
DENIED_PREFIX = "POLICY DENIED"


class OPAGuardrailTool(BaseTool):
    """A LangChain tool wrapped so every invocation is gated by the gateway.

    Construct with :meth:`wrap`; pass the real tool, the gateway client, and
    the industry label the gateway's policy dispatcher expects.
    """

    # NOTE: BaseTool is a pydantic model; these are pydantic fields.
    wrapped: BaseTool
    client: GatewayClient
    industry: str = "fintech"
    principal_id: str | None = None
    session_id: str | None = None

    @classmethod
    def wrap(
        cls,
        tool: BaseTool,
        *,
        client: GatewayClient | None = None,
        industry: str = "fintech",
        principal_id: str | None = None,
        session_id: str | None = None,
    ) -> OPAGuardrailTool:
        """Wrap ``tool`` so its dispatch is gated by the gateway.

        The wrapper exposes the same name/description/args_schema as the real
        tool, so an agent sees one tool — it just cannot bypass the gate.
        """
        return cls(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            wrapped=tool,
            client=client or GatewayClient(),
            industry=industry,
            principal_id=principal_id,
            session_id=session_id,
        )

    # -- interception -------------------------------------------------------
    # BaseTool routes invoke()/ainvoke() to _run()/_arun(); putting the gate
    # here covers both the sync and async agent paths with one implementation.
    def _run(self, *args: Any, **kwargs: Any) -> Any:
        normalized_args = self._normalize_args(args, kwargs)
        verdict = self._check(normalized_args)
        if not verdict.allowed:
            return self._denied(verdict)
        # Delegate through the wrapped tool's PUBLIC invoke() — it owns the
        # config/validation plumbing; calling its private _run would require
        # re-supplying framework internals. ``input`` is the normalized dict.
        result = self.wrapped.invoke(normalized_args)
        return verdict.redacted_output if verdict.redacted_output is not None else result

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        normalized_args = self._normalize_args(args, kwargs)
        verdict = self._check(normalized_args)
        if not verdict.allowed:
            return self._denied(verdict)
        result = await self.wrapped.ainvoke(normalized_args)
        return verdict.redacted_output if verdict.redacted_output is not None else result

    # -- helpers ------------------------------------------------------------
    def _normalize_args(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        """Map a LangChain tool call onto the gateway's ``args`` object.

        ``invoke()`` normalizes a dict or string input into keyword arguments
        before calling ``_run``/``_arun``. If LangChain handed us a single
        positional value (a bare-string tool), wrap it under ``{"input": ...}``
        — LangChain's own convention for single-string tools.
        """
        if args and not kwargs and len(args) == 1:
            value = args[0]
            return dict(value) if isinstance(value, dict) else {"input": value}
        return dict(kwargs)

    def _check(self, args: dict[str, Any]) -> Verdict:
        return self.client.check(
            tool=self.name,
            args=args,
            industry=self.industry,
            principal_id=self.principal_id,
            session_id=self.session_id,
        )

    def _denied(self, verdict: Verdict) -> str:
        """Return a policy-reason string instead of running the tool.

        LangChain wraps a tool's string return in a ``ToolMessage`` (attaching
        the ``tool_call_id`` it owns), so the model sees the denial and can
        re-plan — e.g. acquire a change ticket — while the real tool never
        executes. Returning a string (not a ``ToolMessage``) is the convention
        at this level: ``_run`` has no ``tool_call_id`` to attach.
        """
        return f"{DENIED_PREFIX}: {verdict.reason}"
