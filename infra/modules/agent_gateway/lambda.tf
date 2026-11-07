# ---------------------------------------------------------------------------
# The Lambda that runs the gateway app (the same code as `make run-gateway`).
# The package includes `src/` + `policies/*.rego`; the in-process regopy engine
# loads the Rego at cold start (D5/D6) — no OPA server, no .wasm.
#
# FINOPS: arm64 (Graviton) is ~20% cheaper per GB-second than x86; memory and
# reserved concurrency are caller-tunable and bounded; a hard timeout caps the
# cost of any single runaway invocation.
# ---------------------------------------------------------------------------

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = var.lambda_source_dir
  output_path = "${path.module}/.build/${local.name_prefix}-gw.zip"
}

resource "aws_lambda_function" "gateway" {
  function_name = "${local.name_prefix}-gw"
  description   = "Serverless agent gateway — in-process OPA Rego tool-dispatch gate."
  role          = aws_iam_role.lambda.arn

  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  handler     = var.lambda_handler
  runtime     = var.runtime
  memory_size = var.memory_size
  timeout     = 10 # hard cap: a single decision is sub-ms; 10s is a runaway guard

  # FINOPS: bound max parallelism (and therefore max cost); -1 => unreserved.
  reserved_concurrent_executions = local.reserved_concurrency

  # x86_64 because `regopy` (the embedded OPA Rego engine) currently only
  # ships manylinux x86_64 wheels on PyPI. Switch to arm64 (Graviton,
  # ~20% cheaper) when regopy publishes an aarch64 wheel — the Rego source
  # is architecture-agnostic so no code change is needed, only this attr.
  architectures = ["x86_64"]

  dynamic "vpc_config" {
    for_each = length(var.vpc_subnet_ids) > 0 ? [1] : []
    content {
      subnet_ids         = var.vpc_subnet_ids
      security_group_ids = var.vpc_security_group_ids
    }
  }

  environment {
    variables = {
      # Policy version surfaced in audit records (git-versioned; NFR7).
      POLICY_VERSION = "v1"
      # Audit sink target + observability toggles are read by the handler.
      AUDIT_SINK       = "cloudwatch"
      LOG_FORMAT       = "json" # structured JSON lines -> CloudWatch
      LOG_LEVEL        = var.environment == "prod" ? "INFO" : "DEBUG"
      COMPLIANCE_SCOPE = join(",", var.compliance_frameworks)

      # OpenTelemetry environment variables (standard OTLP exporter config).
      # Set these when the OTel SDK is added to the Lambda package. The gateway
      # reads the same vars the agent framework uses, so traces join the same
      # trace tree automatically. See docs/observability.md.
      # OTEL_EXPORTER_OTLP_ENDPOINT   = "https://otlp.example.com:4318"
      # OTEL_EXPORTER_OTLP_PROTOCOL  = "http/protobuf"
      # OTEL_SERVICE_NAME            = "agentic-opa-guardrails"
      # OTEL_RESOURCE_ATTRIBUTES     = "deployment.environment=${var.environment}"
      # OTEL_TRACES_SAMPLER          = "parentbased_traceidratio"
      # OTEL_TRACES_SAMPLER_ARG      = "1.0"
    }
  }

  # Never log or expose PHI/financial payloads in trace/debug.
  tracing_config {
    mode = "Active"
  }

  tags = local.tags

  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gateway.function_name
  principal     = "apigateway.amazonaws.com"
  # Scope the grant to this specific API's routes, not any caller.
  source_arn = "${aws_apigatewayv2_api.gateway.execution_arn}/*/*"
}
