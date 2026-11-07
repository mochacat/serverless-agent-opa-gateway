# Agentic OPA Guardrails 🛡️🤖

> **Declarative Security & Infrastructure Policy Engine for AI Agent Tool Execution**

A serverless, low-latency control plane that enforces deterministic security boundaries on autonomous AI agent execution loops. By evaluating Open Policy Agent (OPA) Rego policies **in-process** via the embedded `regopy` interpreter inside a Python AWS Lambda runtime, this system dynamically blocks hallucinated or out-of-bounds agent tool executions based on real-time infrastructure state and telemetry.

## Quickstart

```bash
# Install dependencies
pip install -r requirements.txt

# Start the local gateway (zero AWS)
make run-gateway
```

In another terminal, try the gate:

```bash
# ALLOW — safe read
curl -s -X POST http://127.0.0.1:8080/tools/call \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"tool":"get_balance","industry":"fintech",
                 "principal_id":"ops"}}' | jq .

# DENY — money movement needs a change ticket (HERO F)
curl -s -X POST http://127.0.0.1:8080/tools/call \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"tool":"execute_transfer","industry":"fintech",
                 "args":{"amount":100,"beneficiary":"payroll"},
                 "principal_id":"ops"}}' | jq .

# DENY — shell injection in target blocked (HERO B)
curl -s -X POST http://127.0.0.1:8080/tools/call \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"tool":"block_ip","industry":"secops",
                 "args":{"target":"*; rm -rf /"},
                 "principal_id":"ops"}}' | jq .
```

> **Note:** In the local demo, `principal_id` is a lookup key into the demo
> identity store. In production, the API Gateway handles authentication (IAM,
> Cognito, Lambda authorizer, or mTLS) before the request reaches the Lambda —
> the gateway enriches identity server-side, never from the caller's payload.
> See `docs/security.md` for the full production auth model.

Run the full demo across all industries and scenarios:

```bash
make demo
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Agent Process                         │
│                                                             │
│  ┌──────────┐    ┌──────────────────────┐   ┌──────────┐   │
│  │   LLM    │───▶│  Framework Adapter   │──▶│   Tool   │   │
│  │ (model)  │    │ (interceptor/hook)   │   │ (actual) │   │
│  └──────────┘    └───────┬──────────────┘   └──────────┘   │
│                          │                                  │
│                          │ 1. POST /tools/call              │
│                          │    (JSON-RPC, SigV4-signed)      │
└──────────────────────────┼──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   Gateway (API Gateway → Lambda)             │
│                                                             │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────┐ │
│  │   Schema    │───▶│   Identity   │───▶│  Policy Engine │ │
│  │  Validate   │    │  Enrichment  │    │  (regopy OPA)  │ │
│  └─────────────┘    └──────────────┘    └───────┬────────┘ │
│                                                  │         │
│                     ┌────────────────────────────┼────────┐ │
│                     │  Decision                  │        │ │
│                     │  ┌──────┐    ┌────────┐   │        │ │
│                     │  │ALLOW │    │  DENY  │   │        │ │
│                     │  │(G    │    │(reason │   │        │ │
│                     │  │ red- │    │  to    │   │        │ │
│                     │  │ act) │    │ agent) │   │        │ │
│                     │  └──────┘    └────────┘   │        │ │
│                     └────────────────────────────┘        │ │
│                                                           │ │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
│  │ Audit Sink   │  │ Metrics Sink │  │ Session Store   │  │
│  │ (stdout→CW)  │  │ (in-memory,  │  │ (trajectory,    │  │
│  └──────────────┘  │  OTLP seam)  │  │ rate window)    │  │
│                    └──────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Production auth flow

In production, the gateway uses **AWS_IAM** auth on API Gateway. The flow is
a single request — no second hop:

```
Agent (EC2/EKS)
  │  IAM role: app-agent-role
  │  AWS SDK SigV4-signs the request automatically
  │
  ▼
API Gateway ─── verifies the SigV4 signature ─── rejects if invalid
  │
  ▼
Lambda ─── reads the verified IAM identity from the event context
           (the agent never sent its own identity)
  │
  ▼
Rego policy evaluates the tool call against that principal
```

The agent holds **no credentials** — the gateway resolves identity
server-side from the IAM session. The agent just needs `execute-api:Invoke`
permission on the gateway URL. See `docs/security.md` for the full setup.

### The adapter pattern

The gateway exposes a single JSON-RPC endpoint `POST /tools/call`. Any agent framework can integrate by writing a thin adapter that:

1. **Intercepts** the tool call before it executes (framework hook/wrapper)
2. **POSTs** the normalized payload to the gateway
3. **ALLOW** → the tool runs normally (with optional G-redaction applied to output)
4. **DENY** → the tool never runs; the policy reason flows back to the model for re-planning

The normalized `tools/call` schema:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "tool": "execute_transfer",
    "industry": "fintech",
    "args": { "amount": 100, "beneficiary": "payroll" },
    "context": {
      "target": { "isolated": true, "leased": false },
      "approval": { "reference": "CHG-42", "approved": true },
      "rate_window": { "max": 3 },
      "trajectory": [{"tool": "describe_cluster"}, {"tool": "dry_run"}]
    },
    "session_id": "sess-abc123"
  }
}
```

| Field | Required | Description |
|---|---|---|
| `tool` | Yes | The tool being called |
| `industry` | No | Routes to the right policy surface (`fintech`, `healthcare`, `secops`); unknown → fail-closed |
| `args` | No | Tool arguments (inspected by policy for B — amount caps, allowlists, PHI scope, etc.) |
| `context` | No | Real-time state: target isolation, approval ticket, trajectory history, rate budget |
| `session_id` | No | Key for trajectory (D) and rate-window (E) state across calls |

On ALLOW:

```json
{
  "jsonrpc": "2.0", "id": 1,
  "result": {
    "allowed": true,
    "action": { "tool": "execute_transfer", "args": {...} },
    "redacted_output": {...}
  }
}
```

On DENY:

```json
{
  "jsonrpc": "2.0", "id": 1,
  "error": {
    "code": -32000, "message": "Tool dispatch denied",
    "data": { "reason": "BLOCKED: money movement requires an approved change ticket (dual-control, feature F)." }
  }
}
```

### Reference adapters

| Framework | File | How it hooks in |
|---|---|---|
| **LangChain** | `src/agent_interceptor.py` | `OPAGuardrailTool` wraps a `BaseTool`, overrides `invoke()` to gate through the gateway before delegating. |
| **Pydantic AI** | `src/agent_interceptor_pydantic.py` | `build_gateway_hooks()` returns a `Hooks` capability that intercepts `before_tool_execute` / `after_tool_execute`. |
| **Strands** | `src/agent_interceptor_strands.py` | `GatewayGuardrail` implements `HookProvider` with `BeforeToolCallEvent` / `AfterToolCallEvent` handlers. |

```bash
make demo-langchain   # LangChain agent
make demo-pydantic    # Pydantic AI agent
make demo-strands     # Strands SDK agent
```

All three send the same `tools/call` schema. Writing an adapter for a new framework is just one HTTP POST.

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

## Security / OWASP coverage

The gateway directly covers **OWASP GenAI LLM Top-10 (2026)**:

| LLM | Threat | How the gate covers it |
|-----|--------|------------------------|
| LLM01 | Prompt Injection | Tool arguments are inspected by policy (B); injected tool names or malformed args hit default-deny (H) |
| LLM02 | Sensitive Info Disclosure | Output redaction (G) strips PHI / PII / credentials from tool results before the model sees them |
| LLM03 | Excessive Agency | Verb-class gating (A) + destructive-require-ticket (F) + trajectory precondition (D) — the model cannot escalate beyond its permitted tool class |
| LLM06 | Unbounded Consumption | Rate windows (E) cap destructive operations per session; hard timeout on the Lambda (10s) |
| LLM10 | Improper Output Handling | The gate enforces the boundary — the agent holds no credentials, the gateway enriches identity server-side |

The remaining five (LLM04/05/07/08/09) are documented **boundaries** in
`docs/security.md` — the gateway never overclaims coverage.

## Capabilities (A–H)

The gateway ships with **three industry reference policies** — fintech,
healthcare, and secops. Each defines a set of hypothetical tools to
demonstrate the same policy capabilities with different nouns and rules:

| Industry | Read tools | Mutate tools | Destructive tools | What it shows |
|---|---|---|---|---|
| **Fintech** | `get_balance`, `get_account`, `list_transactions`, `get_rate` | `create_transfer`, `update_beneficiary`, `send_otp` | `execute_transfer`, `approve_payment`, `revoke_card`, `close_account` | Money movement needs a change ticket (F), amount caps & beneficiary allowlist (B), rate limits (E) |
| **Healthcare** | `read_patient_record`, `get_lab_results`, `list_appointments`, `query_phi` | `update_patient_demographics`, `submit_claim`, `schedule_appointment` | `delete_patient_record`, `anonymize_patient_record`, `bulk_export_records` | PHI scope authorization (B), output redaction of patient identifiers (G) |
| **SecOps** | `list_hosts`, `get_host`, `list_incidents`, `search_logs` | `quarantine_host`, `isolate_host`, `update_playbook` | `revoke_token`, `block_ip`, `kill_process`, `reset_cluster_state` | Shell-injection blocking in targets (B), target isolation requirement (C) |

> **These are reference examples, not a production tool catalog.** The tools
> shown here (`get_balance`, `query_phi`, `block_ip`, etc.) are hypothetical
> — they exist only in the Rego policies to demonstrate the gate's capabilities.
> When you deploy this gateway, you'll replace these with your own tools and
> write Rego rules that match your actual use case. The `policies/` directory
> is the single source of truth; add a new `.rego` file for your industry, or
> customize the existing ones.

The same policy capabilities apply across all three industries:

| Capability | Hero | Description |
|---|---|---|
| **A** Verb-class gating | | Read / mutate / destructive — each industry defines its own tool classes |
| **B** Argument/payload inspection | ⭐ | Amount caps, beneficiary allowlists, PHI scope authorization, shell-injection blocking |
| **C** Context/state | | Target isolation, lease detection |
| **D** Trajectory | | Destructive actions require `describe` + `dry_run` in the session history |
| **E** Rate/deny-window | ⭐ | Cap destructive ops per session window |
| **F** Change-ticket approval | ⭐ | Destructive money movement / secops actions require a human-approved ticket |
| **G** Output redaction | ⭐ | Strip sensitive fields (PHI, account numbers, credentials) from tool results |
| **H** Default-deny | | `default allow := false` — unknown tools, industries, or args are blocked |

## Framework adapters (demoed)

Reference adapters prove the gate works *inside* real agent frameworks (each is an
optional extra, so the gateway core stays framework-free):

- **LangChain** — `OPAGuardrailTool` wraps a `BaseTool` and gates every
  `invoke`/`ainvoke`. `make demo-langchain`
- **Pydantic AI** — `build_gateway_hooks()` returns a `Hooks` capability that
  intercepts `before_tool_execute`/`after_tool_execute` inside the agent's own
  loop. `make demo-pydantic`
- **Strands Agents SDK** — `GatewayGuardrail` implements `HookProvider` with
  `BeforeToolCallEvent` / `AfterToolCallEvent` handlers. `make demo-strands`

All send the same normalized `tools/call` (see Architecture above) and show
DENY → model re-plans within policy.

## Deploy

The gateway deploys as API Gateway HTTP → Lambda via OpenTofu. See
`docs/deployment.md` for the full path (AWS prerequisites, configuration,
post-deploy smoke test, cold-start notes). For the production auth and
network model, see `docs/security.md`.

## Project layout

```
├── handler.py              # AWS Lambda entrypoint (thin API Gateway adapter)
├── src/
│   ├── engine/             # PolicyEngine (regopy) + SessionStore
│   ├── gateway/            # GatewayApp (JSON-RPC), redact, HTTP server
│   ├── audit/              # Audit record builder + injectable sinks
│   └── metrics/            # Decision counters + OTLP-ready seam
├── policies/               # OPA Rego policy source (base + fintech/healthcare/secops)
├── tests/                  # All pytest tiers (zero AWS, injected fakes)
├── infra/                  # OpenTofu module + dev root config
└── examples/               # Customer-facing demos by framework
    ├── langchain/           #   LangChain tool interceptor
    ├── pydantic/            #   Pydantic AI agent hooks
    ├── strands/             #   Strands SDK gateway agent
    └── verify/              #   Raw JSON-RPC demo + CI policy smoke
```