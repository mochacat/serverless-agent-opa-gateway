# Observability

## Audit (mandatory)

Every allowed AND denied tool dispatch produces one structured audit record.
This is the compliance artifact — it is always written on the decision path.

Record schema (written as JSON to stdout → CloudWatch Logs):

```json
{
  "tool": "execute_transfer",
  "args": {"amount": 100, "beneficiary": "payroll"},
  "principal": {"id": "arn:aws:sts::123456789012:assumed-role/agent-role/session"},
  "context": {"trajectory": [...], "rate_window": {"max": 3}},
  "decision": {"allow": false, "reason": "BLOCKED: ...", "redact": []},
  "policy_version": "v1",
  "reason": "BLOCKED: money movement requires an approved change ticket.",
  "trace_id": "0123456789abcdef0123456789abcdef",
  "ts": 1700000000.123
}
```

The `AuditSink` protocol is injectable — swap implementations per environment
without changing gateway code.

## Metrics (optional, seam only)

The `MetricsSink` protocol provides counters and gauges. An in-memory
implementation ships by default for the local demo. The interface is designed
for an OTLP exporter drop-in:

| Metric | Type | Attributes | Description |
|--------|------|------------|-------------|
| `decisions` | Counter | `tool` | Total tool dispatch decisions |
| `denials` | Counter | `tool` | Denied dispatches |
| `policy_eval_ms` | Gauge | `tool` | Per-decision evaluation latency |

**No OTLP exporter is implemented.** The seam exists — build one when you need
to ship metrics to your observability backend.

## Tracing (seam only)

The gateway extracts the W3C `traceparent` header from inbound requests and
uses the 32-hex trace ID in audit records for correlation. It does **not**
create OpenTelemetry spans or export traces.

To add tracing:

1. Create an OTel `TracerProvider` in the Lambda handler
2. Extract the `traceparent` into a span context
3. Create a child span named `policy_decision` for each `tools/call`
4. Record the decision (`allow`/`deny`/`reason`) as span attributes
5. Export via OTLP (gRPC/HTTP) to your collector

The audit record is the durable evidence — OTel traces are ephemeral
observability and should never replace it.

### Environment variables

The Lambda handler reads the following OTel-standard environment variables.
These are passed through to the Lambda environment in the Terraform module
(see `infra/modules/agent_gateway/lambda.tf`):

| Variable | Example | Purpose |
|----------|---------|---------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `https://otlp.example.com:4318` | Where to send traces/metrics (HTTP/gRPC) |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | Export protocol (http/protobuf or grpc) |
| `OTEL_SERVICE_NAME` | `agentic-opa-guardrails` | Service name for trace identification |
| `OTEL_RESOURCE_ATTRIBUTES` | `deployment.environment=prod,service.version=v1` | Additional resource labels |
| `OTEL_TRACES_SAMPLER` | `parentbased_traceidratio` | Sampling strategy |
| `OTEL_TRACES_SAMPLER_ARG` | `0.1` | Sample rate (e.g. 10% when using traceidratio) |

These vars are standard across all OTel SDKs — the agent framework (LangChain,
OpenAI, etc.) and the gateway both read them from the same environment, so
traces from the agent and the gateway automatically join the same trace tree.
No gateway-specific config needed beyond setting the vars.

## Logging

The Lambda handler logs nothing by default. Audit records written to stdout
are automatically collected by CloudWatch Logs. Set `LOG_LEVEL=DEBUG` in the
Lambda environment variables for additional diagnostic output.

## Cost / performance notes

- Audit writes are on the critical path (sub-millisecond, in-process)
- Metrics are in-memory counters (no I/O)
- OTel export, when added, should be async/off-critical-path to never block
  the decision response