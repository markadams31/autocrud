<#
.SYNOPSIS
    Provision (or re-provision) an Auto CRUD environment end to end.

.DESCRIPTION
    Wraps the full sequence from infra/README.md into one idempotent command:

      1. Preflight     - terraform / az / sqlcmd present, signed in, tfvars exist
      2. terraform     - init + apply (apply keeps its interactive confirmation)
      3. Image         - az acr build (remote build; no local Docker needed)
      4. App restart   - picks up the freshly pushed image
      5. Database      - seed demo schema (only if not already seeded), grant
                         permissions to the app-users group
      6. Access        - add the signed-in user to the app-users group
      7. Local .env    - written to the repo root for local development
      8. Summary       - the app URL and what's left to do (add other users)

    Safe to re-run: terraform is declarative, the seed is skipped when data
    already exists (force with -ReseedDatabase; the seed script is destructive
    by design), and group membership/permission grants tolerate repeats.

.EXAMPLE
    pwsh infra/provision.ps1                    # provision dev
    pwsh infra/provision.ps1 -Environment prod
    pwsh infra/provision.ps1 -ReseedDatabase    # reset the demo dataset
#>
[CmdletBinding()]
param(
    [ValidateSet('dev', 'prod')]
    [string] $Environment = 'dev',

    # Force the (destructive) demo seed even when the database already has data.
    [switch] $ReseedDatabase,

    # Skip terraform init/apply — run only the post-provisioning steps.
    [switch] $SkipTerraform
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$envDir   = Join-Path $PSScriptRoot "environments/$Environment"

function Step([string] $msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail([string] $msg)  { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# ── 1. Preflight ──────────────────────────────────────────────────────────────
Step "Preflight checks"

foreach ($tool in 'terraform', 'az', 'sqlcmd') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Fail "'$tool' is not installed or not on PATH. See infra/README.md prerequisites."
    }
}
# The seed/permissions steps need go-sqlcmd (Entra auth); the legacy ODBC
# sqlcmd has no --authentication-method flag.
if (-not (& sqlcmd '--help' 2>&1 | Select-String -Quiet 'authentication-method')) {
    Fail "sqlcmd is the legacy ODBC version; the modern go-sqlcmd is required (winget install sqlcmd)."
}

$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) { Fail "Not signed in to Azure. Run: az login" }
Write-Host "Signed in as $($account.user.name) on subscription '$($account.name)'"

foreach ($f in 'terraform.tfvars', 'backend.tfvars') {
    if (-not (Test-Path (Join-Path $envDir $f))) {
        Fail "$envDir\$f is missing. Copy $f.example and fill it in (see infra/README.md step 1)."
    }
}

# ── 2. Terraform ──────────────────────────────────────────────────────────────
Push-Location $envDir
try {
    if (-not $SkipTerraform) {
        Step "terraform init"
        terraform init -backend-config="backend.tfvars" -input=false
        if ($LASTEXITCODE -ne 0) { Fail "terraform init failed" }

        Step "terraform apply (review the plan; terraform will ask for confirmation)"
        terraform apply
        if ($LASTEXITCODE -ne 0) { Fail "terraform apply failed (or was declined)" }
    }

    Step "Reading terraform outputs"
    $out = @{}
    foreach ($name in 'app_service_url', 'app_name', 'acr_name', 'resource_group_name',
                      'app_users_group_name', 'env_db_server', 'env_db_database',
                      'env_db_schemas', 'env_db_audit_columns', 'env_log_level') {
        $out[$name] = terraform output -raw $name
        if ($LASTEXITCODE -ne 0) { Fail "terraform output '$name' unavailable — has this environment been applied?" }
    }
}
finally { Pop-Location }

# ── 3 + 4. Build the image in ACR, restart the app ────────────────────────────
Step "Building the application image in ACR ($($out.acr_name)) — remote build"
az acr build --registry $out.acr_name --image "autocrud:latest" $repoRoot
if ($LASTEXITCODE -ne 0) { Fail "az acr build failed" }

Step "Restarting the App Service to pick up the image"
az webapp restart --name $out.app_name --resource-group $out.resource_group_name | Out-Null

# ── 5. Database: seed (once) + permissions ────────────────────────────────────
$sqlArgs = @('-S', $out.env_db_server, '-d', $out.env_db_database,
             '--authentication-method', 'ActiveDirectoryAzCli', '-b')

Step "Checking whether the database is already seeded"
$seeded = & sqlcmd @sqlArgs -h -1 -W -Q "SET NOCOUNT ON; SELECT IIF(OBJECT_ID('dbo.Currency','U') IS NULL, 0, 1);" 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail ("Cannot reach the database as your az login identity: $seeded`n" +
          "(Your IP is allowlisted by terraform apply — if it changed since, re-run without -SkipTerraform.)")
}

if (("$seeded".Trim() -ne '1') -or $ReseedDatabase) {
    Step "Seeding demo schema + data (database/seed.sql)"
    & sqlcmd @sqlArgs -i (Join-Path $repoRoot 'database/seed.sql')
    if ($LASTEXITCODE -ne 0) { Fail "seed.sql failed" }
} else {
    Write-Host "Database already seeded - skipping (use -ReseedDatabase to reset the demo data)."
}

Step "Granting CRUD permissions to the app-users group ($($out.app_users_group_name))"
# permissions.sql declares the group name inline; substitute this environment's.
$permsSql = (Get-Content (Join-Path $repoRoot 'database/permissions.sql') -Raw) `
    -replace "(?m)(DECLARE\s+@GroupName\s+NVARCHAR\(200\)\s*=\s*N')[^']*(')", "`${1}$($out.app_users_group_name)`${2}"
$tmp = New-TemporaryFile
try {
    Set-Content -Path $tmp -Value $permsSql -Encoding utf8
    & sqlcmd @sqlArgs -i $tmp
    if ($LASTEXITCODE -ne 0) { Fail "permissions.sql failed" }
}
finally { Remove-Item $tmp -Force -Confirm:$false }

# ── 6. Add the signed-in user to the app-users group ─────────────────────────
Step "Adding you to '$($out.app_users_group_name)'"
$selfId = az ad signed-in-user show --query id -o tsv
az ad group member add --group $out.app_users_group_name --member-id $selfId 2>$null
if ($LASTEXITCODE -eq 0) { Write-Host "Added." } else { Write-Host "Already a member - nothing to do." }

# ── 7. Local .env ─────────────────────────────────────────────────────────────
Step "Writing .env at the repo root (hosted-database local development)"
@"
DB_SERVER=$($out.env_db_server)
DB_DATABASE=$($out.env_db_database)
DB_SCHEMAS=$($out.env_db_schemas)
DB_AUDIT_COLUMNS=$($out.env_db_audit_columns)
LOG_LEVEL=$($out.env_log_level)
"@ | Set-Content (Join-Path $repoRoot '.env')

# ── 8. Summary ────────────────────────────────────────────────────────────────
Step "Done"
Write-Host @"
App URL:   $($out.app_service_url)
           (first container pull takes a minute or two; /health reports readiness)

Grant a colleague access:
  az ad group member add --group '$($out.app_users_group_name)' --member-id `$(az ad user show --id <their-upn> --query id -o tsv)

Local development against this environment:
  .env has been written at the repo root - see README.md 'Local development'.
"@
