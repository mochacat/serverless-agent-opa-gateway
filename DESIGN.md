# RFD 001 — Serverless Agentic OPA Guardrails

## Metadata

| Field | Value |
|-------|-------|
| Status | **Discussion** (issues being resolved before implementation) |
| Author | @mochacat |
| Reviewer(s) | Technical evaluators / consulting prospects (early- & mid-stage AI companies) |
| Date | 2026 (current date) |
| Supersedes | README.md's informal design sketch |
| Companion docs | `REQUIREMENTS.md` (this design's requirements source), `TASKS.md` (breakdown) |
| Versioning | Rego policy is git-versioned; no build artifact (loaded directly via `regopy`) |
| Primary deliverable | **A runnable, deployable artifact** (N0) |

---

## 1. Overview

We design and build a **serverless, deterministic authorization gate for AI-agent tool execution**: an **agent gateway** hosted on **API Gateway HTTP → Lambda** that evaluates **Open Policy Agent (OPA) Rego policies in-process** before any tool action executes. It uses the embedded **`regopy`** Rego interpreter — no OPA server, no sidecar, no `.wasm` build step in the demo.

The gate sits **between the agent orchestration layer (LangChain / MCP / LlamaIndex) and execution targets** (databases, clouds, internal APIs). It is a **tool-dispatch gate** — it authorizes the *action an agent proposes to take*, not the LLM request itself.

This is a **deliverable for early- and mid-stage AI companies adopting serverless**: it must double as (a) a working artifact an engineer can clone, run locally, and deploy in minutes (N0), and (b) a credible demonstration of senior infrastructure reasoning (deployment model, observability, security baseline, IaC) for the teams adopting it.

---

## 2. Goals & Non-Goals

### 2.1 Goals
- **Primary:** A runnable, deployable agent-gateway artifact (local `make` demo + real OpenTofu deploy), with **no AWS account required for the demo** (N6).
- Demonstrate the **differentiating OPA capabilities (B/E/F/G)** — argument/payload inspection, rate/deny-window, change-ticket approval, output redaction — as the hero, with default-deny as the baseline (N0).
- **In-process `<1ms` policy evaluation** via `regopy` (embedded Rego interpreter), **language-agnostic portable contract** in Rego source (N4).
- **Deterministic default-deny** (N2) and a **full audit trail** (N5).
- Reusable **OpenTofu module + runnable env** for repeatable platform deployment (N7).
- Comprehensive **unit + integration testing** and a **functional CI/CD pipeline** (`TASKS.md` breakdown) that gate merges.

### 2.2 Non-Goals
- **Not an LLM-request interceptor.** We do not filter/adjudicate the prompt stream or the model response (LLM-content filtering is a complementary layer — Bedrock Guardrails, or a future long-running LLM-proxy service using Presidio/llm-guard, §3.3a — documented, not built here). The model call is not proxied by this gateway.
- **Not a reimplementation of a managed agent gateway.** We do not rebuild routing, MCP translation, OAuth/credential exchange, or model routing — AgentCore Gateway and Bedrock Guardrails do that. We are the **complementary OPA policy-evaluation layer** on the tool-dispatch path: the controls those platforms cannot express (payload/argument inspection B, rate/deny-window + trajectory D/E, change-ticket cross-referencing F, output redaction G). Designed to sit *alongside* either platform, not replace it. The Rego contract is portable and vendor-agnostic (N4).
- **Not a full compliance program.** We build the *deterministic access-control and audit layer* that supports HIPAA/SOC2/ISO posture; we do not certify/handle BAA, data-residency, retention-as-a-program, or IR.
- **No fragile ephemeral-sandbox / RFC-8693 token-minting service in v1.** These are documented as future paths (O2); the demo is self-contained.
- **No mandatory OTel/observability dependency.** OTel is a swappable adapter seam behind an injected sink (5.4); it is never a runtime requirement for the demo.
- **No full production state store.** Trajectory/rate state uses an in-memory store for the demo behind a swappable interface; a real Datastore/Redis is a documented swap.

---

## 3. Context

### 3.1 The problem
Prompt engineering cannot guarantee deterministic guardrails against destructive tool execution. A model can hallucinate, be prompt-injected, or pick the wrong tool with wrong arguments. A system prompt is not a security boundary. We need a **hard authorization boundary** on the *actions* agents take.

### 3.2 Market / ecosystem reality
- AWS ships **AgentCore Policy (Cedar)** (GA Mar 2026, default-deny) + **Bedrock Guardrails** (GA Jun 2026) as native gates. **OPA/Rego is the correct tool for the rules Cedar cannot express**: complex payload inspection, rate-based/deny-window access, change-ticket cross-referencing. **This is the gap this project fills.**
- OPA is **CNCF-graduated**, supports portable WebAssembly as an optional compilation target, and is mature.
- Most early/mid-stage AI teams run **LangChain / MCP / Python on Lambda**, not necessarily Bedrock → the gateway is **runtime-agnostic**.

### 3.3 Deployment-model decision (already decided in requirements; recap for context)
We chose **API Gateway → Lambda** over a long-running service, because of **cost** and **autonomous multi-agent workloads**:
- Multi-agent work is **spiky/bursty/session-based**; Lambda bills only compute used; a persistent service bills wall-clock uptime (paying for idle).
- Burst fan-out scales without capacity planning.
- MCP 2026-07-28 is **stateless** → matches Lambda's per-invocation stateless model.
- Long-running service remains a *documented scale path* for sustained very-high throughput.

### 3.3a Future product: a long-running LLM proxy (separate scope, not built here)

**This gateway deliberately does NOT proxy the model request.** It sits only on the **tool-dispatch path** (authorize the action, redact tool output before it reaches the model). The model call travels directly from the agent to its provider (Bedrock, OpenAI, vLLM, …) and is never intercepted here. That is a scoped decision, not an omission — see below.

**Why the model path is out of scope for this project:**
- **Streaming is the wrong shape for Lambda.** LLM responses stream tokens, and API Gateway HTTP + Lambda does not cleanly proxy a live token stream (payload limits, connection termination, buffering). A model-path proxy is a poor serverless fit — the thing that becomes the bottleneck and fights the platform.
- **Different customer.** Early/mid-stage autonomous-agent teams on API Gateway want cheap, self-hosted, vendor-agnostic *tool authorization*. A full LLM proxy (content moderation in both directions, model routing, multi-provider redaction) serves a heavier, throughput-hungry segment that usually sits behind a real API gateway and a long-running service.

**Planned future work (documented, not built in this repo):** a **long-running LLM proxy service** — on Fargate/ECS (or similar), *not* Lambda — that:
- Proxies the model call (OpenAI-compatible protocol; Bedrock adapter), running **in-process content moderation**: `Microsoft Presidio` (PII/PHI redaction on request and response) + an injection shield such as `llm-guard`.
- Composes with this OPA tool gate: the two layers are independent and complementary (this repo = tool path; the future service = model path), sharing the same agent-gateway mental model and vendor-agnostic stance.
- Is recommended **in front of a conventional API gateway** for that customer segment (routing, auth, rate limiting at the edge; the proxy owns content policy).

This is explicitly a **separate product for a different customer**, tracked as future work — not a feature to bolt onto the serverless gateway.

### 3.4 Latency honesty
`<1ms` = **engine eval only**. End-to-end warm path ~5–15ms. In agent workflows the dominant cost is the LLM round-trip (300ms–3s+), so this is ~1–5% overhead — acceptable for autonomous multi-agent. Migrate to a service only under *sustained very-high throughput + strict single-digit-ms* requirement.

### 3.5 "Compiled to Wasm" explained (optional alternative, not the default)

Writing policies in **Rego**, then `opa build -t wasm` produces a `.wasm` binary + a JSON manifest that a Wasm engine can execute in-process. This project's **demo uses `regopy`** — an embedded Rego interpreter — which loads `.rego` source directly, no compile step needed.

- **The mainstream OPA default** is interpreted Rego (`--target rego`, e.g. Kubernetes Gatekeeper, REST authz servers). Compiling to Wasm is an **alternative performance/embedding/portability path** for non-Go runtimes (Python here). With `regopy`, the in-process eval is already sub-ms, so the `.wasm` compile step adds complexity without benefit for the demo.
- **Where `.wasm` would be useful:** if the runtime cannot embed `regopy` (e.g. Node.js, Java), or if policy must ship as a single opaque binary rather than source. The Rego source is the portable contract either way — the same rules evaluated by `regopy` (Python), `opa` CLI, or a Wasm engine in another runtime.
- **Decision recorded (D5a):** `.wasm` is a documented optional alternative, not the demo's mechanism. Not built in v1; the `build-wasm` Make target exists only as scaffolding if that path is exercised later.

---

## 4. Proposal

### 4.1 System topology

```
Agent (LangChain / MCP / LlamaIndex)          AWS Lambda Control Plane
   │  holds no credentials                        │
   │  LLM decides "delete_cluster"                │
   │    (untrusted output — NOT intercepted)      │
   │                                              │
   │  1. tools/call {tool, args, context}         │
   ├───────────────────────────────────────────►  │  API Gateway HTTP (JSON-RPC)
   │                                             │
   │                                             │  2. validate schema + enrich identity
   │                                             │     (server-side principal; agent has none)
   │                                             │  3. OPA Rego eval (in-process, `regopy`) vs
   │                                             │     args + context + session trajectory
   │                                             ├──────────────►  [policies/*.rego]
   │                                             │
   │  4. DENY (403) + policy reason              │  5. audit record + (optional) OTel spans
   │◄───────────────────────────────────────────┤     (policy_version, trace_id)
   │                                             │
   ▼                                             ▼
Tool dispatch gated         Destructive action blocked . per-call audit trail
```

### 4.1a Why a gateway at all (vs. embedding the policy in-process)?

`regopy` runs Rego in-process, so the *policy evaluation itself* costs nothing
extra — the question is **who controls it and how it's reached**. The design
chooses a shared network gateway (API Gateway → Lambda) because the product's
target is the multi-agent / service-mesh case, not a single trusted process.

**When an in-process library would be better.** If only one agent runs in one
process all owned by the same team, embedding the engine as a library is lower-
latency and simpler. That path is fully supported — `GatewayApp` is transport-
agnostic and callable directly (the local server, the Lambda handler, and the
`GatewayClient` all drive the same object). The gateway is not a claim that
this shape is *always* best.

**Why a shared gateway is the right default here:**
- **Centralized, single-source policy.** One copy of the Rego + one place to
  change it. Embedded libs mean a policy update re-deploys *every* agent and
  copies drift — which becomes a security hole.
- **Enforcement at a trust boundary you control.** When the agent is
  third-party, non-Python, or not fully trusted, you cannot ship it a library
  and trust it to enforce. The gate must live on the network path *under the
  infra/security team's* control — the agent cannot tamper with, version, or
  bypass it. This is what makes "agent holds no credentials; the gateway
  enriches identity server-side" a real property and not a slogan.
- **Agent-agnostic.** A library only gates Python agents in your process; the
  gateway gates any agent (LangChain, MCP, any runtime) over one HTTP surface.
- **One audit trail.** Enforcement and evidence live in one CloudWatch/compliance
  store, not scattered across each agent's logs — the property auditors and
  the FinOps/security program actually rely on.
- **Serverless economics.** Stateless, scale-to-zero, bills on compute used —
  the right shape for spiky/multi-agent traffic and for the stateless-MCP
  model.

**Net.** The tool-dispatch decision is in-memory either way; the gateway is the
container that makes that decision *shared, auditable, and enforced at a
boundary nobody but the platform team can cross*. When that's not needed,
embed it instead — the seam is the point, not the API Gateway.

### 4.2 Components & interfaces

| Component | Path | Responsibility |
|-----------|------|----------------|
| **Policy source of truth** | `policies/agent_guardrails.rego` | Rego rules (A–H); git-versioned |
| **Rego tests** | `policies/agent_guardrails_test.rego` | `opa test` unit tests (T1) |
| **Engine** | `src/engine/policy_engine.py` | Load Rego via `regopy` once; `evaluate(...) -> Decision` (portable API) |
| **Gateway app** | `src/gateway/app.py` | JSON-RPC; schema-validate → enrich → eval → respond → audit |
| **Lambda entrypoint** | `src/handler.py` | Adapt API Gateway event→request→gateway→response |
| **Interceptor (LangChain)** | `src/agent_interceptor.py` (`OPAGuardrailTool`) | Wraps a `BaseTool`; gates `invoke`/`ainvoke` via `src/agent_gateway_client.py` |
| **Interceptor (Pydantic AI)** | `src/agent_interceptor_pydantic.py` (`build_gateway_hooks`) | `Hooks` capability; gates `before_tool_execute`/`after_tool_execute` inside the agent's own loop |
| **Gateway client** | `src/agent_gateway_client.py` | Stdlib JSON-RPC client; fail-closed `Verdict`; reused by every framework adapter |
| **Session store (D/E)** | `src/engine/session_store.py` | In-memory swappable store; supplies trajectory/rate state |
| **Audit sink** | (interface) | Injected; CloudWatch in prod, fake in tests |
| **Metric sink** | (interface) | Injected; OTel-swallwing in local, real OTLP optional |

### 4.3 The policy (Rego) — capability surface

> **Why OPA here (positioning):** the managed gateways (AgentCore Gateway, Bedrock Guardrails) do routing, auth, and content moderation — but none evaluate Rego. B/E/F/D require cross-referencing *arguments, session trajectory, rate windows, and external approval state* — exactly what a declarative policy engine expresses. The Rego source is the portable, versioned contract (N4), evaluated in-process via `regopy`, and designed to complement, not replace, the managed platforms.

All A–H built. **Hero: B, E, F, G.** Baseline: A, C, H. D needs session store.

**Input contract to the policy (single object):**
```json
{
  "input": {
    "tool":   "delete_cluster",
    "args":   { "cluster": "prod-eu-1", "env": "production", "force": true },
    "principal": { "id": "svc-agent", "authenticated": true, "roles": ["ops"] },
    "context": {
      "approval":      { "reference": "CHG-12345", "approved": true },
      "target":        { "isolated": true, "leased": false },
      "rate_window":   { "mutations": 2, "max": 3 },
      "trajectory":    [ {"tool":"describe_cluster","at":"..."}, {"tool":"dry_run","at":"..."} ],
      "policy_version": "v7"
    }
  }
}
```

**Output contract from the policy (single object):**
```json
{
  "allow": true,
  "reason": "Action approved by policy engine.",
  "redact": ["patient_mrn", "ssn"]              // G: fields to strip from returned output
}
```

**Key rule semantics (concise; full in Rego):**
- `default allow := false`  → H (default-deny).
- **B — argument inspection:** `delete_cluster` allowed only if `env != "production"` OR approved; regex guard on resource IDs.
- **E — rate/deny-window:** count of `context.trajectory[*]` destructive ops in window ≤ `rate_window.max`.
- **F — change-ticket:** destructive requires `context.approval.reference != ""` and `approved == true` and `args.dry_run == false`.
- **G — output transform:** produce `redact` field listing sensitive fields to strip from the tool result before returning to LLM; engine/gateway applies the redaction.
- **A — verb-classes:** read tools require `principal.authenticated`; mutate require auth + safe context; destructive require the F conditions.
- **C — context/state:** destructive requires `target.isolated` and `not target.leased`.
- **D — trajectory:** destructive allowed only if a `describe` + `dry_run` appear earlier in `context.trajectory` (supplied by session store).

**Determinism (NFR4):** OPA evaluates are pure functions of `input`; the gateway injects all context. Same input → same decision, always.

### 4.4 Engine implementation (N3, N4)

**Engine:** `regopy` — the OPA Rego engine embedded **in-process** in the Lambda. No OPA server, no network hop.

```python
from regopy import Interpreter
interp = Interpreter()          # load once at cold start (module-level global)
interp.add_module("policy.rego", REGO_SOURCE)
interp.set_input({"tool": ..., "args": ..., "context": ..., "principal": ...})
decision = interp.query("data.ai.guardrails.decision")
```

- **Cold start (NFR2):** load the interpreter + policy once at module import; reuse across warm invocations.
- `evaluate(tool, args, context, principal) -> Decision{allow, reason, redact}` — the stable contract.
- **N4 / portability:** the **Rego source** (`policies/agent_guardrails.rego`) is the versioned, portable contract — evaluated with identical semantics by `regopy` (Python, in-process), the `opa` CLI, or an OPA server in another runtime. The `.wasm` artifact is an *optional* alternative, not the demo's mechanism (see D5a).
- **In-process vs sidecar in Lambda (D2, D2a):** a sidecar does not exist in Lambda — there is only one runtime process per invocation. The alternative to in-process is a *second* HTTP OPA service, which adds latency, a second cold start, and second billing. In-process wins on latency, cost, determinism, and simplicity for serverless. A shared OPA *service* (sidecar-style, hot-reloadable policy) is only the right pattern for the documented long-running-service scale path (§3.3) — and the same Rego is portable to it.

### 4.5 Gateway app behavior

Per `tools/call`:
1. **Schema-validate** (reject malformed JSON-RPC; required fields).
2. **Enrich** — inject server-side `principal` (agent contributes no credentials; least-privilege posture).
3. **Evaluate** via engine with `args + context`.
4. **DENY** → JSON-RPC error with structured `reason` (LLM reflects / replan). **ALLOW** → if `redact` present, apply G sanitanizer to simulated/supplied tool output, else return action to execute.
5. **Audit** — injected sink: `{tool, args, principal, context, decision, policy_version, reason, request_id/trace_id}` (allow AND deny).

### 4.6 Local run — full decision path, zero AWS (N0, N6)

**Requirement:** the entire gateway must run locally as a plain process and let you exercise the **full decision path end-to-end, for real** — not mocked, no AWS, no Docker/LocalStack:

```
HTTP request → gateway server → schema-validate → identity enrich
             → REAL in-process OPA Rego eval (`regopy`) → allow/deny (+ redact)
             → audit record → JSON-RPC response
```

- Rego loaded directly via `regopy` at module import — no compile step.
- `make run-gateway` → stdlib `http.server` (O4 resolution: **stdlib, zero extra deps**) serving the JSON-RPC endpoint.
- `make test-policy` → `opa test`; `make test-integration` → pytest (T2–T6).
- In-memory session store + injected fake audit/metric sinks → **zero AWS credentials** runnable.
- **Only cloud *services* are substituted** (audit sink → fake/stdout instead of CloudWatch; no API Gateway/Lambda plumbing). The **gateway logic and real Rego policy eval (`regopy`) are the real thing** — the same code that deploys.
- **Interacting locally:** `curl` the running server with a `tools/call` payload, or point the LangChain interceptor at `http://localhost:<port>`.
- **Not required locally:** LocalStack/Docker (rejected, D8) — cloud plumbing is covered by the mocked API Gateway event contract (T5) and the optional post-`apply` smoke test.

### 4.7 Deployment (N7)

- **OpenTofu module** `infra/modules/agent_gateway/`:
  - Lambda + IAM role (least-privilege) (`main_role.tf`, `lambda.tf`)
  - API Gateway **HTTP API** (`apigw.tf`) — cheap, low-latency, not REST
  - CloudWatch **log group** for audit with retention (`cloudwatch.tf`)
  - Variables: `agent_name`, `environment`, `region`, `retention_days`, `memory_size`, etc.
  - Outputs: `gateway_url`, `lambda_arn`, `audit_log_group`.
- **Runnable root config** `infra/envs/dev/` → `tofu apply`.
- **Rego is loaded from source** directly via `regopy` — no build artifact; the Lambda package includes `policies/*.rego` directly.
- **Network privacy (default public, hardening optional):** the reference
  deploy is a public API Gateway HTTP → Lambda. The module exposes
  `vpc_subnet_ids`/`vpc_security_group_ids` (attach the Lambda to a VPC) and
  a PrivateLink/`execute-api` **interface VPC endpoint** is the documented
  path for keeping agent↔gateway traffic internal. Not enabled by default;
  API Gateway keeps a public execution path, so truly private-only needs a
  resource-policy lockdown (out of scope). See `docs/deployment.md` §7.1.

### 4.8 Testing & CI (from REQUIREMENTS §5.1/5.2)

| Tier | Scope | Tool |
|------|-------|------|
| T1 | Rego semantics | `opa test` |
| T2 | Engine unit | pytest |
| T3 | Gateway schema/decision/audit | pytest (+ injected fake sinks — resolves O5) |
| T4 | End-to-end safe-vs-unsafe | pytest (in-memory store + fake sinks) |
| T5 | Lambda handler (API GW events) | pytest |
| T6 | Coverage ≥80% | pytest-cov |

**CI (`.github/workflows/ci.yml`)**: `opa check` → `opa test` → `ruff format/check` → pytest+cov → `tofu fmt -check` + `tofu validate`. Runs on push + PR. Deploy job is a manual `workflow_dispatch` and gated. **Portability gate:** tests run against the same Rego source loaded at runtime — no build artifact, no drift.

> **No live Lambda is needed for tests.** The test tiers run **locally with zero AWS** (N6). `handler.py` is a thin adapter mapping an **API Gateway event dict → gateway app → response dict**; tests feed it a faithful **mock event** (identical to API Gateway's contract) and assert the response, using the same injected fakes (audit, metrics, session store) as all other tiers. The *real* Lambda is what the OpenTofu module deploys; an optional **deployment smoke test** after `tofu apply` hits the live `gateway_url` with safe/unsafe payloads — that's a manual/CI-deploy step documented in `docs/deployment.md`, separate from the local test tiers.

### 4.9 Observability (from REQUIREMENTS 5.4)

- **Trace propagation (correct model):** the agent is the **root** of the trace and already carries the `trace_id` (plus its own span) when it calls the gateway — propagated via standard trace-context headers (`traceparent: 00-<trace_id>-<parent_span_id>-01`). The gateway **extracts** that inbound context and its `policy_decision` span uses the **same `trace_id`** with a **`parent_id`** pointing at the caller's span. So the gateway *extends the agent's trace*; it does not invent one. For the standalone demo (no agent present) the gateway generates a fresh `trace_id`. That id is written into the audit record (N5) so the decision is correlatable to its trace and vice versa.
- **Injected metric sink** returns decision/deny-rate/`policy_eval_ms` counters (correct shape locally, zero deps).
- **OTel adapter seam** (in `docs/observability.md`): `policy_decision` span schema (with inbound-parent extraction); export via OTLP to any backend (Grafana/SigNoz/Tempo/etc.); async/after-audit; **never on the critical path**; AWS-native tools never coupled in; vendor-agnostic.

### 4.10 Security posture (from REQUIREMENTS §5.3)

- **OWASP LLM Top-10 (2026) Covered:** LLM01 (Prompt Injection, via tool-dispatch gate), LLM02 (Sensitive Info Disclosure, via G redaction), LLM03 (Excessive Agency, via default-deny/verb-class), LLM06 (Unbounded Consumption, via rate), LLM10 (Improper Output Handling, via G). The other five (04, 05, 07, 08, 09) are documented as boundaries, never overclaimed.
- **Agent holds no credentials.** gateway enriches identity server-side; infra IAM is least-privilege.

### 4.11 Adapter layer & the normalized tool-call schema (the stable contract)

**Rego never reads a framework's raw payload.** Policy evaluates a small,
gateway-owned **normalized schema**; a thin **adapter** per agent framework
copies the requested tool-name and arguments into it. This keeps the policy
layer stable across every framework instead of being rewritten for each one.

Normalized `input` (the *only* shape Rego sees — see `4.3`):

| field | type | meaning |
|---|---|---|
| `input.tool` | string | requested tool name (e.g. `"execute_transfer"`) |
| `input.args` | object | the tool's arguments |
| `input.principal` | object | caller identity, enriched server-side (`authenticated`, `authorized_scopes`) |
| `input.context` | object | cross-call state (trajectory D, rate E, approval F, target C), injected from session store |
| `input.industry` | string | selects the policy package (healthcare/fintech/secops) |

Framework shapes map onto it by copying two fields (`tool` name + `args`):

| Ecosystem | source of name | source of args |
|---|---|---|
| OpenAI | `message.tool_calls[].function.name` | `.function.arguments` (JSON string) |
| Anthropic | `content[]` block `type:"tool_use"` → `.name` | `.input` (object) |
| **MCP** (open standard) | `tools/call` `params.name` | `params.arguments` (object) |
| LangGraph | emits OpenAI-shape `tool_call` | same as OpenAI |

**Reference adapters (built).** Two frameworks are actually implemented:

  * **LangChain** — `src/agent_interceptor.py`: `OPAGuardrailTool` subclasses
    `BaseTool` and overrides `invoke`/`ainvoke` to gate then delegate, because it
    is the most widely used agent framework in Python and its `@tool` model is
    closest to this normalized schema.
  * **Pydantic AI** — `src/agent_interceptor_pydantic.py`: `build_gateway_hooks()`
    returns a `Hooks` **capability** (`@hooks.on.before_tool_execute` /
    `@hooks.on.after_tool_execute`) you attach to the `Agent`. Because pydantic-ai
    calls the tool functions itself, the gate fires *inside* its own execution
    loop — DENY raises `ModelRetry` (tool never runs, model re-plans), redaction
    substitutes the output. Same reusable bundle attaches to every agent an org
    runs (the "one control plane, agent-agnostic" story, §4.1a).

MCP remains the one true open standard (Linux Foundation;
Anthropic/OpenAI/Google tooling support it), and its `tools/call` already uses
`name`+`arguments` — the smallest, most defensible mapping. The gateway is
**not** an MCP *server*; it is a policy decision point an MCP-based agent routes
`tools/call` into. Other frameworks are documented as one page of field-mapping
each, added only for frameworks actually supported (per `REQUIREMENTS` — no
speculative adapters).

**Versioning discipline:** the normalized schema is the stable API. Changing
it (e.g. `input.args` → `input.arguments`) breaks every policy, so it is
documented, versioned, and adapters are built to it — never policy-to-vendor-
format.

---

## 5. Decision Log / Alternatives Considered

| # | Decision | Chosen | Rejected / Notes |
|---|----------|--------|------------------|
| D1 | Deployment model | **API Gateway → Lambda** (serverless) | Long-running service rejected for cost + spiky multi-agent + stateless-MCP (see §3.3); documented scale path. |
| D2 | Enforcement location | **In-process OPA engine (`regopy`)** | OPA sidecar/server rejected: **a sidecar does not exist in Lambda** (one process per invocation). The only alternative is a second HTTP OPA service → latency + second cold start + second billing. In-process wins on latency/cost/determinism for serverless (D2a). |
| D2a | In-process vs sidecar (Lambda) | **In-process** | A sidecar-style shared OPA *service* (hot-reloadable, centralized policy) is only right for the long-running-service scale path (§3.3) — and the same Rego is portable to it (N4). |
| D3 | Interception point | **Gate tool dispatch** | LLM-request interception rejected for the *serverless gateway* (can't stop destructive effects, provider-coupled, streaming is a poor Lambda fit). Complementary content layer is Bedrock Guardrails (documented). |
| D3a | Model-path proxy | **Out of scope for the serverless gateway; separate long-running service (§3.3a)** | The model request/response is NOT proxied here. A future long-running LLM proxy (Fargate/ECS + Presidio/llm-guard, fronted by a conventional API gateway) owns the content layer for a different, throughput-hungry customer segment. Not built in this repo. |
| D4 | Portability | **Rego source is the portable contract** | Evaluated with identical semantics by `regopy` (Python), the `opa` CLI, or an OPA server in any runtime. A literal `.wasm` artifact is an *optional* alternative (D5a), not the demo's mechanism. |
| D5 | `regopy` (embedded Rego engine) | Accepted (N3/N4) | In-process, sub-ms, no network. Verified working on Python 3.14. The invented `python-opa` package does **not** exist on PyPI — rejected. |
| D5a | Optional `.wasm` artifact | Documented alternative, not the demo | If a literal `.wasm` artifact is required, options are `regopy` bundle entrypoint or raw `wasmtime` against OPA's Wasm ABI — more glue, deferred until a real requirement exists. |
| D6 | Policy sourcing | **Rego source in git; no build artifact needed** | With `regopy`, the `.rego` is loaded directly (no compile step). A `.wasm` build step (and its `build-wasm` Make target) is only needed if D5a is exercised — recorded so CI can add it without rework. |
| D7 | Local server | **stdlib `http.server`** (O4) | Flask rejected: zero extra deps (N6), keeps demo dependency-free. |
| D8 | Test double for AWS (O5) | **Dependency-injected fake sinks** | moto/localstack rejected: adds weight + AWS coupling; DI keeps everything local + fast. |
| D8a | Local run scope (N6) | **Full decision path runs locally, for real** (HTTP → validate → **real in-process Rego eval** → audit → response); only cloud *services* are faked | Mocking the policy eval would make the local demo meaningless. No LocalStack/Docker required. Same gateway code as deployed. |
| D9 | Session store (D/E) | **In-memory, swappable interface** | Production DynamoDB/Redis is a documented swap; keeps Lambda stateless. |
| D10 | Sandbox / token-minting (O2) | **Documented future path, not built** | Keeps demo self-contained; principle (least-privilege, no agent creds) is stated. |
| D11 | Observability | **OTel adapter seam, vendor-agnostic** | Never mandatory; OTLP export async/off-critical-path; no AWS observability coupling. |

---

## 6. Open Questions (to resolve during build)

- **O1** — Exact JSON-RPC request/response schema (method, params, error shape). *Proposed default*: method `tools/call`, params `{tool, args, context}`, response `{result:{allow, reason, redact?}}` or JSON-RPC error with `data.reason`.
- **O2** — How much of sandbox/scoped-token to document vs. build. *Resolution*: document only (D10), but make the "no agent credentials" property real via identity enrichment.
- **O3** — Wasm commit vs. CI-built. *Resolution*: superseded — the demo loads Rego directly via `regopy`; a `.wasm` build (D5a) is only added if the optional artifact path is exercised.
- **O4** — Local server. *Resolution*: stdlib `http.server` (D7).
- **O5** — Test double. *Resolution*: dependency-injected fake sinks (D8).
- **Q6** — Exact coverage threshold and CI tool version pinning (to keep CI functional and reproducible).
- **Q7** — Whether to include a tiny OpenTofu `tofu validate` CI step that needs a mock provider, or gate deploy entirely behind `workflow_dispatch` + docs.

---

## 7. Work Breakdown → `TASKS.md`

The tasks below are derived from this design and tracked in `TASKS.md`, knocked off one by one.

1. Scaffold repo structure + `requirements.txt`/`requirements-dev.txt`, `pyproject.toml` (pytest-cov, ruff).
2. Write Rego policy `policies/agent_guardrails.rego` implementing A–H, heroes first.
3. Write Rego tests `policies/agent_guardrails_test.rego` (T1).
4. Makefile: `test-policy`, `test-integration`, `test-all`, `run-gateway`, `build-zip`, `deploy-local`.
5. Engine `src/engine/policy_engine.py` + `SessionStore` (+ unit tests T2).
6. Gateway `src/gateway/app.py` (schema-validate/enrich/eval/audit) + injected sinks (+ unit tests T3).
7. Integration tests T4 (`tests/test_gateway_integration.py`) + `tests/testdata/*.json`.
8. Lambda handler `src/handler.py` (+ tests T5).
9. LangChain interceptor `src/agent_interceptor.py`.
10. Coverage gate T6 wired into Makefile + pyproject.
11. OpenTofu module + dev env → `tofu apply` (repeatable deploy).
12. `.github/workflows/ci.yml` functional pipeline (opa check/test, ruff, pytest+cov, tofu validate).
13. Docs: `docs/architecture.md`, `docs/security.md`, `docs/observability.md`, `docs/deployment.md`, README rewrite, `docs/compliance.md`.
14. Final gate: fresh clone → `make test-all` green, `make run-gateway` demo works, CI green.

---

## 8. References

- OWASP GenAI LLM Top 10 (2026) — `GenAI-Security-Project/GenAI-LLM-Top10`
- AWS AgentCore Policy (Cedar) & Bedrock Guardrails GA notes (2026)
- MCP 2026-07-28 (stateless) spec
- OPA v1.20/1.21 (CNCF-graduated), `regopy` (embedded Rego interpreter)
- Agent-gateway pattern references (InfoQ, bex.co) — used to shape topology
- RFD format (Oxide/community) — this document's structure
- `REQUIREMENTS.md` (requirement source of truth)