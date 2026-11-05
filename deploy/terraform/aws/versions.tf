terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # État partagé et verrouillé : à activer avec un bucket S3 existant (voir README).
  # backend "s3" {
  #   bucket       = "mon-bucket-terraform"
  #   key          = "evalforge/terraform.tfstate"
  #   region       = "eu-west-3"
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "evalforge"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
