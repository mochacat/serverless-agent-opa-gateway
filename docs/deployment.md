# Deployment

This is an **open-source project**: there is no single target AWS account, so
**CI never deploys** and the repo holds **no deploy credentials**. Instead the
repo *ships a deployable artifact*, and **you** deploy it into **your own**
account with OpenTofu. This document is that path.

> **What "deployable" means here:** the reusable OpenTofu module + the Lambda
> package + a runnable dev root config. A user who follows the steps below gets
> a working gateway at an HTTPS URL in their account — that is the deliverable.

---

## 1. The two deployment forms

| Form | Command | What it is |
|------|---------|-----------|
| **Local (zero AWS)** | `make run-gateway` | Full decision path as a plain process — the same `GatewayApp` the Lambda runs. No AWS, no Docker. This is the demo. |
| **Real deploy (your AWS)** | `make deploy` | API Gateway HTTP API → Lambda, packaged by OpenTofu. Real HTTPS endpoint in your account. |

Both run the **same** gateway code and the **same** Rego (`regopy` in-process).
Only the cloud plumbing differs.

---

## 2. Prerequisites

- [OpenTofu](https://opentofu.org/) ≥ 1.6 (`tofu`)
- Python 3.14 (the Lambda runtime and the local build both target it)
- AWS credentials for the account you deploy into (via `AWS_PROFILE` / SSO /
  `aws configure`) — **only for `make deploy`/`plan`/`destroy`; never for CI**
- No `opa` needed for deploy (the Rego is evaluated in-process by `regopy`)

---

## 3. Configure the environment

The dev root config lives in [`infra/envs/dev/`](../infra/envs/dev/). Copy the
example and adjust — it is git-ignored, so your account ids stay local:

```bash
cp infra/envs/dev/terraform.tfvars.example infra/envs/dev/terraform.tfvars
```

Key knobs (see `infra/envs/dev/variables.tf` for all):

```hcl
aws_region            = "us-east-1"
agent_name            = "agent-gw"
lambda_source_dir     = "../../../build/lambda"   # pre-built by make deploy: src/ + policies/ + pip deps
memory_size           = 512          # FinOps: right-size
reserved_concurrency  = 2            # FinOps: caps max cost
log_retention_days    = 30           # raised automatically by compliance floor
compliance_frameworks = []           # e.g. ["hipaa"]  -> ~6y audit retention
kms_key_arn           = null         # set a CMK in prod (HIPAA/PCI)
allowed_origins       = []           # empty = deny all cross-origin
```

---

## 4. Deploy

```bash
# Single command: builds the Lambda package (src/ + policies/ + pip deps),
# then runs tofu init + apply in YOUR account.
make deploy        # == build/lambda/ + cd infra/envs/dev && tofu init && tofu apply -auto-approve

# Preview without deploying (still needs the build step first):
make plan          # runs tofu plan in the dev env
```

`make deploy` prints the resulting `tools_call_url`. Tear it down with
`make destroy`.

> **CI parity:** CI runs `tofu init` + `tofu validate` only (no `plan`/`apply`),
> because those need real AWS access. Deploy is a human step by design.

---

## 5. Post-deploy smoke test

With the stack up, exercise safe-vs-unsafe against the live endpoint:

```bash
GW_URL="$(cd infra/envs/dev && tofu output -raw tools_call_url)"

# ALLOW — benign read
curl -s -X POST "$GW_URL" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"tool":"get_balance","industry":"fintech"}}'

# DENY — money movement without a change ticket (feature F)
curl -s -X POST "$GW_URL" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"tool":"execute_transfer","industry":"fintech",
                 "args":{"amount":100,"beneficiary":"payroll"}}}'
# -> {"jsonrpc":"2.0","id":2,"error":{"code":-32000,... "reason":"BLOCKED: ...change ticket..."}}
```

The audit record for each call lands in the CloudWatch log group named by
`tofu output -raw audit_log_group`.

---

## 6. Cold start / latency

- **Cold start** pays the `regopy` interpreter + Rego compile **once** per
  environment (module-level singleton in the handler). Warm invocations reuse it.
- **Warm decision latency** is sub-millisecond in-process — there is no network
  hop to an OPA server (D2/D2a). A shared OPA *service* is only the documented
  long-running scale path, not the default.
- **Package size:** `make deploy` pip-installs runtime deps then terraform zips
  the result via `archive_file` (~44 MB with the `regopy` native wheel). It fits
  Lambda's 250 MB unzipped limit; above 50 MB the provider uploads via S3
  automatically. If size matters, a future arm64 wheel (faster, cheaper, but
  `regopy` currently ships x86_64 only — see `infra/modules/agent_gateway/lambda.tf`)
  is a one-attribute change.

---

## 7. Module reference

**Inputs** (`infra/modules/agent_gateway/variables.tf`), grouped:

- *Core:* `agent_name`, `environment`, `region`, `lambda_source_dir`,
  `lambda_handler`, `runtime`
- *Security:* `kms_key_arn`, `allowed_origins`, `vpc_subnet_ids`,
  `vpc_security_group_ids`, `api_throttling_burst_limit`, `api_throttling_rate_limit`
- *FinOps:* `memory_size`, `reserved_concurrency`, `log_retention_days`
- *Compliance:* `compliance_frameworks`, `enable_api_access_logs`, `tags`

**Outputs** (`infra/modules/agent_gateway/outputs.tf`): `gateway_url`,
`tools_call_url`, `lambda_arn`, `lambda_role_arn`, `audit_log_group`,
`audit_log_group_arn`, `effective_log_retention_days`, `tags`.

### 7.1 Network topology / privacy

**Default (reference deploy):** a **public** API Gateway HTTP API → Lambda
(AWS-managed side). The Lambda is *not* attached to a VPC unless you pass the
VPC variables; the reference `dev` env does not, so the API Gateway URL is the
public reachability boundary. This is fine for the server‑to‑server, least‑
privilege posture described in `docs/security.md` — the agent holds no
credentials and the gateway enriches identity server-side.

**Hardening options — documented, buildable, not enabled by default.** For
customers who must keep agent↔gateway traffic off the public internet:

- *Attach the Lambda to a private VPC subnet* — set `vpc_subnet_ids` and
  `vpc_security_group_ids` (the module's `dynamic "vpc_config"` activates).
  Use when the *agent side* also runs in your VPC.
- *Interface VPC endpoint for API Gateway* (`execute-api`, powered by
  PrivateLink) — lets a VPC‑resident agent reach the API Gateway without egress
  through the public network.

**Caveat (do not overclaim):** API Gateway HTTP keeps a *public* execution
path; these options keep AWS‑managed traffic internal, but a truly
private‑only enforcement requires further lockdown (e.g. an API‑Gateway
resource policy that denies non‑endpoint sources, or disabling public
execution). That tighter posture is **out of scope for the reference deploy**
and flagged here so reviewers don't read the VPC options as "private by
default." Neither option is what makes the gateway "server-side" — it is
server-side (API Gateway + Lambda) with or without a VPC.

---

## 8. Security / compliance notes

- **Least-privilege IAM:** the Lambda role can write only its own log group and
  (optionally) use one CMK. No wildcard actions.
- **Audit retention floor:** setting `compliance_frameworks = ["hipaa"]` raises
  log retention to ~6 years automatically; `soc2`/`pci-dss`/`iso27001`/`gdpr` set
  a 1-year floor. `effective_log_retention_days` output is your auditor evidence.
- **CMK:** supply `kms_key_arn` in regulated environments to encrypt log groups
  and Lambda env with your key instead of AWS-managed.
- **CORS:** denied by default; agents should call server-to-server.
- These are **supports**, not certifications — see `docs/compliance.md`.