# ---------------------------------------------------------------------------
# Common tags applied to every taggable Azure resource in this module.
#
# A minimal, conventional baseline (Application / Environment / ManagedBy) that
# supports cost allocation, environment filtering, and "who provisioned this"
# governance queries. var.tags is merged on top so a caller can add estate-wide
# tags (Owner, CostCenter, DataClassification, …) without editing the module;
# caller-supplied keys win on collision.
# ---------------------------------------------------------------------------
locals {
  common_tags = merge(
    {
      Application = var.app_name
      Environment = var.environment
      ManagedBy   = "Terraform"
    },
    var.tags,
  )
}
