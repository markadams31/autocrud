# Stable UUID for the app role — computed once and stored in state.
resource "random_uuid" "app_role_id" {}

resource "azuread_application" "main" {
  display_name     = "${var.app_name}-${var.environment}"
  sign_in_audience = "AzureADMyOrg"

  # ---------------------------------------------------------------------------
  # App role — "User"
  #
  # app_role_assignment_required = true on the service principal (below) means
  # Entra enforces role membership before the user can authenticate. Anyone not
  # assigned the User role — directly or via the security group — is rejected
  # at the Entra login page, before they ever reach EasyAuth or the app.
  # ---------------------------------------------------------------------------
  app_role {
    allowed_member_types = ["User"]
    description          = "Standard application user. Required for access."
    display_name         = "User"
    enabled              = true
    id                   = random_uuid.app_role_id.result
    value                = "User"
  }

  # ---------------------------------------------------------------------------
  # API permissions
  #
  # Microsoft Graph — openid, profile, email: standard OIDC sign-in claims.
  # Azure SQL Database — user_impersonation: EasyAuth acquires a SQL-scoped
  #   token for the signed-in user and injects it as X-MS-TOKEN-AAD-ACCESS-TOKEN.
  #   The app passes this directly to the ODBC driver (connection.py) so SQL
  #   Server authenticates the real caller, not the application identity.
  # ---------------------------------------------------------------------------
  required_resource_access {
    resource_app_id = "00000003-0000-0000-c000-000000000000" # Microsoft Graph

    resource_access {
      id   = "37f7f235-527c-4136-accd-4a02d197296e" # openid
      type = "Scope"
    }
    resource_access {
      id   = "14dad69e-099b-42c9-810b-d002981feec1" # profile
      type = "Scope"
    }
    resource_access {
      id   = "64a6cdd6-aab1-4aaf-94b8-3cc8405e90d6" # email
      type = "Scope"
    }
  }

  required_resource_access {
    resource_app_id = "022907d3-0f1b-48f7-badc-1ba6abab6d66" # Azure SQL Database

    resource_access {
      id   = "c39ef2d1-04ce-46dc-8b5f-e9a5c60f0fc9" # user_impersonation
      type = "Scope"
    }
  }

  web {
    # EasyAuth callback URL — must exactly match the deployed App Service hostname.
    redirect_uris = [
      "https://${local.linux_web_app_name}.azurewebsites.net/.auth/login/aad/callback",
    ]

    implicit_grant {
      access_token_issuance_enabled = false # Server-side auth; access tokens not needed from implicit flow.
      id_token_issuance_enabled     = true
    }
  }
}

resource "azuread_service_principal" "main" {
  client_id = azuread_application.main.client_id

  # Enforces that users must be explicitly assigned the User app role.
  # Without this, any user in the tenant can authenticate.
  app_role_assignment_required = true
}

# ---------------------------------------------------------------------------
# Federated identity credential — managed identity as the EasyAuth credential
#
# Establishes trust so a token issued for the dedicated user-assigned identity
# (azurerm_user_assigned_identity.easyauth) can be presented as the client
# assertion for THIS app registration. That is what lets EasyAuth authenticate
# its OAuth code exchange without a client secret. Paired with the
# OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID app setting on the web app.
#
#   subject  — the identity's principal (object) ID
#   audience — api://AzureADTokenExchange (the fixed token-exchange audience)
# ---------------------------------------------------------------------------
resource "azuread_application_federated_identity_credential" "easyauth_mi" {
  application_id = azuread_application.main.id
  display_name   = "easyauth-mi-${var.environment}"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://login.microsoftonline.com/${var.tenant_id}/v2.0"
  subject        = azurerm_user_assigned_identity.easyauth.principal_id
}

# Client secret — SUPERSEDED by the federated identity credential above.
# auth_settings_v2 now authenticates with the managed identity; this secret is
# kept dormant for one release as an instant rollback and is slated for removal
# in a follow-up once the MI login is confirmed on dev. The far-future end_date
# is left untouched so this dying secret isn't needlessly rotated on its way out.
resource "azuread_application_password" "easyauth" {
  application_id = azuread_application.main.id
  display_name   = "easyauth-${var.environment}"
  end_date       = "2099-01-01T00:00:00Z"
}

# ---------------------------------------------------------------------------
# Security group
#
# Add users to this group in the Entra admin centre to grant them access.
# The group is assigned the User app role below.
#
# Note: assigning a group to an app role requires Entra ID P1 or P2 (or
# Microsoft 365 E3+). If your tenant does not have the required licence,
# assign the User role to individual users via the Enterprise Application
# blade instead, and remove the azuread_app_role_assignment resource below.
# ---------------------------------------------------------------------------
resource "azuread_group" "app_users" {
  display_name     = "${var.app_name}-${var.environment}-users"
  security_enabled = true
  mail_enabled     = false
}

resource "azuread_app_role_assignment" "app_users" {
  app_role_id         = random_uuid.app_role_id.result
  principal_object_id = azuread_group.app_users.object_id
  resource_object_id  = azuread_service_principal.main.object_id
}
