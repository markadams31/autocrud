# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

output "app_service_url" {
  description = "Public URL of the deployed application. Open this to verify the deployment."
  value       = "https://${azurerm_linux_web_app.main.default_hostname}"
}

output "app_name" {
  description = "App Service name — used in az webapp commands."
  value       = azurerm_linux_web_app.main.name
}

output "acr_name" {
  description = "Azure Container Registry name (no .azurecr.io suffix). Used by 'az acr build'."
  value       = azurerm_container_registry.main.name
}

output "resource_group_name" {
  description = "Resource group containing all environment resources."
  value       = azurerm_resource_group.main.name
}

output "app_users_group_name" {
  description = "Entra security group name. Set @GroupName in database/permissions.sql to this, and add users here to grant access."
  value       = azuread_group.app_users.display_name
}

output "sql_admins_group_name" {
  description = "Entra security group that is the SQL Server Entra admin. Add DBAs here to grant SQL administration (independent of who runs terraform apply)."
  value       = azuread_group.sql_admins.display_name
}

# ---------------------------------------------------------------------------
# Post-provisioning commands, in the order they're run. The environment
# wrappers surface these with a "_N_" prefix so they sort to the top of the
# apply summary in order. The $(...) subexpressions resolve your signed-in
# identity at run time and work in both bash and PowerShell. Copy-paste to run.
# (mail, not userPrincipalName: for guest/#EXT# accounts the UPN isn't the
# address that authenticates — the mail attribute is.)
# ---------------------------------------------------------------------------

output "add_self_to_group_command" {
  description = "Add the signed-in user to the app-users group (grants the app role transitively)."
  value       = "az ad group member add --group '${azuread_group.app_users.display_name}' --member-id $(az ad signed-in-user show --query id -o tsv)"
}

output "seed_database_command" {
  description = "Apply database/seed.sql as the signed-in user. Run from the repo root."
  value       = "sqlcmd -S ${azurerm_mssql_server.main.fully_qualified_domain_name} -d ${local.mssql_database_name} --authentication-method ActiveDirectoryAzCli -i database/seed.sql"
}

output "grant_permissions_command" {
  description = "Apply database/permissions.sql (set @GroupName first) as the signed-in user. Run from the repo root."
  value       = "sqlcmd -S ${azurerm_mssql_server.main.fully_qualified_domain_name} -d ${local.mssql_database_name} --authentication-method ActiveDirectoryAzCli -i database/permissions.sql"
}

output "build_and_push_command" {
  description = "Build the image in ACR (remote build — no local Docker needed). Run from the repository root."
  value       = "az acr build --registry ${azurerm_container_registry.main.name} --image ${var.docker_image_name}:${var.docker_image_tag} ."
}

output "deploy_command" {
  description = "Restart the App Service to pick up a freshly pushed image."
  value       = "az webapp restart --name ${azurerm_linux_web_app.main.name} --resource-group ${azurerm_resource_group.main.name}"
}

# ---------------------------------------------------------------------------
# Local .env values for this environment. Copy these into a .env at the repo
# root. DB_DRIVER is a constant (not resource-derived) — see .env.example.
# ---------------------------------------------------------------------------

output "env_db_server" {
  description = "DB_SERVER for .env — Azure SQL server FQDN."
  value       = azurerm_mssql_server.main.fully_qualified_domain_name
}

output "env_db_database" {
  description = "DB_DATABASE for .env."
  value       = local.mssql_database_name
}

output "env_db_schemas" {
  description = "DB_SCHEMAS for .env — comma-separated schemas reflected."
  value       = join(",", var.db_schemas)
}

output "env_db_audit_columns" {
  description = "DB_AUDIT_COLUMNS for .env — comma-separated DB-managed columns."
  value       = join(",", var.db_audit_columns)
}

output "env_azure_tenant_id" {
  description = "Entra tenant ID — handy for 'az login --tenant <id>'."
  value       = var.tenant_id
}

output "env_log_level" {
  description = "LOG_LEVEL for .env."
  value       = var.log_level
}

# ---------------------------------------------------------------------------
# Other module outputs (not surfaced in the environment apply summary).
# ---------------------------------------------------------------------------

output "app_insights_connection_string" {
  description = "Application Insights connection string."
  value       = azurerm_application_insights.main.connection_string
  sensitive   = true
}
