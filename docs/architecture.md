# Architecture

## Deployment model

The gateway deploys as **API Gateway HTTP API → Lambda** — a serverless
request-response path with no long-running infrastructure.

```
┌─────────────┐     ┌──────────────┐     ┌──────────┐
│ Agent (any) │────▶│ API Gateway  │────▶│  Lambda  │
│             │     │  HTTP API    │     │ (regopy) │
└─────────────┘     └──────────────┘     └──────────┘
                          │                     │
                          │ IAM auth            │ stdout → CloudWatch Logs
                          │ SigV4 verification  │ (audit trail)
                          ▼                     ▼
                     Rejected on          Decision returned
                     invalid auth         as JSON-RPC response
```

A long-running service (ECS/Fargate, EKS) is a **documented scale path**,
never the default — the same `GatewayApp` class runs in any Python process.

## Managed vs custom policy engines

| Engine | Why not chosen |
|--------|----------------|
| **AWS Cedar** | AWS-managed only (no local eval), no trajectory/rate features, tied to AWS APIs |
| **Guardrails for Amazon Bedrock** | Tied to Bedrock, only filters LLM input/output (not tool dispatch) |
| **OPA (this project)** | Open standard, in-process eval via `regopy`, pure/stateless, portable |

OPA was chosen because it is the only option that runs in-process without a
sidecar, is cloud-agnostic, and has a mature policy language (Rego).

## Module / environment layout

```
infra/
├── modules/
│   └── agent_gateway/       # Reusable OpenTofu module
│       ├── apigw.tf          # API Gateway HTTP API + IAM auth
│       ├── lambda.tf         # Lambda function + archive
│       ├── main_role.tf      # Least-privilege IAM role
│       ├── cloudwatch.tf     # Audit log groups + compliance retention
│       ├── variables.tf      # All inputs (core, security, FinOps, compliance)
│       ├── outputs.tf        # Gateway URL, Lambda ARN, log group
│       └── locals.tf         # Tags, retention floor, concurrency
└── envs/
    └── dev/                  # Runnable root config
        ├── main.tf           # Instantiates the module
        ├── variables.tf      # Environment-level inputs
        └── terraform.tfvars.example
```

## Latency

- **Cold start**: `regopy` interpreter + Rego compile happens once per Lambda
  execution environment (module-level singleton). ~200-500ms added to the first
  request.
- **Warm decisions**: sub-millisecond in-process — no network hop to an OPA
  server or sidecar.
- **No .wasm in the default path**: the demo evaluates Rego source directly via
  `regopy`. A `.wasm` compile step is an optional optimization (see DESIGN D5a).

## Determinism

The gateway is a pure function of its inputs: `(tool, args, industry, context,
principal)` → `(allow, reason, redact)`. Cross-call state (trajectory D, rate E)
is injected via `input.context` from a swappable `SessionStore` — OPA never
touches the store directly. This makes every decision reproducible and testable.