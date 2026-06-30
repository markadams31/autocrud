# Current caller identity — set as the Entra admin on the SQL Server.
# Whoever runs terraform apply becomes the SQL admin, which also allows
# them to connect via sqlcmd in the null_resource below.
data "azurerm_client_config" "current" {}

# Current deployer public IP — whitelisted so terraform apply can reach the
# SQL Server for the null_resource. Updated on each apply.
data "http" "deployer_ip" {
  url = "https://api.ipify.org"
}

resource "azurerm_mssql_server" "main" {
  name                = local.mssql_server_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  version             = "12.0"
  minimum_tls_version = "1.2"

  # Entra-only auth — no SQL admin login or password anywhere in configuration.
  azuread_administrator {
    login_username              = "TerraformDeployer"
    object_id                   = data.azurerm_client_config.current.object_id
    azuread_authentication_only = true
  }
}

# Standard (paid) database — created with azurerm. Skipped when the free offer
# is enabled, which Azure only allows at creation time (see azapi resource below);
# exactly one of the two databases exists.
resource "azurerm_mssql_database" "main" {
  count     = var.sql_use_free_limit ? 0 : 1
  name      = local.mssql_database_name
  server_id = azurerm_mssql_server.main.id
  sku_name  = var.sql_sku

  # max_size_gb and auto-pause are only meaningful for serverless SKUs.
  # The provider silently ignores them for DTU-based SKUs.
  max_size_gb                 = var.sql_max_size_gb
  auto_pause_delay_in_minutes = var.sql_auto_pause_delay_minutes
  min_capacity                = 0.5

  lifecycle {
    # The paid database is the prod path — guard against accidental deletion.
    # (prevent_destroy must be a literal, so it can't be keyed to the environment
    # via a variable; instead it's set true here, on the resource only prod uses,
    # and false on the free/dev database below.) To intentionally destroy, first
    # run: terraform state rm 'module.<name>.azurerm_mssql_database.main[0]'
    prevent_destroy = true
  }
}

# ---------------------------------------------------------------------------
# Free-tier database (azapi)
#
# The free offer (useFreeLimit) can only be set when the database is created —
# Azure rejects converting a paid database to free — and azurerm doesn't expose
# the property. So the free-tier database is created with azapi, while paid
# databases use azurerm above. Enabled only when var.sql_use_free_limit is true;
# the offer requires a serverless (GP_S_*) SKU and Azure allows one free
# database per subscription.
# ---------------------------------------------------------------------------
resource "azapi_resource" "sql_database_free" {
  count     = var.sql_use_free_limit ? 1 : 0
  type      = "Microsoft.Sql/servers/databases@2023-08-01-preview"
  name      = local.mssql_database_name
  parent_id = azurerm_mssql_server.main.id
  location  = azurerm_mssql_server.main.location

  body = {
    sku = { name = var.sql_sku }
    properties = {
      maxSizeBytes                = var.sql_max_size_gb * 1024 * 1024 * 1024
      autoPauseDelay              = var.sql_auto_pause_delay_minutes
      minCapacity                 = 0.5
      useFreeLimit                = true
      freeLimitExhaustionBehavior = var.sql_free_limit_exhaustion_behavior
    }
  }

  lifecycle {
    # The free offer is the dev / ephemeral path — dev is provisioned and torn
    # down repeatedly — so this database is intentionally NOT protected: a plain
    # `terraform destroy` removes it, with no `terraform state rm` step first.
    # (The paid database above is the prod path and keeps prevent_destroy = true.)
    prevent_destroy = false
  }
}

# The id of whichever database was created (paid via azurerm, or free via
# azapi), for downstream references.
locals {
  mssql_database_id = one(concat(
    azurerm_mssql_database.main[*].id,
    azapi_resource.sql_database_free[*].id,
  ))
}

# Allow Azure services — required for the App Service to reach the database.
# Note: this opens the firewall to all Azure-hosted services, not just this
# App Service. Acceptable for a public-endpoint demo; use VNet integration
# and private endpoints for stricter environments.
resource "azurerm_mssql_firewall_rule" "allow_azure_services" {
  name             = "AllowAzureServices"
  server_id        = azurerm_mssql_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# Deployer IP — allows the local machine running terraform apply to connect
# for the null_resource below. Updated automatically on each apply.
resource "azurerm_mssql_firewall_rule" "deployer" {
  name             = "TerraformDeployer"
  server_id        = azurerm_mssql_server.main.id
  start_ip_address = trimspace(data.http.deployer_ip.response_body)
  end_ip_address   = trimspace(data.http.deployer_ip.response_body)
}

# ---------------------------------------------------------------------------
# Contained database user for the App Service managed identity
#
# The managed identity is used exclusively for schema reflection — it reads
# sys.* catalog views and never touches data rows. Its only database grant is
# VIEW DEFINITION (database-wide), which covers both things reflection needs:
#
#   1. Visibility. SQL Server only surfaces an object in the sys.* catalog
#      views to a principal holding SOME permission on it; VIEW DEFINITION
#      qualifies, so reflection sees every table and column.
#   2. Definition text. SQL Server hides the *definition text* of computed
#      columns and default constraints from any principal lacking VIEW
#      DEFINITION, and SQLAlchemy derives a column's computed/default status
#      from that text. Without it a computed column (which SQL rejects on write)
#      or a value-generating default (NEWID/SYSUTCDATETIME) reflects as an
#      ordinary writable column — so creating a row 403s or wrongly demands a
#      server-supplied value.
#
# db_datareader is deliberately NOT granted: it would let this identity read
# every data row while adding nothing reflection needs. Data access always uses
# the signed-in user's OBO token (connection.py), never this identity.
# (reflection.py also classifies correctly under a db_datareader-only identity,
# for databases where VIEW DEFINITION can't be granted — its structural
# sys.columns reads are a safety net there, not a dependency here.)
#
# Prerequisites:
#   - az login must be current with the same identity used as Entra SQL admin
#   - PowerShell 7+ — the provisioner runs via `pwsh` (interpreter below);
#     install with `winget install Microsoft.PowerShell`
# ---------------------------------------------------------------------------
resource "null_resource" "sql_contained_user" {
  # Re-run if the web app or database is recreated.
  triggers = {
    web_app_name = azurerm_linux_web_app.main.name
    database_id  = local.mssql_database_id
  }

  provisioner "local-exec" {
    interpreter = ["pwsh", "-Command"]
    # Uses az CLI to acquire an Azure SQL access token, then executes the
    # SQL via .NET SqlConnection — no sqlcmd dependency or version conflict.
    command = <<-PWSH
      $ErrorActionPreference = 'Stop'
      $token = (az account get-access-token --resource https://database.windows.net/ --query accessToken --output tsv).Trim()
      if (-not $token) { throw 'Failed to acquire Azure SQL token. Run az login and retry.' }
      $conn = New-Object System.Data.SqlClient.SqlConnection('Server=${azurerm_mssql_server.main.fully_qualified_domain_name};Database=${local.mssql_database_name};Encrypt=True;TrustServerCertificate=False;')
      $conn.AccessToken = $token
      $conn.Open()
      $cmd = $conn.CreateCommand()
      # Create the contained user once; GRANT VIEW DEFINITION every run (it's
      # idempotent) so a re-apply re-asserts it. VIEW DEFINITION is the identity's
      # only grant — see the comment block above for why db_datareader is omitted.
      $cmd.CommandText = "IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'${azurerm_linux_web_app.main.name}') BEGIN CREATE USER [${azurerm_linux_web_app.main.name}] FROM EXTERNAL PROVIDER; END; GRANT VIEW DEFINITION TO [${azurerm_linux_web_app.main.name}];"
      $cmd.ExecuteNonQuery() | Out-Null
      $conn.Close()
      Write-Host 'SQL contained user ready: ${azurerm_linux_web_app.main.name}'
    PWSH
  }

  depends_on = [
    azurerm_mssql_database.main,
    azapi_resource.sql_database_free,
    azurerm_linux_web_app.main,
    azurerm_mssql_firewall_rule.deployer,
    azurerm_mssql_firewall_rule.allow_azure_services,
  ]
}
