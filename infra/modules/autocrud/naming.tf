module "naming" {
  source  = "Azure/naming/azurerm"
  version = "~> 0.4"
  prefix  = [var.app_name]
  suffix  = [var.environment]
}

# Short random suffix for resources that must be globally unique across Azure
# (SQL Server, ACR, App Service). Computed once and stored in state — stable
# across subsequent applies.
resource "random_id" "unique" {
  byte_length = 3
}

locals {
  unique = random_id.unique.hex

  # Names for resources where the naming module lacks a matching attribute.
  # Follow Azure abbreviation conventions: https://aka.ms/azabbrev
  #
  # ACR: alphanumeric only, 5-50 chars — hyphens stripped.
  container_registry_name = substr(
    replace("cr${var.app_name}${var.environment}${local.unique}", "-", ""),
    0, 50
  )

  # SQL Server: lowercase, hyphens allowed, globally unique.
  mssql_server_name = "sql-${var.app_name}-${var.environment}-${local.unique}"

  # SQL Database: unique within the server, not globally.
  mssql_database_name = "sqldb-${var.app_name}-${var.environment}"

  # App Service: globally unique.
  linux_web_app_name = "app-${var.app_name}-${var.environment}-${local.unique}"
}
