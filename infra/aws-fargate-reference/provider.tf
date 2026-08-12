provider "aws" {
  region                      = var.aws_region
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  skip_region_validation      = true

  default_tags {
    tags = merge(var.additional_tags, {
      Project   = "hermes-enterprise-deployment-lab"
      Component = "aws-fargate-reference"
      ManagedBy = "opentofu"
      Reference = "cloud-hybrid-iac"
    })
  }
}
