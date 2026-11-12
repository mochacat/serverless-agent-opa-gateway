# ---------------------------------------------------------------------------
# API Gateway HTTP API (not REST — cheaper + lower latency, D7) in front of the
# Lambda. Routes: POST /tools/call (the gate) and GET /health.
#
# SECURITY + FINOPS: per-route throttling caps a runaway/abusive agent client;
# CORS is denied by default (agents call server-to-server). COMPLIANCE:
# request-level access logs to CloudWatch when enabled.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_api" "gateway" {
  name          = "${local.name_prefix}-gw"
  protocol_type = "HTTP"
  description   = "Serverless agent gateway (OPA tool-dispatch gate)."

  cors_configuration {
    allow_origins = var.allowed_origins
    allow_methods = ["POST", "GET"]
    allow_headers = ["content-type", "traceparent"]
    max_age       = 3600
  }

  tags = local.tags
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.gateway.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.gateway.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 10000
}

resource "aws_apigatewayv2_route" "tools_call" {
  api_id             = aws_apigatewayv2_api.gateway.id
  route_key          = "POST /tools/call"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "AWS_IAM"
}

resource "aws_apigatewayv2_route" "health" {
  api_id    = aws_apigatewayv2_api.gateway.id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.gateway.id
  name        = "$default"
  auto_deploy = true

  # SECURITY + FINOPS: per-stage throttling bounds concurrent load/cost.
  default_route_settings {
    throttling_burst_limit = var.api_throttling_burst_limit
    throttling_rate_limit  = var.api_throttling_rate_limit
  }

  dynamic "access_log_settings" {
    for_each = var.enable_api_access_logs ? [1] : []
    content {
      destination_arn = aws_cloudwatch_log_group.api_access[0].arn
      format = jsonencode({
        requestId      = "$context.requestId"
        ip             = "$context.identity.sourceIp"
        requestTime    = "$context.requestTime"
        httpMethod     = "$context.httpMethod"
        routeKey       = "$context.routeKey"
        status         = "$context.status"
        responseLength = "$context.responseLength"
        integrationErr = "$context.integrationErrorMessage"
        latency        = "$context.responseLatency"
      })
    }
  }

  tags = local.tags
}

# SECURITY: resource-based policy that restricts who can invoke the gateway.
# The `tools_call` route uses AWS_IAM auth (line 37), which already requires
# `execute-api:Invoke` — any caller must have IAM permissions. That is sufficient
# for access control; no separate resource policy is needed.
