variable "app_name" {
  type        = string
  description = "Base application name. Used as a prefix in all resource names."
  default     = "autocrud"
}

variable "environment" {
  type        = string
  description = "Deployment environment — drives resource naming and environment-specific defaults."
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be 'dev' or 'prod'."
  }
}

variable "location" {
  type        = string
  description = "Azure region for all resources."
  default     = "australiaeast"
}

variable "tags" {
  type        = map(string)
  description = "Extra tags merged onto every taggable resource, on top of the module's Application/Environment/ManagedBy baseline. Use for estate-wide governance tags (Owner, CostCenter, DataClassification, …). Caller keys win on collision."
  default     = {}
}

variable "tenant_id" {
  type        = string
  description = "Entra ID (Azure AD) tenant ID."
}

# ---------------------------------------------------------------------------
# Application config — passed as App Service environment variables
# ---------------------------------------------------------------------------

variable "db_schemas" {
  type        = list(string)
  description = "Database schemas to expose via the CRUD API. Required — no default, so a caller that forgets to set it fails loudly instead of silently exposing only 'dbo'."

  validation {
    condition     = length(var.db_schemas) > 0
    error_message = "db_schemas must list at least one schema, e.g. [\"dbo\"]."
  }
}

variable "db_audit_columns" {
  type        = list(string)
  description = "Columns the database manages via trigger/default (excluded from all write payloads). Optional — the database may have none."
  default     = []
}

variable "log_level" {
  type        = string
  description = "Application log level."
  default     = "INFO"
  validation {
    condition     = contains(["DEBUG", "INFO", "WARNING"], var.log_level)
    error_message = "log_level must be DEBUG, INFO, or WARNING."
  }
}

variable "log_retention_in_days" {
  type        = number
  description = "Log Analytics workspace retention. 30 days suits dev; raise it (up to 730) where audit/incident history must be kept longer."
  default     = 30
  validation {
    condition     = var.log_retention_in_days >= 30 && var.log_retention_in_days <= 730
    error_message = "log_retention_in_days must be between 30 and 730."
  }
}

variable "log_user_identity" {
  type        = string
  description = "How the signed-in user appears in logs: 'email' (default, best for support), 'hash' (a stable pseudonym — correlatable, no PII), or 'none'."
  default     = "email"
  validation {
    condition     = contains(["email", "hash", "none"], var.log_user_identity)
    error_message = "log_user_identity must be 'email', 'hash', or 'none'."
  }
}

variable "log_user_identity_salt" {
  type        = string
  description = "Salt for log_user_identity = 'hash', to resist reversing known addresses. Ignored in other modes."
  default     = ""
  sensitive   = true
}

variable "appinsights_sampling_ratio" {
  type        = number
  description = "Fraction of request traces to keep (0.0–1.0). Lower to cap Application Insights ingestion cost on a chatty deployment."
  default     = 1.0
  validation {
    condition     = var.appinsights_sampling_ratio >= 0 && var.appinsights_sampling_ratio <= 1
    error_message = "appinsights_sampling_ratio must be between 0.0 and 1.0."
  }
}

variable "bulk_max_rows" {
  type        = number
  description = "Max rows a single bulk operation (delete/update/import) may touch in one transaction."
  default     = 1000
  validation {
    condition     = var.bulk_max_rows >= 1
    error_message = "bulk_max_rows must be at least 1."
  }
}

variable "health_check_database" {
  type        = bool
  description = "Whether /health performs a live database round-trip (full readiness) or only checks the in-memory schema snapshot (liveness). Default true. Set false for a serverless auto-pausing database: the ~1/min health probe would otherwise keep it awake and burn its compute allowance, never letting it pause."
  default     = true
}

# ---------------------------------------------------------------------------
# SKUs — configurable so dev and prod can use different tiers
# ---------------------------------------------------------------------------

variable "app_service_sku" {
  type        = string
  description = "App Service Plan SKU. B1 is the minimum that supports Always On."
  default     = "B1"
}

variable "acr_sku" {
  type        = string
  description = "Azure Container Registry SKU."
  default     = "Basic"
}

variable "sql_sku" {
  type        = string
  description = "Azure SQL Database SKU. Defaults to serverless (GP_S_Gen5_1)."
  default     = "GP_S_Gen5_1"
}

variable "sql_max_size_gb" {
  type        = number
  description = "Maximum storage for the SQL database in GB."
  default     = 32
}

variable "sql_auto_pause_delay_minutes" {
  type        = number
  description = "Auto-pause delay in minutes for serverless databases. -1 disables auto-pause. Ignored for non-serverless SKUs."
  default     = 60
}

variable "sql_min_capacity" {
  type        = number
  description = "Minimum vCores a serverless database scales down to (the floor it bills at while active, before auto-pause). 0.5 is the smallest Gen5 value. Ignored for non-serverless SKUs."
  default     = 0.5
  validation {
    condition     = var.sql_min_capacity >= 0.5
    error_message = "sql_min_capacity must be at least 0.5."
  }
}

variable "sql_admin_include_deployer" {
  type        = bool
  description = "Add the identity running 'terraform apply' (data.azurerm_client_config.current) to the SQL administrators group. Set true for a local dev apply so the deployer can run the contained-user provisioner; leave false for CI/prod, where DBAs are added to the group out of band."
  default     = false
}

variable "sql_use_free_limit" {
  type        = bool
  description = "Enable the Azure SQL Database free offer (a monthly free compute allowance + 32 GB storage). Requires a serverless GP_S_* SKU; Azure allows only one free database per subscription."
  default     = false
}

variable "sql_free_limit_exhaustion_behavior" {
  type        = string
  description = "What happens when the monthly free allowance is used up: 'AutoPause' pauses the database until next month (no charges); 'BillOverUsage' keeps it running at normal serverless rates. Only applied when sql_use_free_limit = true."
  default     = "AutoPause"
  validation {
    condition     = contains(["AutoPause", "BillOverUsage"], var.sql_free_limit_exhaustion_behavior)
    error_message = "sql_free_limit_exhaustion_behavior must be 'AutoPause' or 'BillOverUsage'."
  }
}

# ---------------------------------------------------------------------------
# Container image — set once at initial deploy; subsequent updates are made
# directly via az webapp config container set (see lifecycle.ignore_changes).
# ---------------------------------------------------------------------------

variable "docker_image_name" {
  type        = string
  description = "Image name (ACR repository) without the registry prefix or tag. The image bundles the SPA + API, so it's named for the app, not a tier."
  default     = "autocrud"
}

variable "docker_image_tag" {
  type        = string
  description = "Image tag for the initial deployment. Updated deployments bypass Terraform."
  default     = "latest"
}
