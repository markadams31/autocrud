resource "azurerm_container_registry" "main" {
  name                = local.container_registry_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = var.acr_sku

  # Admin credentials disabled — the App Service pulls using its managed identity
  # via the AcrPull role assignment in app_service.tf.
  admin_enabled = false

  tags = local.common_tags
}
