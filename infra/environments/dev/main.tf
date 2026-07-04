module "autocrud" {
  source = "../../modules/autocrud"

  environment = "dev"
  tenant_id   = var.tenant_id

  # Dev-specific overrides
  app_service_sku = "B1"
  log_level       = "DEBUG"

  # Schemas to reflect and DB-managed audit columns — supplied per-deployment
  # via terraform.tfvars (db_schemas is required; db_audit_columns optional).
  db_schemas       = var.db_schemas
  db_audit_columns = var.db_audit_columns

  # Auto-pause after 60 minutes of inactivity — keeps dev costs low.
  sql_auto_pause_delay_minutes = 60

  # Serverless SKU — required for the Azure SQL Database free offer below.
  sql_sku = "GP_S_Gen5_2"

  # Azure SQL Database free offer: a monthly free compute allowance plus 32 GB
  # storage (the module's default size). One free database per subscription;
  # AutoPause stops it at no charge once the free allowance is used up.
  sql_use_free_limit                 = true
  sql_free_limit_exhaustion_behavior = "AutoPause"

  # Skip the live DB round-trip in /health so the ~1/min App Service health probe
  # doesn't keep this serverless database awake — it can then auto-pause when idle
  # (conserving the free vCore-second allowance). Trades a cold start on the next
  # real request, which is fine for dev. Prod keeps the full readiness check.
  health_check_database = false

  # Local dev applies run from an interactive `az login`, so add the deployer to
  # the SQL admins group — it inherits admin and can run the contained-user
  # provisioner. (In CI this would be false; dev is applied from the workstation.)
  sql_admin_include_deployer = true

  # Uncomment to override other defaults:
  # app_name          = "autocrud"
  # location          = "australiaeast"
  # sql_max_size_gb   = 32
  # acr_sku           = "Basic"
  # docker_image_name = "autocrud"
  # docker_image_tag  = "latest"
  # log_user_identity          = "email"  # "email" | "hash" | "none"
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
output "sql_admins_group_name" { value = module.autocrud.sql_admins_group_name }

# Values for a local .env at the repo root — see .env.example.
output "env_db_server" { value = module.autocrud.env_db_server }
output "env_db_database" { value = module.autocrud.env_db_database }
output "env_db_schemas" { value = module.autocrud.env_db_schemas }
output "env_db_audit_columns" { value = module.autocrud.env_db_audit_columns }
output "env_azure_tenant_id" { value = module.autocrud.env_azure_tenant_id }
output "env_log_level" { value = module.autocrud.env_log_level }
