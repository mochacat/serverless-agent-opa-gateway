# Pin the OpenTofu + provider floors so `tofu validate` in CI == local.
# AWS provider v6 is required for `aws_apigatewayv2_*` HTTP APIs and the
# resource-attribute shapes used in this module.
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0, < 7.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
  }
}
