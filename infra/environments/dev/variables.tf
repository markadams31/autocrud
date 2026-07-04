variable "subscription_id" {
  type        = string
  description = "Azure subscription ID for the dev environment."
}

variable "tenant_id" {
  type        = string
  description = "Entra ID tenant ID (shared across environments)."
}

variable "db_schemas" {
  type        = list(string)
  description = "Database schemas to expose via the CRUD API. Required — no default, so a misconfigured deployment fails at plan time rather than silently exposing the wrong schemas."

  validation {
    condition     = length(var.db_schemas) > 0
    error_message = "db_schemas must list at least one schema, e.g. [\"dbo\"]."
  }
}

variable "db_audit_columns" {
  type        = list(string)
  description = "Columns the database manages via trigger/default (excluded from all write payloads). Optional — leave empty ([]) if the database has none."
  default     = []
}
