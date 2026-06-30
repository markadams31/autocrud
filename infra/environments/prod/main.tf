module "autocrud" {
  source = "../../modules/autocrud"

  environment = "prod"
  tenant_id   = var.tenant_id

  # Prod-specific overrides
  app_service_sku = "P1v3"
  log_level       = "INFO"

  # Schemas to reflect and DB-managed audit columns — supplied per-deployment
  # via terraform.tfvars (db_schemas is required; db_audit_columns optional).
  db_schemas       = var.db_schemas
  db_audit_columns = var.db_audit_columns

  # Auto-pause in prod
  sql_auto_pause_delay_minutes = 30

  # Uncomment to override other defaults:
  # app_name         = "autocrud"
  # location         = "australiaeast"
  # sql_sku          = "GP_S_Gen5_1"
  # sql_max_size_gb  = 32
  # acr_sku          = "Basic"
  # docker_image_name = "autocrud"
  # docker_image_tag  = "latest"
  # log_user_identity          = "hash"   # "email" | "hash" | "none" — consider hash/none in prod
  # log_user_identity_salt     = ""        # required when log_user_identity = "hash"
  # appinsights_sampling_ratio = 1.0       # 0.0–1.0; lower to cut App Insights cost
  # bulk_max_rows              = 1000
}

# Post-provisioning commands, numbered in the order they're run. Terraform sorts
# outputs alphabetically and forbids a leading digit, so the "_N_" prefix is what
# floats these to the top (in order) above the other outputs below — "_" sorts
# before "a". The $(...) subexpressions in the commands resolve your signed-in
# identity and work in both bash and PowerShell. Fetch one with e.g.
# `terraform output -raw _1_add_self_to_group_command`.
output "_1_add_self_to_group_command" { value = module.autocrud.add_self_to_group_command }
output "_2_seed_database_command" { value = module.autocrud.seed_database_command }
output "_3_grant_permissions_command" { value = module.autocrud.grant_permissions_command }
output "_4_build_and_push_command" { value = module.autocrud.build_and_push_command }
output "_5_deploy_command" { value = module.autocrud.deploy_command }

# Identifiers — sort alphabetically, below the numbered commands.
output "app_service_url" { value = module.autocrud.app_service_url }
output "app_users_group_name" { value = module.autocrud.app_users_group_name }
