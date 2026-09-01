terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }

  # Reuses the bucket that already exists in this account (versioning enabled).
  backend "s3" {
    bucket       = "terraform-state-bucket-rohxgdwyic"
    key          = "graphrag-comparision/ap-south-1/terraform.tfstate"
    region       = "ap-south-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Subproject  = "GraphRAG Performance Metrics"
      ManagedBy   = "terraform"
      Environment = var.environment
    }
  }
}
