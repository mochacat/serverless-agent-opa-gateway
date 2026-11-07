# Security — Production Auth & Network Model

This document describes how the gateway authenticates callers and protects
traffic in a production deployment. It covers the **two-layer auth model**:

1. **Transport/auth layer** — API Gateway authenticates the request before it
   reaches the Lambda (IAM, Lambda authorizer, mTLS).
2. **Policy layer** — the Rego engine evaluates the authenticated principal's
   permissions against the tool call.

## Why two layers?

The agent **holds no credentials**. The gateway resolves identity server-side.
This is the least-privilege posture: if the agent is compromised, the attacker
cannot escalate because there are no stored credentials to steal.

| Layer | Responsibility | Where it runs |
|-------|---------------|---------------|
| Transport/auth | Authenticates the caller (who is this?) | API Gateway, before the Lambda |
| Policy | Authorizes the tool call (is this allowed?) | Lambda, in-process regopy |

## Production auth options

### Option 1: IAM auth (recommended default)

API Gateway HTTP API supports **AWS IAM** as the auth mechanism. The caller
signs requests with [Signature Version 4](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference-aws-signing.html)
using an IAM role or user.

```
Agent (EC2/EKS) ─── SigV4-signed request ───▶ API Gateway (IAM auth) ───▶ Lambda
                                                    │
                                                    ▼
                                              Resolved principal:
                                              { id: "arn:aws:sts::123:assumed-role/app-role/session",
                                                authenticated: true,
                                                authorized_scopes: "fintech" }
```

**Pros:** No custom auth code, integrates with IAM roles for EC2/EKS/Fargate,
works with VPC endpoints.

**Cons:** Requires the agent's compute environment to have AWS credentials
(easy on EC2/EKS, harder for external agents).

### Option 2: Lambda authorizer

A custom Lambda authorizer (formerly "custom authorizer") validates a token
(JWT, OAuth, API key) and returns an IAM policy that scopes the caller to
specific routes.

```
Agent ─── Bearer token ───▶ API Gateway ───▶ Authorizer Lambda ───▶ IAM policy
                                    │
                                    ▼
                              Lambda (with resolved principal in event context)
```

**Pros:** Works with any identity provider (Auth0, Okta, custom JWT), no AWS
credentials needed on the agent side.

**Cons:** Additional Lambda invocation per request (adds latency and cost).

### Option 3: mTLS (mutual TLS)

The client presents a TLS certificate signed by a trusted CA. API Gateway
validates the certificate and passes its subject/issuer to the Lambda.

```
Agent ─── Client cert ───▶ API Gateway (TLS termination) ───▶ Lambda
                                    │
                                    ▼
                              Resolved principal:
                              { id: "CN=agent-prod-1,OU=agents,O=acme",
                                authenticated: true,
                                authorized_scopes: "fintech,readonly" }
```

**Setup requirements:**

- API Gateway HTTP API does **not** support mTLS natively. Use either:
  - **API Gateway REST API** (supports mTLS with a trust store)
  - **CloudFront + Lambda@Edge** to terminate TLS and pass the client cert
  - **ALB + Lambda** (ALB terminates TLS, supports mTLS via mutual authentication)
- Requires ACM Private CA or your own CA to issue client certificates
- Certificate rotation and revocation need operational tooling

**Pros:** Strong cryptographic authentication, no shared secrets, certificate
can encode identity in the CN/SAN fields.

**Cons:** Operational overhead of CA management, more complex infrastructure.

## Network topology

### Default (public endpoint)

The reference deploy creates a **public** API Gateway HTTP API. This is
acceptable for server-to-server communication when combined with IAM auth
or a Lambda authorizer — the endpoint is public but only authenticated callers
can invoke it.

```
Agent (anywhere) ─── HTTPS ───▶ API Gateway (public) ───▶ Lambda
```

### Hardened: private VPC

For agent↔gateway traffic that never leaves your network:

```
Agent (your VPC) ───▶ PrivateLink (VPC endpoint) ───▶ API Gateway ───▶ Lambda
                                                          │
                                                          ▼
                                                    (in your VPC subnet)
```

Configure via the OpenTofu module:

```hcl
vpc_subnet_ids         = ["subnet-abc", "subnet-def"]
vpc_security_group_ids = ["sg-123"]
```

This attaches the Lambda to your VPC subnets. API Gateway still needs either:

- A **VPC endpoint** for `execute-api` (PrivateLink) — the agent calls the
  gateway through a private IP in your VPC
- Or a **resource policy** that denies requests from outside your VPC

> **Caveat:** API Gateway HTTP API retains a public execution path. A resource
> policy can deny non-VPC sources, but truly private-only enforcement (no
> public internet path at all) requires a different architecture — API Gateway
> REST API with a VPC endpoint and resource policy, or an internal ALB + Lambda.

## How the Lambda resolves identity

The `enrich_principal` hook in `GatewayConfig` is the pluggable seam. The
Lambda handler (`handler.py`) wires an `_resolve_principal` function that
reads the IAM identity from the API Gateway event:

```python
def _resolve_principal(event: dict) -> dict:
    auth = (event.get("requestContext") or {}).get("authorizer") or {}
    iam = auth.get("iam") or {}
    user_arn = iam.get("userArn") or ""
    if user_arn:
        return {"id": user_arn, "authenticated": True, "authorized_scopes": ""}
    return {"id": "svc-lambda", "authenticated": True, "authorized_scopes": ""}
```

When API Gateway uses `AWS_IAM` auth, the event's `requestContext.authorizer.iam`
contains:

```json
{
  "accessKey": "AKIA...",
  "accountId": "123456789012",
  "callerId": "AIDA...",
  "userArn": "arn:aws:sts::123456789012:assumed-role/agent-role/session",
  "userId": "AROA...:session"
}
```

The handler extracts the `userArn` and passes it as the principal id. The Rego
policies then evaluate the tool call against this principal — the agent never
sent the principal itself.

### Terraform configuration

The OpenTofu module wires IAM auth automatically:

```hcl
module "agent_gateway" {
  source = "../../modules/agent_gateway"

  # ...

  # IAM role the agent runs as (e.g. ECS task role, EC2 instance profile).
  # A resource-based policy on the API Gateway restricts invocation to this
  # role only. Set null for wide-open dev access (IAM auth still required).
  agent_role_arn = "arn:aws:iam::123456789012:role/app-agent-role"

  # The route authorization_type is already "AWS_IAM" — no extra config needed.
}
```

The caller (the agent's compute environment) needs the following IAM permission
to invoke the gateway:

```json
{
  "Effect": "Allow",
  "Action": "execute-api:Invoke",
  "Resource": "arn:aws:execute-api:us-east-1:123456789012:api-id/*/POST/tools/call"
}
```

On EC2/EKS/Fargate, attach this policy to the instance profile or task role.
The AWS SDK handles SigV4 signing automatically — no code changes needed in the
agent.

## The `principal_id` in the local demo

In the local demo server (`src/gateway/server.py`), `principal_id` is a
lookup key into an in-memory map. This is **not authentication** — it's a
convenience for testing without AWS. The map knows two identities:

| principal_id | authenticated | scopes |
|---|---|---|
| `ops` | true | — |
| `clinician` | true | `diagnosis` |

Any other value (or missing) → `authenticated: false` → all tools are denied.

In production, `principal_id` is never read from the caller's payload. The
gateway resolves identity from the API Gateway event context, which has
already been authenticated by IAM, a Lambda authorizer, or mTLS.

## OWASP mapping

| LLM | Threat | Mitigation |
|-----|--------|------------|
| LLM01 | Prompt Injection | Tool arguments are inspected by policy; caller cannot escalate auth through prompt injection because auth is server-side |
| LLM02 | Sensitive Info Disclosure | Output redaction (G) strips PHI/PII; the agent never held credentials to leak |
| LLM03 | Excessive Agency | Verb-class gating + change-ticket requirement + trajectory precondition |
| LLM07 | Supply Chain | Not covered by this gateway — see `docs/compliance.md` for boundary |