# Infrastructure

Terraform configuration for deploying Auto CRUD to Azure. Provisions all of an
environment's infrastructure from scratch. The only things left to do by hand are a
handful of post-`apply` commands, which Terraform prints ready to copy-paste in bash
from the repo root (see [Provisioning](#provisioning)).

## What gets provisioned

| Resource | Purpose |
|---|---|
| Resource Group | Container for all environment resources |
| Azure Container Registry | Stores the application's Docker image (SPA + API) |
| Azure SQL Server | Hosts the database. Entra-only auth — no SQL password |
| Azure SQL Database | The database itself. Serverless by default |
| App Service Plan | Compute for the web app |
| Linux Web App | Runs the application container |
| Log Analytics Workspace | Telemetry sink for Application Insights |
| Application Insights | Request traces, exceptions, dependency tracking |
| Entra App Registration | Identity for EasyAuth — one per environment |
| Entra Service Principal | Enterprise application object tied to the registration |
| Entra Security Group | Add users here to grant access to the application |
| App Role Assignment | Assigns the security group the `User` app role |

## Directory structure

```
infra/
├── modules/
│   └── autocrud/           Shared module — the canonical definition of what
│       ├── versions.tf       one environment looks like. All environments use
│       ├── variables.tf      this module; the only differences between them
│       ├── naming.tf         are the variable values passed in.
│       ├── resource_group.tf
│       ├── acr.tf
│       ├── monitoring.tf
│       ├── sql.tf
│       ├── app_service.tf
│       ├── entra.tf
│       └── outputs.tf
└── environments/
    ├── dev/                Dev environment — B1 App Service, serverless SQL
    │   ├── terraform.tf
    │   ├── main.tf
    │   ├── variables.tf
    │   ├── terraform.tfvars.example
    │   └── backend.tfvars.example
    └── prod/               Prod environment — P1v3 App Service, serverless SQL
        ├── terraform.tf
        ├── main.tf
        ├── variables.tf
        ├── terraform.tfvars.example
        └── backend.tfvars.example
```

The module pattern is used rather than duplicating configuration per environment.
A structural change — adding a new resource, changing a policy — is made once in
the module and both environments pick it up on the next `terraform apply`. Drift
between environments is structurally prevented, not just by convention.

## Key decisions

**Docker container over zip deploy**
The backend depends on `pyodbc`, which requires the Microsoft ODBC Driver for
SQL Server to be installed on the host OS. Zip deploy relies on an App Service
startup script to install it, which is fragile and slow. Docker bakes the driver
into the image at build time — the runtime environment is fixed and reproducible.

**Entra-only SQL authentication**
No SQL admin password is created, stored, or rotated. The SQL Server is
configured with an Entra administrator (the identity running `terraform apply`)
and `azuread_authentication_only = true`. All connections — for schema reflection
and for data access — use Entra tokens. There are no credentials anywhere in the
configuration.

**Two identities, two purposes**
The App Service has a system-assigned managed identity used exclusively for two
things: pulling Docker images from ACR and connecting to SQL for schema
reflection. It is granted `AcrPull` on the registry and, on the database, a single
permission: `VIEW DEFINITION`.

That one grant covers both things reflection needs. SQL Server only surfaces an
object in the `sys.*` catalog views to a principal holding *some* permission on it,
and `VIEW DEFINITION` qualifies — so reflection sees every table and column. It also
reveals the *definition text* of computed columns and default constraints, which SQL
Server otherwise hides; reflection derives a column's computed/default status from
that text, and without it a computed column (which SQL rejects on write) or a
value-generating default reflects as an ordinary writable column, so creating a row
fails or wrongly demands a database-supplied value.

`db_datareader` is deliberately *not* granted. It would let the identity read every
data row while adding nothing reflection needs — data access always uses the
signed-in user's token, never this one. Keeping it to `VIEW DEFINITION` holds the
identity to metadata, exactly as intended. (The app also classifies correctly
against databases where it has only `db_datareader` and no `VIEW DEFINITION`, by
reading structural flags from `sys.columns` directly — that's a portability safety
net, not something this deployment relies on.)

All data access uses the signed-in user's token, acquired by EasyAuth via the
On-Behalf-Of flow and injected as a request header. SQL Server authenticates the
real caller on every query. Authorization lives entirely in SQL grants — the
application enforces none of its own.

**EasyAuth at the infrastructure level**
Authentication and the acquisition of the user's Azure SQL token are handled
entirely by App Service EasyAuth, configured here in Terraform. The application
code reads headers that EasyAuth injects — it contains no auth logic, no OAuth
flow, and no token acquisition. The same headers are present in both production
(real EasyAuth) and local development (the `dev_auth_proxy.py` script).

**Public SQL endpoint**
The SQL Server uses a public endpoint with two firewall rules: one that allows
Azure services (required for the App Service to connect) and one that whitelists
the IP of whoever is running `terraform apply` (required for the
`null_resource` that creates the contained database user). For stricter
environments, replace these with VNet integration and a private endpoint.

**Serverless SQL**
Both environments use a serverless General Purpose SKU, which scales compute to
zero after a configurable idle period. The SKU is a variable, so any valid
Azure SQL SKU can be supplied.

Dev additionally enables the **Azure SQL Database free offer**
(`sql_use_free_limit`, on a `GP_S_Gen5_2` SKU): a monthly free compute allowance
plus 32 GB of storage. When the allowance is exhausted the database auto-pauses
until the next month rather than billing (`sql_free_limit_exhaustion_behavior`).
The azurerm provider doesn't expose the free-limit properties, and Azure only
accepts them when the database is created (a paid database can't be converted to
free). So with the free offer enabled the database is created via azapi;
otherwise it's created via azurerm. Azure permits only one free database per
subscription, so it's enabled on dev alone — which sits in its own subscription,
separate from prod.

**Separate subscriptions per environment**
Dev and prod are deployed into separate Azure subscriptions. Terraform state is
stored in a third subscription (an existing storage account) and is configured
separately via `backend.tfvars` at init time.

**`prevent_destroy` on the paid database only**
The paid `azurerm_mssql_database` (prod) carries `lifecycle { prevent_destroy = true }`,
so Terraform refuses to destroy it even if it's removed from configuration — production
data shouldn't be one `terraform destroy` away from gone. The free-offer `azapi_resource`
(dev, see *Serverless SQL* above) deliberately does **not**: dev is ephemeral and gets
torn down and rebuilt often, so it destroys cleanly with no extra step.

`prevent_destroy` only accepts a literal `true`/`false`, never a variable, so it can't be
keyed to the environment directly — but it doesn't need to be. Only one of the two database
resources exists per environment (paid for prod, free for dev), so the flag is simply set
per resource.

To intentionally drop the **prod** database, first remove it from state, then apply:
```
terraform state rm 'module.autocrud.azurerm_mssql_database.main[0]'
```

## Prerequisites

| Requirement | Notes |
|---|---|
| Terraform >= 1.7 | `winget install Hashicorp.Terraform` |
| Azure CLI | `winget install Microsoft.AzureCLI` |
| PowerShell 7+ (`pwsh`) | `winget install Microsoft.PowerShell` — the SQL-user provisioner runs via `pwsh` |
| Entra permissions | Application Administrator + Groups Administrator (or Global Administrator) |
| Entra P1/P2 licence | Required to assign a security group to an app role. See note below |

> **No P1/P2?** Remove the `azuread_app_role_assignment` resource from
> `modules/autocrud/entra.tf` and assign the `User` app role to individual users
> directly via the Entra portal (Enterprise Applications → your app →
> Users and groups → Add user).

## Provisioning

Run these steps once per environment. Both environments are independent — each
has its own state file and can be applied in any order.

### 1. Configure

```powershell
cd infra/environments/dev   # or prod

# Landing zone variables (subscription ID, tenant ID)
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars

# Remote state backend (separate storage account subscription)
cp backend.tfvars.example backend.tfvars
# Edit backend.tfvars
```

### 2. Authenticate

```powershell
az login
# Use the same subscription_id you set in terraform.tfvars (step 1)
az account set --subscription <landing-zone-subscription-id>
```

The identity you log in with becomes the Entra administrator on the SQL Server
for this environment, so it must have the Entra permissions listed above. It also
needs access to **both** subscriptions involved: the landing-zone subscription
(where resources are created, from `terraform.tfvars`) and the state-storage
subscription (read at `terraform init`, from `backend.tfvars`).

### 3. Initialise and apply

```powershell
terraform init -backend-config="backend.tfvars"
terraform plan
terraform apply
```

`terraform apply` creates all Azure resources and then runs a `null_resource`
that acquires an Azure SQL access token via `az account get-access-token` and
creates the App Service managed identity as a contained database user using
PowerShell's built-in `SqlConnection`. This requires your local machine to have
network access to the SQL Server — the deployer firewall rule is added
automatically using your current public IP.

### 4. Note the outputs

After a successful apply, Terraform prints the key values you need for the
next steps.

The five post-provisioning commands are prefixed `_1_`…`_5_` so they sort to the
top of the summary in the order you run them — they're complete and ready to
copy-paste into bash from the root directory.

The `env_*` outputs are the values for a local `.env`. Assemble one at the repo
root (run from this environment directory; no driver setting — mssql-python
bundles its own SQL Server driver):

```powershell
@"
DB_SERVER=$(terraform output -raw env_db_server)
DB_DATABASE=$(terraform output -raw env_db_database)
DB_SCHEMAS=$(terraform output -raw env_db_schemas)
DB_AUDIT_COLUMNS=$(terraform output -raw env_db_audit_columns)
LOG_LEVEL=$(terraform output -raw env_log_level)
"@ | Set-Content ../../../.env
```

## Updating after initial provisioning

**Deploying a new image**
Image updates bypass Terraform (see `lifecycle.ignore_changes` in
`app_service.tf`). Build a new image into ACR and restart the App Service:
```powershell
az acr build --registry <acr-name> --image autocrud:<new-tag> .
az webapp restart --name <app-name> --resource-group <resource-group>
```

**Changing infrastructure**
Modify the module, then apply from the relevant environment directory:
```powershell
cd infra/environments/dev
terraform apply
```
Always apply to dev before prod and review the plan output carefully.

**Refreshing the schema snapshot**
After a DDL change (new table, new column), the app's in-memory schema snapshot
can be refreshed without redeploying:
```
POST https://<app-service-url>/admin/refresh
```

**Changing the SQL database SKU**
Update `sql_sku` in the environment's `main.tf` and apply. Azure SQL SKU changes
are applied in-place with no downtime for most transitions.
