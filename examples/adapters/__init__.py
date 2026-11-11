"""Client-side framework adapters — NOT deployed with the gateway.

These modules sit on the **client side** of the gateway: an agent's process
uses them to gate its tool dispatches through the running gateway before any
tool executes. They are deliberately **excluded** from the Lambda package
(deploy copies ``src/`` + ``policies/`` only) — the server never needs to
know which agent framework is calling it.

Reference adapters:
- :mod:`~examples.adapters.gateway_client` — framework-agnostic JSON-RPC client
  (``GatewayClient``), the one thing every adapter shares. Fail-closed.
- :mod:`~examples.adapters.agent_interceptor` — LangChain ``OPAGuardrailTool``
  that subclasses ``BaseTool`` and gates ``invoke``/``ainvoke``.
- :mod:`~examples.adapters.agent_interceptor_pydantic` — Pydantic AI
  ``Hooks`` capability that gates ``before_tool_execute``.
- :mod:`~examples.adapters.agent_interceptor_strands` — Strands Agents SDK
  ``HookProvider`` that gates ``BeforeToolCallEvent``.

Each interceptor imports its framework lazily (the optional extras in
``pyproject.toml``), so the core stays framework-free.
"""
