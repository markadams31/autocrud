# Provider requirements for this module. Constraints are kept IDENTICAL to the
# root modules (environments/*/terraform.tf) on purpose: this is an internal
# module pinned in lockstep with its only callers, not a public module meant to
# float across major versions. Matching `~>` pins here means a bump must touch
# both places and any mismatch fails `terraform init` loudly — surfacing drift
# rather than letting a looser `>=` floor silently accept the root's newer pin.
terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
    http = {
      source  = "hashicorp/http"
      version = "~> 3.4"
    }
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.0"
    }
  }
}
