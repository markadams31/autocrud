resource "azurerm_service_plan" "main" {
  name                = module.naming.app_service_plan.name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = var.app_service_sku
  tags                = local.common_tags
}

# ---------------------------------------------------------------------------
# Dedicated identity for EasyAuth's confidential-client login.
#
# Exists ONLY to back the federated identity credential on the app registration
# (entra.tf), which lets EasyAuth authenticate its OAuth code exchange with a
# managed-identity assertion instead of a client secret — nothing to store or
# rotate. Kept SEPARATE from the app's system-assigned identity (which pulls from
# ACR and reads SQL for reflection): the identity trusted to act as the login
# client must not carry unrelated privileges. See Microsoft's "use a managed
# identity instead of a secret" for App Service built-in auth.
# ---------------------------------------------------------------------------
resource "azurerm_user_assigned_identity" "easyauth" {
  name                = module.naming.user_assigned_identity.name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.common_tags
}

resource "azurerm_linux_web_app" "main" {
  name                = local.linux_web_app_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  service_plan_id     = azurerm_service_plan.main.id
  https_only          = true
  tags                = local.common_tags

  # System-assigned identity: ACR pull (role assignment below) and schema
  # reflection to Azure SQL (connection.py DefaultAzureCredential). Its
  # principal_id is identity[0].principal_id even with a user-assigned identity
  # also attached. User-assigned identity: dedicated to EasyAuth login — backs
  # the federated credential (entra.tf) and is named in the
  # OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID app setting below.
  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.easyauth.id]
  }

  site_config {
    always_on = true

    # Reject TLS below 1.2 and turn off the FTP/FTPS publishing endpoint — the
    # image is deployed by managed-identity ACR pull (site_config above) and the
    # pipeline's `az webapp config container set`, never FTP, so the deployment
    # surface stays closed. minimum_tls_version defaults to 1.2 on this provider
    # already; set explicitly so the posture is legible as compliance evidence.
    minimum_tls_version = "1.2"
    ftps_state          = "Disabled"

    # Route App Service's health probe to the app's /health readiness endpoint so
    # an instance that can't serve (startup reflection failed, or the database is
    # unreachable — returns 503, see routes/admin.py) is taken out of rotation and
    # auto-healed rather than serving errors.
    health_check_path                 = "/health"
    health_check_eviction_time_in_min = 5

    # Pull the image from ACR using this app's managed identity (granted AcrPull
    # below) rather than registry admin credentials — which are disabled. Without
    # this flag App Service falls back to admin creds and the pull fails with
    # ImagePullUnauthorizedFailure.
    container_registry_use_managed_identity = true

    application_stack {
      docker_image_name   = "${var.docker_image_name}:${var.docker_image_tag}"
      docker_registry_url = "https://${azurerm_container_registry.main.login_server}"
    }
  }

  # Turn on web-server HTTP logging so the AppServiceHTTPLogs diagnostic category
  # (routed to Log Analytics in monitoring.tf) actually emits — one record per
  # inbound request. Without this httpLoggingEnabled is false and that table stays
  # empty even with the diagnostic setting in place. It's the edge-level request
  # view that in-app instrumentation can't see: e.g. an expired-session EasyAuth
  # 302 → Entra redirect, which is answered before the request reaches the app.
  logs {
    http_logs {
      file_system {
        retention_in_days = 7
        retention_in_mb   = 35
      }
    }
  }

  app_settings = {
    # Mirrors the env vars the app reads (see backend/app/config.py, main.py,
    # middleware.py, telemetry.py).
    DB_SERVER   = azurerm_mssql_server.main.fully_qualified_domain_name
    DB_DATABASE = local.mssql_database_name
    DB_SCHEMAS  = join(",", var.db_schemas)
    # No DB_DRIVER: mssql-python bundles its own SQL Server driver, so there is
    # no ODBC driver name to configure (the app ignores a leftover setting).
    DB_AUDIT_COLUMNS = join(",", var.db_audit_columns)
    BULK_MAX_ROWS    = tostring(var.bulk_max_rows)
    LOG_LEVEL        = var.log_level

    # How the signed-in user is recorded in logs (email / hash / none) and the
    # salt for hash mode. See app/config.py and app/middleware.py.
    LOG_USER_IDENTITY      = var.log_user_identity
    LOG_USER_IDENTITY_SALT = var.log_user_identity_salt

    WEBSITES_PORT = "8000"

    # Full readiness probe (live DB round-trip) vs snapshot-only liveness. Set
    # false where the database is serverless with auto-pause, so the frequent
    # health probe doesn't keep it awake — see routes/admin.py and config.py.
    HEALTH_CHECK_DATABASE = var.health_check_database ? "true" : "false"

    # Application Insights. The app self-instruments via the azure-monitor-
    # opentelemetry distro (telemetry.py), which reads ONLY the connection
    # string. The legacy APPINSIGHTS_INSTRUMENTATIONKEY and the codeless
    # ApplicationInsightsAgent_EXTENSION_VERSION agent (not supported for custom
    # containers anyway) are deliberately omitted — connection string throughout.
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.main.connection_string
    APPINSIGHTS_SAMPLING_RATIO            = tostring(var.appinsights_sampling_ratio)

    # EasyAuth confidential-client credential — the app's user-assigned identity,
    # used as a federated credential (entra.tf) so EasyAuth authenticates its code
    # exchange with a managed identity, no client secret. App Service treats this
    # specific setting name specially; auth_settings_v2.client_secret_setting_name
    # points at it below.
    OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID = azurerm_user_assigned_identity.easyauth.client_id
  }

  # ---------------------------------------------------------------------------
  # EasyAuth v2 — Microsoft Entra ID
  #
  # Validates every inbound request. Unauthenticated requests are redirected
  # to the Entra login page. After login, EasyAuth also acquires an Azure SQL
  # token on the user's behalf (via the user_impersonation scope requested
  # below) and injects it as X-MS-TOKEN-AAD-ACCESS-TOKEN — the header the app
  # reads in connection.py to authenticate database connections as the real user.
  # ---------------------------------------------------------------------------
  auth_settings_v2 {
    auth_enabled           = true
    default_provider       = "azureactivedirectory"
    unauthenticated_action = "RedirectToLoginPage"
    require_https          = true

    # Let /health through without authentication. Both the App Service health
    # probe (site_config.health_check_path above) and the CI deploy smoke check
    # hit it anonymously; without this they'd get a 302 to the Entra login page
    # instead of the app's real 200/503. /health exposes only a table count.
    excluded_paths = ["/health"]

    active_directory_v2 {
      client_id                  = azuread_application.main.client_id
      client_secret_setting_name = "OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID"
      tenant_auth_endpoint       = "https://login.microsoftonline.com/${var.tenant_id}/v2.0"
      allowed_audiences          = ["api://${azuread_application.main.client_id}"]

      # Request the Azure SQL scope so EasyAuth acquires and stores the user's
      # SQL token for injection into downstream requests.
      login_parameters = {
        scope = "openid profile email offline_access https://database.windows.net/user_impersonation"
      }
    }

    login {
      token_store_enabled = true
    }
  }

  lifecycle {
    # Image tag is managed by the deployment pipeline, not Terraform.
    # After the initial deploy, update the running image with:
    #   az webapp config container set \
    #     --name <app-name> --resource-group <rg> \
    #     --docker-custom-image-name <acr-login-server>/<image>:<tag>
    ignore_changes = [
      site_config[0].application_stack[0].docker_image_name,
    ]
  }
}

# Grant the App Service's managed identity permission to pull images from ACR.
# No registry admin credentials are needed with this in place.
resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_linux_web_app.main.identity[0].principal_id
}
