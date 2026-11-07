# CLAUDE.md

Guidance for working in this repository. Supersedes any generic assumptions; when in doubt, `REQUIREMENTS.md` and `DESIGN.md` are the source of truth — update them when behavior or decisions change.

## What this project is
A **serverless agent gateway** on **API Gateway HTTP → Lambda** that enforces deterministic authorization on AI-agent **tool dispatch**. It evaluates **Open Policy Agent (OPA) Rego policies** in-process via the embedded `regopy` interpreter, before a tool action executes. Target: early- and mid-stage companies adopting AI on serverless.

Do not describe this as a "portfolio" or "consulting showcase" — it is a client deliverable.

## Toolchain
- Everything is installed via **mise** (Python 3.14, `opa`, `tofu`). Do not re-install these or add ad-hoc global installs; use what mise provides. `which python3` resolves through mise.
- Build/test tools: `make`, `opa` (1.20+, Wasm target available), `tofu` (OpenTofu), `python3`/`pytest`.

## Non-negotiable architecture invariants
- **It gates tool dispatch, NOT the LLM request.** The gateway sits between the agent and tool *execution*. Do not "helpfully" turn it into an LLM-request interceptor.
- **Serverless-first.** Default deployment is API Gateway → Lambda. A long-running service is a *documented scale path*, never the default — do not migrate it in without explicit instruction.
- **In-process OPA Rego (`regopy`).** No OPA sidecar/server, no network hop to an OPA daemon. The embedded Rego engine (`regopy`) evaluates `policies/*.rego` directly in-process at cold start. No `.wasm` artifact in the demo (a `.wasm` path is an optional alternative — see DESIGN D5a). Policy source lives in `policies/`.
- **OPA stays pure/stateless.** Cross-call state (trajectory `D`, rate `E`) does NOT live inside OPA — it is injected via `input.context` from a swappable `SessionStore` (in-memory for the local demo, Db/Redis in prod).
- **Deterministic default-deny** (`default allow := false`) is the baseline. The **hero capabilities are B/E/F/G**: argument/payload inspection, rate/deny-window, change-ticket approval, output redaction. Every capability A–H is built and tested.

## Testing & CI ground rules
- **All tests run with zero AWS credentials** (the only exception is a manual deploy/smoke step). AWS interactions are dependency-injected fakes — do not introduce boto3/moto/localstack into the test path for the core logic.
- The **real Lambda is a deploy artifact**, not a test dependency. `handler.py` is a thin adapter over an API Gateway event contract; tests feed it a mock event.
- `Makefile` targets: `test-policy` (rego), `test-integration` (pytest, all tiers), `test-all`, `run-gateway`, `build-zip`. CI runs the same steps — "CI green == local green."
- CI must be **functional**: `opa check` → `opa test` → `regopy` load smoke → ruff → pytest+cov → `tofu validate`. Deploy is manual (`workflow_dispatch`).

## Security / compliance framing
- The gateway directly covers **OWASP GenAI LLM Top-10 (2026)**: LLM01 (Prompt Injection), LLM02 (Sensitive Info Disclosure), LLM03 (Excessive Agency), LLM06 (Unbounded Consumption), LLM10 (Improper Output Handling). The other five (LLM04/05/07/08/09) are documented **boundaries** — never overclaim coverage.
- Agent holds **no credentials**; the gateway enriches identity server-side; infra IAM is least-privilege.

## Docs layout
- `REQUIREMENTS.md` — requirements (industries, capability surface, testing, CI/CD, OWASP, OTel).
- `DESIGN.md` — RFD-format design (decisions, open questions, work breakdown).
- `TASKS.md` — task tracker; knock tasks off one by one; each has a `Done =` criterion.
- `docs/` — architecture, security, observability, deployment, compliance.