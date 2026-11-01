# Requirements — Serverless Agentic OPA Guardrails

> **Goal:** A serverless, deterministic authorization gate for AI-agent tool execution, architected as an **agent gateway** on **API Gateway → Lambda**, with **in-process OPA (Rego compiled to WebAssembly)** as the enforcement engine.
>
> **Primary deliverable — a runnable, deployable artifact.** An engineer must be able to clone the repo and, in minutes, (1) run the gateway locally via `make` and see safe-vs-unsafe decisions, and (2) deploy it for real via OpenTofu. The consulting narrative (industries, OWASP, OTel, managed-vs-custom) lives in `docs/` **behind** the working code — it is secondary to the artifact. **The hero of the project is the differentiating OPA capabilities** (argument/payload inspection, output redaction, rate/deny-window, change-ticket approval); "default-deny gate" is the table-stakes baseline, not the pitch. It targets **early- and mid-stage companies adopting AI** that run serverless on Lambda.

---

## 1. Problem & Motivation

Prompt engineering and system prompts **cannot** guarantee 100% deterministic guardrails against destructive tool execution. A model can hallucinate, be prompt-injected, or simply pick the wrong tool with the wrong arguments — and a system prompt telling it "be careful" is not a security boundary. We need a **hard authorization gate** between the agent orchestration layer (LangChain / MCP / LlamaIndex) and execution targets (databases, clouds, internal APIs).

This gate is an **agent gateway**: a dedicated service that sits between agents and their tools and makes a deterministic allow/deny decision per tool call, **before** anything executes. It is **state-aware** — the decision considers real-time context about the current workload (is the target isolated? is this a fresh human-approved change window? is the caller within its rate limit? is the resource leased?) — not just a static per-user permission table.

### Market context (justifies the architecture)
- AWS now ships **AgentCore Gateway** (GA, unified managed gateway: routing, MCP translation, OAuth/Security Guard, ingress+egress auth, credential exchange, observability) + **Bedrock Guardrails** (GA Jun 2026, LLM input/output content moderation, PII redaction). Both are native first-party layers.
- **Neither evaluates OPA/Rego.** AgentCore's authorization control is OAuth/Security Guard (Cedar/declarative role rules); Bedrock Guardrails does content moderation, not policy evaluation. OPA/Rego remains the correct tool for the rules those platforms **cannot** express: **complex payload/argument inspection (B), rate/deny-window + session-trajectory cross-referencing (D/E), and change-ticket/approval cross-referencing (F)**. **This is the gap this project fills** — the portable OPA policy-evaluation layer on the tool-dispatch path.
- **Positioning: complementary, not competitive.** We do not try to out-feature the managed gateways. We own the OPA-only controls they lack, and are designed to sit alongside them (AgentCore for routing/auth, Bedrock Guardrails for input/output moderation) as a defense-in-depth layer. A client can use this gateway *with* either platform — the Rego contract is portable and vendor-agnostic.
- OPA is **CNCF-graduated** and shipped v1.20/v1.21 — mature, with a compiler that emits portable WebAssembly.
- **Deployment reality:** most early/mid-stage AI teams are on **LangChain / MCP / Python on Lambda**, not necessarily Bedrock. The gateway must be **runtime-agnostic** so it demos and deploys regardless of whether the client is LangChain, MCP, or AgentCore.

---

## 1.1 Target Industries (where this is needed most)

The gate is most valuable where tool actions have **high blast radius**, the org has **audit/compliance obligations**, and workflows are genuinely **agentic** (real tool-call volume). Ranked by fit:

| Tier | Industry | Why it fits | Mapping to our features |
|------|----------|-------------|------------------------|
| **Strongest** | **Healthcare / HealthTech** | Highest-value sensitive data (PHI) + strictest controls (HIPAA Minimum Necessary / access control). Agents automating claims, coexisting chart-summarization, ref workflows = real tool-call volume to gate. | PHI-by-content argument gating (B); output redaction before PHI reaches the LLM (G); default-deny least-privilege (A/H); per-action immutable audit (N5); separation-of-duties via approval refs (F). |
| **Strongest** | **FinServ / Fintech & Banking** | Money movement = ultimate destructive blast radius (`execute_transfer`, `approve_payment`, `trade`). Regulators (SOX, PCI, FINRA) require dual-control/4-eyes + immutable audit. | Destructive-verb-requires-human-approval + dry-run (F); default-deny (A/H); immutable audit (N5); rate/deny-window (E). |
| **Strong** | **Cloud/SaaS infra & DevOps** | The tool surface *is* infrastructure (`delete_cluster`, `drop_table`, `force_failover`); high operational blast radius, lower regulatory friction. | Verb-class gating (A); context/state-aware (C); trajectory (D). |
| **Strong** | **Cybersecurity / SecOps** | Zero-trust mindset; AI response actions are destructive high-stakes; default-deny + isolation + audit aligns with SOC 2 / ISO 27001 mindset. | Default-deny (A/H); audit (N5); ephemeral isolation. |
| **Moderate** | Insurance, GovTech, regulated logistics | Real access-control/retention/audit needs + growing agentic use; softer urgency than top tiers. | Audit (N5); access control (A). |
| **Weak / not the pitch** | Gen-AI content, sales, consumer chat | Low blast radius, no audit obligation; the gate would be avoidable overhead. | n/a — do not lead the demo with these. |

**Positioning consequence for the demo:** the example policy set must make buyers in the strong fits self-identify. The demo **must showcase a regulated, high-stakes example** — anchor on **healthcare PHI** (`query_phi` / `read_patient_record`, with content-based gating + output redaction) and **fintech money-movement** (`execute_transfer` requiring human approval + dry-run) — alongside the generic infra tools. This is a configuration difference (Rego is config), *not* a code difference; the product is the gate, the industry only changes the nouns and rules.

---

## 2. Non-Negotiable (Priority 0)

| # | Requirement | Why |
|---|-------------|-----|
| N0 | **Primary deliverable is a runnable, deployable artifact** (not a narrative). Local `make` demo + real OpenTofu deploy both work out of the box; docs are secondary to working code. | This is the make-or-break property. If an engineer can't clone-and-run/deploy quickly, the project fails its purpose regardless of how good the story is. |
| N1 | **Serverless, Lambda-native.** Gateway is `API Gateway HTTP → Lambda`. | Primary stated target; matched to README. |
| N2 | **Deterministic default-deny.** `default allow := false`; unmapped tools are blocked. | Table-stakes baseline; agents must not bypass constraints on unmapped invocations. |
| N3 | **In-process policy evaluation (`<1ms`).** The OPA Rego engine runs **in-process** (via `regopy`, the embedded Rego interpreter) — loaded at cold start, reused across warm invocations. **No network hop to an OPA sidecar/server**, no REST daemon. | The core perf claim and architectural decoupling: decision latency is the eval only, not an HTTP round-trip. |
| N4 | **Language-agnostic policy contract.** The **Rego source** is the portable, versioned contract — evaluated by any OPA runtime (Python via `regopy`, Go, Node, the `opa` CLI, an OPA server) with identical semantics. The demo uses the in-process Python binding. (A literal `.wasm` artifact is an optional alternative — see DESIGN D5/D5a.) | Portability lives in the *policy* (Rego), not a runtime. Same rules, any evaluator. |
| N5 | **Auditable.** Every allowed/denied agent action → audit log, with policy version + decision + reason + principal + action + `trace_id`. | Compliance; the "witness that doesn't confabulate." |
| N6 | **Runs entirely locally with `make`.** The **full gateway decision path must run as a plain local process with no AWS**: real HTTP endpoint → schema-validate → identity enrich → **real in-process Rego policy eval** → allow/deny + redact → audit record → JSON-RPC response. Local testing must exercise this **end-to-end** (not mocked), with zero AWS credentials and no LocalStack/Docker required. | This is the make-or-break property: an engineer must be able to clone the repo and see real safe-vs-unsafe decisions locally in ~10 seconds. If the policy eval path can't run locally, the artifact is not demonstrable. |
| N7 | **Repeatable, deployable infrastructure (OpenTofu).** | "You don't just write code — you deploy repeatable platform infra." |

**Priority ordering:** N0 (runnable/deployable artifact) and the **differentiating OPA capabilities (B/G/E/F)** are the top priority. Default-deny (N2) and the standard gate must be present and correct, but the demo's *emphasis* is the OPA-specific controls that a managed stack can't trivially express. Something that is a correct default-deny gate but offers nothing beyond that **does not meet the bar** — it would be a thing people buy elsewhere. This is explicitly tested: the integration suite stresses the differentiating capabilities hardest.

---

## 3. Architecture (chosen approach + rationale)

### 3.1 Cloud gateway / deployment model: **API Gateway → Lambda**
Chosen over a long-running service (Go/Python daemon on Fargate/ECS). Rationale — tied to **cost** and **autonomous multi-agent workflows**, the two hardest constraints:

- **Cost — Lambda wins for multi-agent workloads.** Autonomous multi-agent workflows are **spiky, bursty, session-based**: agents idle during LLM round-trips (seconds), then fire a tool call, then idle. Lambda bills **only milliseconds of compute actually used**; a persistent service bills **wall-clock uptime**. A continuously-running gateway for a fleet that is mostly waiting on LLM latency is **paying for idle**. Serverless is often single-digit $/mo at early/mid-stage scale vs. ~$30–70+/mo for an always-on Fargate instance.
- **Scale-out without capacity planning.** Fan-out of N sub-agents → N concurrent gate checks. Lambda auto-scales to thousands of concurrent invocations; a service requires sizing, autoscaling, and paying for headroom.
- **Statelessness matches the protocol.** MCP (2026-07-28) is now **stateless**; each `tools/call` is an independent policy decision with no session affinity. Lambda's stateless per-invocation model maps directly onto this.
- **Cost scales with activity** — the billing curve tracks the business curve, ideal pre-revenue.

**The long-running service remains a documented scale path** (same codebase, different wrapper) for the future state of *sustained very-high throughput* where cold-start/per-invocation overhead would dominate a sub-ms p99 — but it is **not** the default demo.

> **Distinct from a future LLM proxy (documented, not built here):** this gateway **does not proxy the model request** — the model call goes directly from agent to provider. A *separate* long-running LLM-proxy service (Fargate/ECS, not Lambda) that does content moderation on the model path (Presidio/llm-guard) is planned future work for a **different, throughput-hungry customer segment** and belongs **behind a conventional API gateway**. See DESIGN.md §3.3a. It is not a feature of this serverless gateway.

### 3.1.2 Deployment model: reusable module + runnable env + offline demo
Infrastructure is a **reusable OpenTofu/Terraform module** (the "reusable platform component, not a one-off script" signal) **plus** a runnable root config that instantiates it — the pattern professional platform teams use.

- **`infra/modules/agent_gateway/`** — parameterized module (inputs: `agent_name`, `environment`, `region`, `retention_days`, ...; outputs: API Gateway URL, Lambda ARN, log group name). Resources: Lambda + IAM role (least-privilege), API Gateway **HTTP API** (cheap, low-latency — not REST), CloudWatch **log group** for audit with retention.
- **`infra/envs/dev/`** — root config instantiating the module for a real `terraform apply` (repeatable env provisioning).
- **Two independent tracks, one codebase:**
  1. **Local demo** (default): `make ...` → in-process Rego eval, **zero AWS credentials**. Evaluator-friendly 10-second clone→run (N6).
  2. **Real deployment** (alpha): OpenTofu `apply` → API Gateway + Lambda + CloudWatch (N7).
- **Engine choice:** **OpenTofu** (Terraform-compatible, open-source); module HCL is drop-in for `terraform` too.

### 3.1.3 Latency reality (honest framing)
The `<1ms` claim is precise about **engine eval only**, not end-to-end. Document it as such:

| Component (warm path) | Added latency |
|---|---|
| OPA Rego eval (in-process, `regopy`) | **~0.1–0.5ms** — the "<1ms" claim |
| HTTP round-trip to API Gateway | ~1–5ms |
| API Gateway forward + integration | ~1–3ms |
| Lambda handling (warm) | ~1–5ms |
| CloudWatch log (async) | ~0 (async) / 5–20ms (sync) |
| Lambda cold start (first call) | 200ms–1s+ |

**End-to-end gateway overhead:** ~5–15ms warm. **Why this is fine:** in agent workflows the gateway is *not* on a user-facing critical path — the dominant latency is the **LLM round-trip (300ms–3s+)**, so 5–15ms is ~1–5% overhead, invisible to both user and agent loop. **Migration trigger** (documented scale path): sustained very-high throughput *and* a strict single-digit-ms end-to-end requirement → move the same codebase to a long-running service (Fargate/ECS). For early/mid-stage autonomous multi-agent, serde is the right cost/scale trade (see §3.1 Cost).

### 3.2 Enforcement engine: **in-process OPA Rego (`regopy`)**
- Rego is the source of truth, git-versioned in `policies/`.
- **`regopy`** embeds the OPA Rego engine in-process; the Lambda loads `policies/agent_guardrails.rego` at cold start and evaluates it in-memory — `<1ms`, no network, no OPA server.
- **Portable contract:** the Rego source is evaluated identically by `regopy` (Python), the `opa` CLI, or an OPA server in any runtime (N4). A literal `.wasm` artifact is an optional alternative, not the demo's mechanism (DESIGN D5a).

### 3.3 Control flow — where OPA sits (interception model)
> **The gateway does NOT intercept the LLM call.** It gates the **tool dispatch**: after the LLM decides to call a tool, the gateway evaluates `{tool, args, context, trajectory}` before anything executes. This is the correct pattern because:
> 1. It is the only point that can stop a destructive **effect** — blocking LLM I/O cannot stop a model that is later injected/hallucinates into a destructive call.
> 2. It is **provider-agnostic** — only needs tool name/args, independent of which model produced them (satisfies N4 portability).
> 3. It has a **clean contract**: `{tool, args, principal, context} → {allow, reason}`.
>
> LLM-*input* filtering (prompt-injection, PII) is a **complementary outer layer** (e.g. Bedrock Guardrails) — documented, not built here. The hard, non-negotiable boundary we build is tool-dispatch gating.

```
Agent (LangChain / MCP / LlamaIndex)              AWS Lambda Control Plane
   │  holds no credentials                            │
   │  LLM decides "delete_cluster"                    │
   │    (untrusted output — NOT intercepted)          │
   │                                                  │
   │  1. intent: tools/call "delete_cluster"          │
   ├───────────────────────────────────────────────►  │  API Gateway (HTTP / JSON-RPC)
   │                                                 │
   │                                                 │  2. validate schema + enrich identity
   │                                                 │  3. OPA Rego eval in-process vs.
   │                                                 │     args + context + session trajectory
   │                                                 ├──────────────►  [Rego policies]
   │                                                 │
   │  4. DENY (403) + dynamic policy reason          │  5. write CloudWatch audit log
   │◄───────────────────────────────────────────────┤     (policy version, decision, reason)
   │                                                 │
   ▼                                                 ▼
Tool dispatch gated          Destructive action blocked · per-call audit trail
```
- Per-call decision returns: `decision.allow`, `decision.reason`, policy version, evaluated tool/args/context.
- **State-awareness without breaking statelessness:** OPA stays pure. Trajectory (session history) and rate counters stream `input.context.trajectory` into each eval; the *state* lives in a swappable session store beside OPA, not inside it.

---

## 4. Functional Requirements

### 4.1 OPA capability surface to build (A–H) — "build everything"
The gateway demonstrates the full breadth of what OPA can do for agent guardrails. The product is the gate; these are the policy dimensions it enforces. **A–H are all built.** OPA stays pure/stateless; cross-call state streams in via `input`.

> **Emphasis (N0):** all A–H are **built and tested**. The **differentiating capabilities are B, E, F, G** (**HERO** tier) — the ones a managed default-deny stack can't trivially express, and the reason OPA earns its place. **A, C, H are baseline** (table-stakes correctness). **D and E additionally require the swappable session store** (cross-call state). The demo and the integration suite lead with B/E/F/G; every capability has at least one crisp, tested example.

| ID | Capability | Model | Built-in policy | Tier |
|----|-----------|-------|-----------------|------|
| B | **Argument / payload inspection** | Evaluate the *actual args*, not just the tool name | `delete_cluster` on `env="production"` denied; `env="dev"` allowed; regex/attribute guards on resource IDs | **HERO** |
| E | **Rate-based / deny-window** | Enforce mutation budgets per window | ≤N destructive/mutations per time window from a given principal/session | **HERO** |
| F | **Change-ticket / approval cross-ref** | Destructive actions require a real human approval reference | destructive requires `approval.reference != ""` + `dry_run == false` | **HERO** |
| G | **Output redaction / transform** | Transform tool output before it returns to the LLM (strip secrets/PHI) | redact PHI / secret fields from allowed tool results | **HERO** |
| A | **Action / verb-class** | Gate by `tool` verb class (read / mutate / destructive) | `delete_*`, `drop_*`, `reset_*`, `force_*` destructive; read tools authenticated-only | baseline |
| C | **Context / state-aware** | Decision depends on live workload context (target isolation, resource lease) | deny destructive when target not isolated / leased elsewhere | baseline |
| D | **Trajectory / multi-call history** | Decision references prior events in the session | `delete` only allowed after a `describe` + `dry_run` occurred earlier this session | built (needs session store) |
| H | **Default-deny + minimal surface** | Unmapped tools blocked until a rule exists | `default allow := false`; new destructive tools fail closed | baseline |

**Cross-call state (D, E) honesty:** in a stateless Lambda, trajectory and rate state live in a **swappable session store** (DynamoDB/Redis in prod; in-memory in the local demo) and are **passed into `input.context.trajectory` / rate counters** on each eval. OPA never holds mutable state itself — it reasons over what the gateway injects. This keeps decisions fast and deterministic while still being trajectory-aware. (State store interface is dependency-injected → swappable for prod.)

### 4.2 Policy engine (Rego `policies/agent_guardrails.rego`)
- **Default deny**: `default allow := false`. Unmapped tools are blocked with a clear "default deny" reason.
- **Verb-class model** (gateway-grade, from the agent-gateway pattern):
  - **Read** tools (`list_instances`, `get_status`, `describe_cluster`, `get_logs`) → allowed when `principal.authenticated == true`.
  - **Mutate** tools (`restart_*`, `update_*`, `deploy_*`) → require authenticated principal **and** a safe context (resource not leased elsewhere, within rate window).
  - **Destructive** tools (`delete_cluster`, `drop_database_table`, `reset_cluster_state`, `force_failover`) → require authenticated principal **and** isolated/healthy target **and** `approval.reference != ""` (a human change-ticket) **and** `dry_run == false`.
- **State-aware authorization**: evaluate `input.context` (isolation, approval, rate-window, lease) at eval time — *agent workload context, appropriate to a serverless control plane*.
- **Structured decision** object so the LLM gets a reason to reflect and re-plan (not just a bare 403).
- Extensible: new destructive tools are **denied by default** until a rule is written (the "minimal tool surface" security property).

### 4.3 Gateway app (`src/gateway/app.py`) — the product
- MCP-style **JSON-RPC over HTTP** endpoint: `tools/call` with `{tool, args, context, principal}`.
- Per call:
  1. Schema-validate the request shape.
  2. Enrich with server-side identity (principal) — agent contributes **no credentials**.
  3. Evaluate OPA Rego in-process (`regopy`) with `context`.
  4. **DENY** → structured error + policy reason (LLM can reflect). **ALLOW** → return the tool action to be executed.
  5. Write an **audit record** (allowed/denied; `tool`, `args`, `principal`, `context`, `decision`, `policy_version`, `reason`, `request_id`).
- Serves both deployment forms via a thin HTTP wrapper.

### 4.4 Engine (`src/engine/policy_engine.py`) — the portable artifact loader
- Loads `policies/agent_guardrails.wasm` + manifest + data bundle.
- `evaluate(tool_desired, context, principal) -> {allow, reason}` — the stable API contract.
- Uses `regopy` (embedded Rego engine) for in-process eval; loads `policies/agent_guardrails.rego` directly.
- Loads once at cold start; reuses across warm invocations.

### 4.5 Lambda entrypoint (`src/handler.py`)
- AWS Lambda `handler(event, context)`.
- Adapts API Gateway event → policy request → gateway app → decision response.
- Same app runs under a local server for `make run-gateway`.

### 4.6 LangChain agent interceptor (`src/agent_interceptor.py`)
- `OPAGuardrailInterceptor` — client-side hook; **calls the gateway** (control plane) before executing a `BaseTool`.
- On DENY: returns the reason to the LLM so it can re-plan (matches README's LangChain example).
- Demonstrates the runtime-agnostic client posture (works against Lambda URL / local server).

### 4.7 Scoped-credential posture
- Agent holds **no credentials**. The gateway exposes only the scoped authorization needed per call (principle stated; lightweight in the demo — no full RFC 8693 token service in v1, documented as a future path). Infra IAM is least-privilege.

---

## 5. Non-Functional Requirements

| # | Requirement | Target |
|---|-------------|--------|
| NFR1 | Decision latency | `<1ms` in-process eval (cold start excluded) |
| NFR2 | Cold start | Load `regopy` interpreter + Rego once at init; reuse across warm invocations |
| NFR3 | Audit completeness | 100% of calls logged (allow **and** deny) |
| NFR4 | Determinism | Same input → same decision (pure function; no external auth at eval time) |
| NFR5 | Portability | Rego source is the portable, versioned contract; evaluated by any OPA runtime |
| NFR6 | Reproducible build | `make` targets; CI runs policy tests + pytest |
| NFR7 | Security baseline | Fail-closed; policy versioned in git; least-privilege IAM in infra |

---

## 5.1 Testing Strategy (unit + integration)

Testing is multi-tier so each layer is verified independently and end-to-end. **All tests must pass locally with zero AWS credentials** (N6) — AWS interactions are dependency-injected and faked in tests.

**The full gateway decision path runs locally, for real (N6):** T4 exercises an actual HTTP request through the gateway, including the **real in-process Rego policy evaluation** (via `regopy`) — only cloud *services* (audit sink, Lambda/API-GW plumbing) are substituted. Mocking the policy eval is explicitly disallowed: that would make the local demo meaningless. No LocalStack/Docker is required.

| Tier | Scope | Tool | What it verifies | Local |
|------|-------|------|------------------|-------|
| **T1 Rego unit tests** | `policies/*.rego` semantics | `opa test` | Each policy rule (A–H) and each deny/allow branch exercises correctly; default-deny holds | ✅ no AWS |
| **T2 Engine unit tests** | `src/engine/policy_engine.py` | `pytest` | Loads Rego via `regopy`; `evaluate(tool,args,context,principal)->{allow,reason}` returns correct decision + reason for representative inputs; determinism | ✅ no AWS (in-process eval) |
| **T3 Gateway unit tests** | `src/gateway/app.py` | `pytest` | Schema validation rejects malformed requests; identity enrichment; correct allow/deny JSON-RPC responses; audit record written (via injected fake logger) | ✅ no AWS (injected audit sink) |
| **T4 Gateway integration** | end-to-end: sample request → gateway → engine → decision | `pytest` | **Safe vs unsafe** scenarios through the real in-process engine: e.g. read allowed, destructive-without-approval denied, production-delete denied, PHI redaction applies, trajectory (D) & rate (E) enforce. | ✅ no AWS (in-memory session store + fake audit sink) |
| **T5 Lambda handler tests** | `src/handler.py` adapting API Gateway events | `pytest` | Correct request→policy→response mapping; correct JSON-RPC/HTTP shape for API Gateway; deny returns structured reason; audit emitted | ✅ no AWS (injected sinks) |
| **T6 Coverage gate** | pytest + coverage | pytest-cov | Enforce a coverage threshold (e.g. ≥80%) on engines/gateway/handler | ✅ no AWS |

**Test data:** `tests/testdata/*.json` holds mocked request payloads — safe vs unsafe reads, destructive-with/without approval, production-delete, PHI query, trajectory sequences, rate-budget sequences.

**Policy-safety invariant enforced at all tiers:** every scenario that would be a real blast-radius failure (delete on production without approval, PHI to a non-authorized principal, rate-limit bypass) asserts **deny**, and every benign scenario asserts **allow**. This is the "fail-closed correctness" the whole project rests on.

**Emphasis in tests (N0):** the integration suite gives the **HERO capabilities (B/E/F/G) the most scenarios and the hardest constraints** — argument-inspection edge cases, rate-limit boundaries, change-ticket/approval permutations, and PHI/secret-redaction completeness — because those are the ones that prove the project's differentiating value. Baseline (A/C/H) is tested for correctness but with fewer adversarial cases.

**OWASP traceability (ties §5.1 to §5.3):** each OWASP item marked **Covered** in §5.3 has at least one test asserting its behavior — e.g. prompt-injection coerced into `delete_cluster` → deny (LLM01/B/C), PHI in tool output → redacted (LLM02/G), unapproved destructive → deny (LLM03/F), mutation budget exceeded → deny (LLM06/E), secret in output → redacted before return (LLM10/G). A test exists for every Covered claim; no Covered item is asserted without a passing test.

---

## 5.2 CI/CD Pipeline (functional, not decorative)

`.github/workflows/ci.yml` must be a real, gate-keeping pipeline that a merge/PR trusts. A functional pipeline is: **fails the build on any broken policy, failing test, unmapped-tool regression, or policy that fails to load.** It runs on **push + PR**.

| Stage | Command | Purpose / gate |
|-------|---------|----------------|
| 1. Policy static analysis | `opa check policies/` | Syntax/style valid before anything runs |
| 2. Rego unit tests | `opa test policies/ -v` (T1) | Policy semantics correct; must pass |
| 3. Policy loads via `regopy` | a smoke check: load `policies/agent_guardrails.rego` with `regopy` and evaluate a fixture | Proves the deployed evaluation path works (the engine actually parses the Rego) |
| 4. Python format/lint | `ruff format --check` + `ruff check` | Consistent, lint-clean |
| 5. Python unit + integration | `make test-integration` (T2–T5) with `pytest-cov` (T6) | All tiers pass; coverage gate enforced |
| 6. OpenTofu validation | `tofu fmt -check` + `tofu validate` (in `infra/envs/dev`) | IaC is valid, formatted — a real deploy is possible |
| 7. (Optional) Deploy job | manual `workflow_dispatch` → `tofu apply` to a dev env | Real deploy path, gated to manual trigger (no risk from CI) |

> A `make build-wasm` stage is **not** in the default pipeline because the demo evaluates Rego directly (D5/D6). It becomes a stage only if the literal `.wasm` artifact path (D5a) is exercised.

**No-drift guarantee (N4):** tests and the deployed Lambda consume the **same `policies/agent_guardrails.rego`** from git — the tested policy *is* the deployed policy (no compiled artifact to drift).

**Developer loop:** `make test-policy` (T1), `make test-integration` (T2–T6), `make run-gateway` — the same targets CI calls, so "CI green == local green."

---

## 5.3 OWASP Top 10 for LLM Applications (2026) coverage mapping

The gateway's controls map to the **authoritative OWASP GenAI LLM Top 10** (2026 edition — the current release from `GenAI-Security-Project/GenAI-LLM-Top10`). This is the security baseline that makes the compliance / regulated-industry pitch concrete. **Items marked Covered are the ones the gateway itself must enforce; items marked Documented / Out-of-scope are explicitly not gateway controls** and are stated as boundaries so the demo never overclaims.

| OWASP ID | Item | Compliance in this project | Where / status |
|----------|------|---------------------------|----------------|
| **LLM01:2026** | Prompt Injection | **Covered (defense-in-depth)** | Even if a prompt-injection coerces the model into calling a destructive tool, the OPA tool-dispatch gate still checks real args + context + approval before execution (features B, C, F). LLM-*input* injection filtering is a complementary outer layer (Bedrock Guardrails) — documented, not built. |
| **LLM02:2026** | Sensitive Information Disclosure | **Covered — headline** | Output redaction (G) strips PHI/secrets from tool output before it reaches the model/context; argument-gating by PHI content (B). Directly serves healthcare/fintech. |
| **LLM03:2026** | Excessive Agency | **Covered — central** | Default-deny (H), verb-class gating (A), minimal tool surface. The gateway's whole purpose is constraining what an agent may do. |
| **LLM04:2026** | Supply Chain | Documented | Policy/model versions are git-pinned; CI gates artifacts. Not a runtime control — documented as an org-level responsibility. |
| **LLM05:2026** | Data & Model Poisoning | Out-of-scope | Training/data-pipeline concern; a runtime tool-gate cannot prevent it. Documented as a non-goal/boundary. |
| **LLM06:2026** | Unbounded Consumption | **Covered** | Rate-based / deny-window (E): mutation budgets cap runaway destructive consumption per window. |
| **LLM07:2026** | Misinformation | Out-of-scope | App/grounding concern; not a tool-authorization control. Documented as non-goal. |
| **LLM08:2026** | Hidden Context Exposure | Documented | The gateway passes only necessary context to OPA; trajectory injection (D) is controlled/curated server-side. |
| **LLM09:2026** | Vector & Embedding Weaknesses | Out-of-scope | RAG/infrastructure concern; documented as non-goal. |
| **LLM10:2026** | Improper Output Handling | **Covered** | *Tool-output* handling: output redaction/validation (G) before effects occur and before results return to the model. |

**Net:** the gateway directly **covers LLM01, LLM02, LLM03, LLM06, LLM10** — the five that a tool-dispatch authorization gate legitimately owns — and **clearly scopes the other five** (LLM04, LLM05, LLM07, LLM08, LLM09) as boundaries handled elsewhere. A demo/consulting pitch leads with the five Covered items and is explicit about the five boundaries.

---

## 5.4 Observability (OpenTelemetry) — vendor-agnostic, OTLP-native, but swappable

Observability is **OpenTelemetry-native and vendor-agnostic** (no preference for AWS observability tools; OTLP export is config, not code). The gateway is the natural distributed-tracing choke point because every agent action crosses it.

- **Decision traceability (required):** every call carries a `trace_id`/`request_id`. That same id is written into the audit record (N5) **and** into OTel span attributes when tracing is on — so a decision is correlatable to its trace and vice versa.
- **OTel as a *swappable adapter*, never mandatory:** the core gateway has **zero OTLP coupling** — it writes to a generic, dependency-injected trace/metrics sink. A thin **OTLP exporter module** (env-configured endpoint) is a drop-in implementation of that sink. Because Lambda is stateless and per-invocation, exporting on the critical path costs latency, so the exporter runs **async/batched after the audit write** and is **optional per environment** — the observer must never gate the decision.
- **Span/metrics schema:** a `policy_decision` span (attributes `tool`, `action`, `decision`, `reason`, `policy_version`, `trace_id`) + counters (decisions, denials, `policy_eval_ms`). Wiring + schema live in `docs/observability.md`.
- **Boundaries (honest):** the audit record remains the mandatory compliance artifact (N5); OTel traces/metrics are the observability enhancement on top. **AWS products are never coupled into the core** — ADOT/X-Ray are only one possible exporter target, chosen per environment alongside Grafana OSS/Cloud, Tempo, SigNoz, Jaeger, etc. And because Lambda has no long-lived listener, the standard choices are an in-process OTLP exporter (async, end-of-invocation) or a local Collector layer — a deploy config, not a design change.

---

## 6. Deliverables (file map)

| Path | Purpose |
|------|---------|
| `policies/agent_guardrails.rego` | Policy source of truth |
| `policies/agent_guardrails_test.rego` | Native Rego unit tests |
| `src/engine/policy_engine.py` | In-process Rego evaluator via `regopy` (portable API) |
| `src/gateway/app.py` | Gateway service (MCP/JSON-RPC) |
| `src/handler.py` | Lambda entrypoint |
| `src/agent_interceptor.py` | LangChain BaseTool guardrail client |
| `tests/test_policy_engine.py` | Engine unit tests (T2) |
| `tests/test_gateway_unit.py` | Gateway schema/decision/audit unit tests (T3) |
| `tests/test_gateway_integration.py` | Safe-vs-unsafe end-to-end tests (T4) |
| `tests/test_handler.py` | Lambda handler tests (T5) |
| `tests/testdata/*.json` | Mocked agent request payloads (safe/unsafe/PHI/trajectory/rate) |
| `pyproject.toml` | pytest + coverage config (T6 threshold), ruff config |
| `infra/modules/agent_gateway/` | Reusable OpenTofu module (Lambda + IAM, API Gateway HTTP, CloudWatch audit) |
| `infra/envs/dev/` | Runnable root config → `tofu apply` (repeatable deployment) |
| `Makefile` | `test-policy`, `test-integration`, `test-all`, `run-gateway`, `build-zip` (a `build-wasm` target is optional/D5a-only) |
| `.github/workflows/ci.yml` | Functional pipeline: opa check, opa test, regopy-load smoke, lint, pytest+cov, tofu validate (push + PR) |
| `requirements.txt` | Runtime deps (boto3, regopy, requests) + `requirements-dev.txt` (pytest, pytest-cov, ruff) |
| `docs/architecture.md` | Deployment-model + managed-vs-custom reasoning |
| `docs/security.md` | OWASP LLM Top-10 (2026) mapping (§5.3) + threat boundaries in depth |
| `docs/observability.md` | OTel adapter seam: `policy_decision` span schema, ADOT layer wiring, metric counters |
| `README.md` | Rewritten: explains *why API Gateway → Lambda for multi-agent* (§3.1) |
| `DESIGN.md` | Design doc (written after this) |
| `TASKS.md` | Task tracker (knocked off one by one) |
| `REQUIREMENTS.md` | This file |

---

## 7. Open Questions to Resolve in DESIGN.md

- O1: Exact request/response schema for the JSON-RPC gateway.
- O2: How much of the ephemeral-sandbox / scoped-token layer to build vs. document (keep demo self-contained).
- O3: (Superseded) Policy is Rego source, loaded directly by `regopy` — no `.wasm` artifact in the demo (D5/D5a).
- O4: Local server mechanism for `make run-gateway` (stdlib `http.server` vs. Flask). Prefer **zero extra deps** → stdlib.
- O5: Test double for AWS (moto/localstack) vs. dependency-injected fake logger.