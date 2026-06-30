resource "azurerm_log_analytics_workspace" "main" {
  name                = module.naming.log_analytics_workspace.name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_in_days
}

resource "azurerm_application_insights" "main" {
  name                = module.naming.application_insights.name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
}

# Stream the web app's platform logs to Log Analytics as a backstop to the
# in-app Application Insights export (see backend/app/telemetry.py).
#
# Why both: the app-side OpenTelemetry exporter only starts once the app is up,
# so a boot-time failure — image pull/ACR-auth errors, an import crash, a config
# validation failure before logging is configured — produces *no* AppTraces.
# AppServiceConsoleLogs captures the container's raw stdout/stderr regardless,
# which is exactly what's needed to diagnose a container that never started.
# Without this, those logs are only reachable via a manual `az webapp log
# download` zip and aren't queryable in Log Analytics at all.
resource "azurerm_monitor_diagnostic_setting" "web_app" {
  name                       = "send-to-log-analytics"
  target_resource_id         = azurerm_linux_web_app.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category = "AppServiceConsoleLogs" # container stdout/stderr
  }

  enabled_log {
    category = "AppServiceHTTPLogs" # one record per inbound HTTP request
  }

  enabled_log {
    category = "AppServicePlatformLogs" # image pull / container lifecycle
  }

  enabled_metric {
    category = "AllMetrics"
  }
}
