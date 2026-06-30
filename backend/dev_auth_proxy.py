"""
dev_auth_proxy.py — Local development authentication proxy.

NOT part of the deployed application. Never imported by the app.
Run as a separate process during local development only.

What it does
------------
In Azure, App Service EasyAuth intercepts every request, validates the
user's Entra ID token, and injects two headers before forwarding to the
app:

  X-MS-TOKEN-AAD-ACCESS-TOKEN   The user's OAuth2 access token scoped to
                                 Azure SQL (database.windows.net). The app
                                 passes this directly to the ODBC driver to
                                 authenticate as the signed-in user.

  X-MS-CLIENT-PRINCIPAL-NAME    The user's UPN (e.g. mark@contoso.com).
                                 Used for log context.

Locally, EasyAuth doesn't exist. This proxy replicates what it does:

  1. Reuses your developer sign-in (e.g. `az login`) to acquire a real Azure SQL
     access token for your identity — no separate sign-in, no tenant id to set,
     and a real human must be present (no service principal / managed identity).
  2. Caches the token and refreshes it silently ~5 minutes before expiry.
  3. Forwards every incoming request to the FastAPI app on APP_PORT with
     the two headers injected.

The result: the app behaves identically locally and in Azure, and you
authenticate as yourself — so the real table-level SQL grants are exercised
during development.

Usage
-----
1. Sign in with the Azure CLI (once): `az login` — or `az login --tenant <id>`
   if you belong to more than one tenant. The proxy reuses this session, so
   there's nothing to put in .env for auth.

   Optional environment variables (defaults shown):
     APP_PORT     Port the FastAPI app is listening on (default: 8000)
     PROXY_PORT   Port this proxy listens on (default: 8001)

2. Run the proxy (from the backend/ directory):

     uv run python dev_auth_proxy.py

3. Point your browser (or HTTP client) at http://localhost:8001 instead of
   http://localhost:8000. The proxy forwards everything to the app.

4. The first request acquires a token from your az login session; subsequent
   requests reuse the cached token until it nears expiry.

Dependencies
------------
This proxy uses azure-identity and python-dotenv, both already declared in the
backend's pyproject.toml — `uv sync` installs them, so there's nothing extra to
install.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urllib.request
import urllib.error
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("dev_auth_proxy")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_PORT    = int(os.environ.get("APP_PORT",   "8000"))
PROXY_PORT  = int(os.environ.get("PROXY_PORT", "8001"))

AZURE_SQL_SCOPE = "https://database.windows.net/.default"


# ---------------------------------------------------------------------------
# Token cache
#
# Shared mutable state between the proxy handler threads. A threading.Lock
# serialises acquisition so only one interactive sign-in ever fires, even
# if several requests arrive simultaneously before the token is cached.
# ---------------------------------------------------------------------------

_token_lock  = threading.Lock()
_cached_token: str | None       = None
_cached_username: str | None    = None
_token_expires_at: float        = 0.0   # epoch seconds (token_obj.expires_on)
_credential = None


def _get_credential():
    """
    Lazy-initialise the developer credential.

    Reuses an interactive developer sign-in — Azure CLI (`az login`), Azure
    Developer CLI, Azure PowerShell, or the VS Code cache — the same identity the
    app's reflection uses locally, and the one you'd seed the database with, so
    there's no separate sign-in and no tenant id to configure (the sign-in already
    pins the tenant). Service-principal (env var) and managed-identity credentials
    are deliberately excluded: the proxy impersonates a real signed-in *user*, so a
    human must be present and the token carries the upn the identity header needs.
    Import is deferred so the module imports without azure-identity installed; the
    ImportError then surfaces with a clear message at runtime.
    """
    global _credential
    if _credential is None:
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError:
            raise RuntimeError(
                "azure-identity is required for the dev auth proxy.\n"
                "Run `uv sync` from the backend/ directory, then `uv run python dev_auth_proxy.py`."
            )
        _credential = DefaultAzureCredential(
            exclude_environment_credential=True,
            exclude_managed_identity_credential=True,
        )
        logger.info("Using your Azure developer sign-in (az login / azd / etc.).")
    return _credential


def get_token() -> str:
    """
    Return a valid Azure SQL access token, acquiring or refreshing as needed.

    Thread-safe: the lock ensures only one thread does the acquire/refresh
    at a time. All others wait and then use the freshly cached token.
    """
    global _cached_token, _cached_username, _token_expires_at

    # Fast path — token is valid with >5 minute buffer remaining.
    # expires_on is epoch seconds, so this MUST compare against wall-clock
    # time.time(), not time.monotonic() (process uptime). Mixing the two makes
    # the buffer check always pass, so the token is never refreshed and silently
    # dies ~1h in until the proxy is restarted.
    if _cached_token and time.time() < _token_expires_at - 300:
        return _cached_token

    with _token_lock:
        # Re-check inside the lock in case another thread already refreshed.
        if _cached_token and time.time() < _token_expires_at - 300:
            return _cached_token

        logger.info("Acquiring Azure SQL token from your developer sign-in...")
        cred = _get_credential()
        token_obj = cred.get_token(AZURE_SQL_SCOPE)
        _cached_token     = token_obj.token
        _token_expires_at = token_obj.expires_on
        _cached_username  = _decode_username(_cached_token)
        logger.info(
            "Token acquired. Expires at %s",
            time.strftime("%H:%M:%S", time.localtime(token_obj.expires_on)),
        )
        return _cached_token


def _decode_username(token: str) -> str:
    """
    Read the UPN from a JWT access token's payload.

    No signature verification — we just acquired this token ourselves. The UPN
    is in the 'upn' or 'preferred_username' claim. Decoded with the standard
    library: no dependency, and no clash with the unrelated 'jwt' PyPI package
    (which shadows PyJWT and has no top-level decode()).
    """
    import base64, json as _json
    # JWT structure: header.payload.signature (all base64url-encoded).
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)  # restore base64url padding
    claims = _json.loads(base64.urlsafe_b64decode(payload_b64))
    return claims.get("upn") or claims.get("preferred_username") or claims.get("email", "unknown")


def get_username() -> str:
    """
    Return the signed-in user's UPN, decoded once when the token is acquired
    and cached alongside it (rather than re-decoded on every request).
    """
    get_token()  # ensures a fresh token — and username — are cached
    return _cached_username or "unknown"


# ---------------------------------------------------------------------------
# Proxy handler
# ---------------------------------------------------------------------------

class ProxyHandler(BaseHTTPRequestHandler):
    """
    Simple forwarding proxy that injects the two EasyAuth headers.

    Uses urllib (stdlib only, no aiohttp needed) to forward requests
    synchronously. Adequate for local development traffic.
    """

    def log_message(self, fmt, *args):
        # Route handler logs through the standard logger instead of stderr.
        logger.debug("Proxy: " + fmt, *args)

    def _forward(self):
        target = f"http://127.0.0.1:{APP_PORT}{self.path}"

        try:
            token    = get_token()
            username = get_username()
        except Exception as e:
            logger.error("Token acquisition failed: %s", e)
            self.send_response(502)
            self.end_headers()
            self.wfile.write(b"Token acquisition failed -- check console for details.")
            return

        # Copy incoming headers, strip hop-by-hop headers, inject auth headers.
        headers = {}
        for key, value in self.headers.items():
            if key.lower() not in ("host", "connection", "transfer-encoding"):
                headers[key] = value

        headers["X-MS-TOKEN-AAD-ACCESS-TOKEN"] = token
        headers["X-MS-CLIENT-PRINCIPAL-NAME"]  = username

        # Read request body if present.
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else None

        req = urllib.request.Request(
            url=target,
            data=body,
            headers=headers,
            method=self.command,
        )

        try:
            with urllib.request.urlopen(req) as resp:
                self.send_response(resp.status)
                for key, value in resp.headers.items():
                    if key.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(key, value)
                self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for key, value in e.headers.items():
                if key.lower() not in ("transfer-encoding", "connection"):
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(e.read())
        except urllib.error.URLError as e:
            logger.error("Could not reach app on port %d: %s", APP_PORT, e)
            self.send_response(502)
            self.end_headers()
            self.wfile.write(
                f"Could not reach the app on port {APP_PORT}. "
                f"Is it running?\n{e}".encode()
            )

    # Forward all HTTP methods through the same handler.
    def do_GET(self):    self._forward()
    def do_POST(self):   self._forward()
    def do_PUT(self):    self._forward()
    def do_PATCH(self):  self._forward()
    def do_DELETE(self): self._forward()
    def do_OPTIONS(self): self._forward()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info(
        "Dev auth proxy starting on http://localhost:%d → http://localhost:%d",
        PROXY_PORT, APP_PORT,
    )
    logger.info(
        "Point your browser and HTTP client at http://localhost:%d",
        PROXY_PORT,
    )
    logger.info(
        "Using your Azure developer sign-in — run `az login` first if you haven't."
    )

    server = ThreadingHTTPServer(("127.0.0.1", PROXY_PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Proxy stopped.")
        server.server_close()