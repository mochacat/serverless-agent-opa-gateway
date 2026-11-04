# Agentic OPA Guardrails 🛡️🤖

> **Declarative Security & Infrastructure Policy Engine for AI Agent Tool Execution**

A serverless, low-latency control plane that enforces deterministic security boundaries on autonomous AI agent execution loops. By evaluating Open Policy Agent (OPA) Rego policies **in-process** via the embedded `regopy` interpreter inside a Python AWS Lambda runtime, this system dynamically blocks hallucinated or out-of-bounds agent tool executions based on real-time infrastructure state and telemetry.

## Why a gateway (not an in-process library)?

The Rego evaluation is always in-process (`regopy`). The gateway exists so that
decision is *shared, audited, and enforced at a boundary the agent cannot
cross*:

- **Centralized policy** — one copy of the Rego, one place to change it. Agents
  can't run drift, stale, or tampered versions.
- **Enforcement at a trust boundary with ownership in the infra/security team**
  — the gate lives on the network path under the platform's control, not
  inside untrusted agent code.
- **Agent-agnostic** — any agent (LangChain, MCP, any runtime) gates over one
  HTTP surface; no per-language library.
- **One audit trail** — enforcement and evidence in one compliance/CloudWatch
  store, not scattered across agents.
- **Serverless economics** — stateless, scale-to-zero, bills on compute used.

When a single trusted team runs one agent in one process, an embedded library
is the better fit and is fully supported (the engine is transport-agnostic).
See `DESIGN.md §4.1a` for the full reasoning.

## Framework adapters (demoed)

Reference adapters prove the gate works *inside* real agent frameworks (each is an
optional extra, so the gateway core stays framework-free):

- **LangChain** — `OPAGuardrailTool` wraps a `BaseTool` and gates every
  `invoke`/`ainvoke`. `make demo-langchain`
- **Pydantic AI** — `build_gateway_hooks()` returns a `Hooks` capability that
  intercepts `before_tool_execute`/`after_tool_execute` inside the agent's own
  loop. `make demo-pydantic`

Both send the same normalized `tools/call` (DESIGN §4.11) and show DENY →
model re-plans within policy.

