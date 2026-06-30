"""
security.py — Browser-facing HTTP hardening for the served SPA.

This module owns everything about how bytes are handed to the browser, beyond
the JSON contract:

  1. SecurityHeadersMiddleware — stamps every response with a small set of
     security headers (nosniff, frame/clickjacking, referrer policy, COOP) and a
     Content-Security-Policy. Applied to API and SPA responses alike.

  2. build_csp — constructs the CSP string. The SPA's index.html contains one
     inline <script> (the pre-paint theme setter, kept inline deliberately to
     avoid a flash of the wrong theme). A blanket `script-src 'self'` would block
     it, so the policy pins that script by its SHA-256 hash — read from the actual
     built index.html, so the hash stays correct no matter how the script changes.

  3. CachingStaticFiles — a StaticFiles subclass that adds Cache-Control. Vite
     emits content-hashed bundles under assets/ whose names change on every build,
     so they're immutable and cached for a year; index.html must never be cached
     hard or clients would pin an old build, so it's served `no-cache`.

Compression is handled separately by Starlette's built-in GZipMiddleware, wired
up in main.py — there's nothing app-specific to add for it.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re

from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send


# ---------------------------------------------------------------------------
# Content-Security-Policy
# ---------------------------------------------------------------------------

# Inline <script> blocks (those without a src=). The negative lookahead skips
# the module bundle tag Vite injects (<script type="module" ... src="...">),
# leaving only genuinely inline scripts whose contents must be hashed.
_INLINE_SCRIPT_RE = re.compile(
    rb"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)


def _inline_script_hashes(index_html: bytes) -> list[str]:
    """
    CSP `'sha256-...'` source tokens for every inline script in index_html.

    The browser hashes the exact bytes between the tags, so we hash the raw file
    bytes captured by the regex — no decode/re-encode round-trip that could alter
    them. Returned tokens are ready to drop into a script-src directive.
    """
    tokens: list[str] = []
    for match in _INLINE_SCRIPT_RE.finditer(index_html):
        digest = hashlib.sha256(match.group(1)).digest()
        tokens.append("'sha256-" + base64.b64encode(digest).decode("ascii") + "'")
    return tokens


def build_csp(frontend_dist: str | None) -> str:
    """
    Build the Content-Security-Policy for the app.

    Defaults to same-origin everything. `style-src` allows 'unsafe-inline'
    because the UI toolkit (Tailwind + base-ui) injects inline styles at runtime
    — inline *styles* can't carry script, so this is a routine, low-risk
    allowance. `script-src` stays strict: 'self' plus the SHA-256 of each inline
    script found in the built index.html (the theme bootstrapper), so no
    'unsafe-inline' for scripts is needed. When the SPA isn't mounted (frontend
    not built — dev/test), there are no inline scripts to allow.
    """
    script_src = ["'self'"]
    if frontend_dist:
        index_html = os.path.join(frontend_dist, "index.html")
        try:
            with open(index_html, "rb") as fh:
                script_src.extend(_inline_script_hashes(fh.read()))
        except OSError:
            pass  # No index.html → no inline scripts to pin; 'self' stands alone.

    directives = [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "style-src 'self' 'unsafe-inline'",
        "connect-src 'self'",
        "script-src " + " ".join(script_src),
    ]
    return "; ".join(directives)


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware:
    """
    Pure-ASGI middleware that appends security headers to every HTTP response.

    Pure ASGI (rather than BaseHTTPMiddleware) to match AccessLogMiddleware and
    to avoid buffering the response body — it only edits the header list on
    `http.response.start`. Existing headers of the same name are left untouched,
    so a route that sets its own (e.g. a deliberately different CSP) wins.
    """

    # Raw ASGI header tuples (lowercase name, latin-1 value), precomputed once.
    #   nosniff           — don't let the browser MIME-sniff a response into script
    #   X-Frame-Options   — legacy clickjacking defence (frame-ancestors in CSP is
    #                       the modern equivalent; sent too for old browsers)
    #   Referrer-Policy   — don't leak full URLs/paths to other origins
    #   COOP              — isolate our browsing-context group from openers
    _STATIC_HEADERS: list[tuple[bytes, bytes]] = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"cross-origin-opener-policy", b"same-origin"),
    ]

    def __init__(self, app: ASGIApp, csp: str | None = None) -> None:
        self.app = app
        self._csp = csp.encode("latin-1") if csp else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Swagger UI and ReDoc serve their own HTML that loads scripts/styles from
        # a CDN and runs inline scripts; the strict CSP would break those pages.
        # They're the only non-SPA HTML we serve, so exempt just those document
        # routes from the CSP — every other security header still applies.
        path: str = scope["path"]
        include_csp = self._csp is not None and not (
            path.startswith("/docs") or path.startswith("/redoc")
        )

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                for name, value in self._STATIC_HEADERS:
                    if name not in present:
                        headers.append((name, value))
                if include_csp and b"content-security-policy" not in present:
                    headers.append((b"content-security-policy", self._csp))
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ---------------------------------------------------------------------------
# Cache-Control for static assets
# ---------------------------------------------------------------------------

# One year, the max practical value for `max-age`.
_IMMUTABLE = "public, max-age=31536000, immutable"


class CachingStaticFiles(StaticFiles):
    """
    StaticFiles that sets Cache-Control appropriate to each file.

    Vite writes content-hashed bundles into assets/ (e.g. index-BjVZy8k2.js); the
    filename changes whenever the contents do, so they're safe to cache forever
    and never revalidate (`immutable`). index.html references those hashed names,
    so it must *not* be cached hard — `no-cache` makes the browser revalidate it
    every load (a cheap 304 when unchanged), which is how a new deploy's asset
    URLs get picked up. Anything else (e.g. favicon.svg) gets a modest cache.
    """

    def file_response(self, full_path, stat_result, scope, status_code: int = 200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        name = os.path.basename(str(full_path))
        parent = os.path.basename(os.path.dirname(str(full_path)))
        if parent == "assets":
            response.headers["Cache-Control"] = _IMMUTABLE
        elif name == "index.html":
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers.setdefault("Cache-Control", "public, max-age=3600")
        return response


__all__ = ["build_csp", "SecurityHeadersMiddleware", "CachingStaticFiles"]
