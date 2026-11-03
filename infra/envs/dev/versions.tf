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

  # Remote state is recommended for team use; disabled by default so the demo
  # works with local state out of the box. Enable by uncommenting + `tofu init -migrate-state`.
  # backend "s3" {
  #   bucket = "my-tofu-state"
  #   key    = "agent-gateway/dev.tfstate"
  #   region = "us-east-1"
  # }
}
