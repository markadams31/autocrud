# CI/CD workflows

| Workflow | Trigger | What it does |
|---|---|---|
| [`ci.yml`](ci.yml) | PR into `main`; called by the deploy workflows | Path-filtered: backend unit/API + integration, frontend lint/unit/build + Playwright e2e, Docker build check, Terraform fmt + validate. A `CI success` gate job aggregates them. |
| [`codeql.yml`](codeql.yml) | PR + push to `main`, weekly | CodeQL static analysis (Python + JS/TS) |
| [`dependency-review.yml`](dependency-review.yml) | PR into `main` | Blocks PRs that add known-vulnerable dependencies |
| [`deploy.yml`](deploy.yml) | Reusable (`workflow_call`) | Build image in ACR → repoint App Service → restart → `/health` smoke check. Called by both deploy workflows so the logic lives in one place. |
| [`deploy-dev.yml`](deploy-dev.yml) | Push to `main` | Runs CI, then calls `deploy.yml` for the **dev** environment |
| [`deploy-prod.yml`](deploy-prod.yml) | Published Release, or manual dispatch | Runs CI, waits for approval, then calls `deploy.yml` for **prod** |

CI runs on `ubuntu-latest` with **Python 3.13** (via `uv`) and **Node 22**, matching the `Dockerfile`. On PRs the jobs are **path-filtered** — a frontend-only change skips the backend tiers, a docs-only change skips everything; the deploy gate (`workflow_call`) always runs the full suite. Third-party actions are **pinned to commit SHAs** and kept current by **Dependabot** ([`.github/dependabot.yml`](../dependabot.yml)). Deployments authenticate to Azure with **OIDC federated credentials** — no long-lived secrets are stored in the repository.

## One-time setup

The CI workflows run with no configuration. The **deploy** workflows need the following before they will work.

### 1. GitHub Environments

Create two environments under **Settings → Environments**: `dev` and `prod`.

- On **`prod`**, add a **Required reviewers** protection rule. GitHub then pauses `deploy-prod` for manual approval before anything ships to production.

Dev and prod live in **separate Azure subscriptions**, so each environment holds its own values below.

### 2. Per-environment secrets and variables

On each environment (`dev` and `prod`), set:

**Secrets** (the OIDC identity — not sensitive with federated credentials, but kept as secrets by convention):

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | App registration (or user-assigned identity) client ID for that environment |
| `AZURE_TENANT_ID` | Entra tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Subscription ID for that environment |

**Variables** (resource names — the Terraform outputs `acr_name`, `app_name`, `resource_group_name`):

| Variable | Value | Terraform output |
|---|---|---|
| `ACR_NAME` | Container registry name (no `.azurecr.io`) | `acr_name` |
| `APP_NAME` | App Service name | `app_name` |
| `RESOURCE_GROUP` | Resource group name | `resource_group_name` |

### 3. Azure federated credentials + roles

For **each** environment's identity (an Entra app registration or a user-assigned managed identity), in its own subscription:

1. **Add a federated credential** scoped to this repository and environment:
   - Entity: **Environment**
   - Environment name: `dev` (and a second credential for `prod`)
   - This produces the subject `repo:<OWNER>/<REPO>:environment:dev` (and `:prod`).

2. **Assign roles** so the workflow can build and deploy:
   - **`Contributor`** on the environment's container registry — for `az acr build`, which
     queues an ACR Tasks run (it needs `Microsoft.ContainerRegistry/registries/scheduleRun/action`).
     `AcrPush` only grants image push/pull and is **not** sufficient to start a build.
   - **`Website Contributor`** on the App Service (or `Contributor` on the resource group) — for `az webapp config container set` + `restart`.

### 4. Branch protection

Under **Settings → Branches**, protect `main` and require the **`CI success`** status check before merge. Require *that one* check (not the individual tier jobs): the tiers are path-filtered and may be skipped, whereas `CI success` always runs and passes only when every tier that ran succeeded. Also require **`CodeQL`** and **`Dependency review`**.

## How a change reaches production

```
PR ─→ ci.yml (path-filtered) + codeql + dependency-review   (CI success must pass to merge)
merge to main ─→ deploy-dev.yml  (full CI gate → deploy.yml: build in ACR → deploy dev → /health smoke check)
publish a Release ─→ deploy-prod.yml  (full CI gate → manual approval → deploy.yml → deploy prod)
```

Images are tagged with the commit SHA (dev) or the release tag (prod) and the App Service is pointed at that immutable tag, so a rollback is a redeploy of an earlier tag. This matches the `lifecycle { ignore_changes = [...docker_image_name] }` in `infra/modules/autocrud/app_service.tf`, which lets the pipeline own the running image without Terraform drift.

The deploy **smoke check** asserts a real `200` from `/health`, which the app returns only when the schema snapshot is loaded *and* the database is reachable. `/health` is excluded from EasyAuth (`excluded_paths` in `app_service.tf`) so both the smoke check and the App Service health probe get the app's true status instead of a login redirect.
