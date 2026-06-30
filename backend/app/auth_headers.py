"""
auth_headers.py — EasyAuth request-header names, in one place.

App Service EasyAuth (and, locally, the dev auth proxy) injects the signed-in
user's identity and access token as request headers. Several layers read them —
the OBO connection (the access token), the /me route and the access log (the
display name) — so the exact header names live here rather than being re-typed
as string literals in each. One spelling, one source of truth.

This module deliberately has no dependencies so anything can import it cheaply.
"""

from __future__ import annotations

# The signed-in user's display name — usually their UPN / email. Used only for
# log context; never for authentication or authorization.
CLIENT_PRINCIPAL_NAME = "X-MS-CLIENT-PRINCIPAL-NAME"

# The signed-in user's Entra (Azure AD) object id.
CLIENT_PRINCIPAL_ID = "X-MS-CLIENT-PRINCIPAL-ID"

# The user's OBO access token. This is the app's authentication signal: it
# connects to the database as the real user, and SQL Server enforces that
# user's grants. Its presence also implies a full EasyAuth session.
USER_ACCESS_TOKEN = "X-MS-TOKEN-AAD-ACCESS-TOKEN"
