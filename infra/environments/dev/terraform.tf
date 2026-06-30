terraform {
  required_version = ">= 1.7"

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

  # Partial backend config — supply values at init time:
  #   terraform init -backend-config=backend.tfvars
  # See backend.tfvars.example for the required keys.
  backend "azurerm" {}
}

provider "azurerm" {
  subscription_id = var.subscription_id

  features {
    resource_group {
      # Allow destroying non-empty resource groups (useful in dev).
      prevent_deletion_if_contains_resources = false
    }
  }
}

provider "azuread" {
  tenant_id = var.tenant_id
}

provider "azapi" {
  subscription_id = var.subscription_id
}
