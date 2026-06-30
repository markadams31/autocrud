"""
middleware.py — Request correlation + access logging.

A single ASGI middleware does two related jobs:

  1. Correlation. Each request gets a short id (an inbound X-Request-ID is
     honoured, e.g. from a gateway), stored in a context variable for the life of
     the request and echoed back as the X-Request-ID response header. A logging
     filter copies that id onto every log record, so — once the log format
     includes %(request_id)s — *every* line emitted while handling a request
     carries the same id: the access line and all the route logs alike. Filtering
     logs by one id shows everything that one request did, which is what you want
     when chasing down a user-reported problem.

  2. Access log. One line per request: method, path, status, elapsed time.
     Request/response bodies and query strings are never logged (filter values
     travel in POST bodies precisely so they stay out of logs, URLs, and
     history) — only the path is recorded. The health probe and hashed static
     assets log at DEBUG to keep the INFO stream readable; client (4xx) and
     server (5xx) responses log at WARNING and ERROR.

This is a pure ASGI middleware rather than BaseHTTPMiddleware so the context
variable set here reliably propagates to the endpoint coroutine that runs within
it (BaseHTTPMiddleware has historically run the endpoint in a separate context).
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import os
import time
from uuid import uuid4

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth_headers import CLIENT_PRINCIPAL_NAME
from app.config import LOG_USER_IDENTITY

logger = logging.getLogger("app.access")

# The in-flight request's id, readable anywhere on its call stack. Defaults to
# "-" for records emitted outside any request (startup, shutdown, background).
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def current_request_id() -> str:
    """The id of the in-flight request, or '-' outside a request."""
    return _request_id.get()


def display_user(name: str | None) -> str:
    """
    Render a user identity for logs according to config.LOG_USER_IDENTITY.

    The email is PII; this is the one place the policy is applied so the access
    log and the CRUD audit lines stay consistent. "hash" yields a short, stable
    pseudonym (so a user's requests still correlate) without storing the address;
    set LOG_USER_IDENTITY_SALT to resist reversing it for known addresses.
    """
    if not name:
        return "-"
    if LOG_USER_IDENTITY == "none":
        return "-"
    if LOG_USER_IDENTITY == "hash":
        salt = os.getenv("LOG_USER_IDENTITY_SALT", "")
        digest = hashlib.sha256((salt + name).encode("utf-8")).hexdigest()
        return "u_" + digest[:12]
    return name  # "email" (default)


def route_template(scope: Scope) -> str:
    """
    The matched route's template (e.g. /api/{schema}/{table}/{pk}) for the
    request, or the raw path if it can't be determined.

    Why not the raw path: the concrete path embeds ids (/api/ppm/Task/12), so
    using it as a log/metric dimension explodes cardinality — you can't ask "p95
    latency of Task fetches" because every id is a distinct value. The template
    collapses them. Starlette stamps `path_params` onto the scope during routing
    (in place — this middleware sees it after the inner app returns); we rebuild
    the template by swapping each param's value back to its {name}, matching whole
    path segments only. Best-effort: a static segment equal to a param value
    (e.g. a table literally named "query") could be mis-labelled — the
    authoritative templated name also lives in App Insights' AppRequests.Name.
    """
    path = scope.get("path", "")
    params = scope.get("path_params") or {}
    if not params:
        return path
    by_value = {str(v): "{%s}" % k for k, v in params.items()}
    return "/".join(by_value.get(seg, seg) for seg in path.split("/"))


class RequestContextLogFilter(logging.Filter):
    """
    Stamp every log record with the current request id, so a format string can
    reference %(request_id)s. Attached to the application's log handler(s) in
    main._configure_logging; runs before formatting, so the attribute is always
    present (even '-' for non-request logs).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


_factory_installed = False


def install_request_id_log_factory() -> None:
    """
    Guarantee every LogRecord carries a `request_id` attribute at *creation*
    time — regardless of which logger emitted it or whether a handler filter has
    been attached yet.

    A handler filter alone isn't enough: during startup, third-party libraries
    (notably the Azure Monitor exporter as it initialises) emit records that
    reach the console handler *before* the filter is attached. The format string
    references %(request_id)s, so each of those records made the formatter raise
    `ValueError: Formatting field not found in record: 'request_id'`, burying the
    real startup logs under a storm of 'Logging error' tracebacks. Setting the
    value in a record factory closes that window for good. Idempotent.
    """
    global _factory_installed
    if _factory_installed:
        return
    old_factory = logging.getLogRecordFactory()

    def _factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        if not hasattr(record, "request_id"):
            record.request_id = _request_id.get()
        return record

    logging.setLogRecordFactory(_factory)
    _factory_installed = True


def _access_fields(
    method: str, path: str, route: str, status: int, elapsed_ms: float, user: str
) -> dict[str, object]:
    """
    The access line's facts as structured log-record fields.

    Passed as `extra=` so they ride on the LogRecord without changing the
    human-readable message. The Application Insights exporter surfaces them as
    customDimensions; locally they're simply ignored by the console formatter.
    Keys are deliberately namespaced (http_*) to avoid colliding with reserved
    LogRecord attributes. `http_route` is the low-cardinality template for
    aggregation; `http_path` keeps the concrete path for pinpointing one request.
    """
    return {
        "http_method": method,
        "http_path": path,
        "http_route": route,
        "http_status": status,
        "duration_ms": round(elapsed_ms, 1),
        "user": user,
    }


def _level_for(status: int, path: str) -> int:
    """Severity for an access line: errors stand out, routine noise is quiet."""
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING
    # Routine, high-volume reads are demoted to DEBUG so they don't dominate the
    # INFO stream (and the Application Insights ingest/cost): the health probe,
    # hashed static assets, and the FK-dropdown option lookups — a single page
    # load fans out to one /options/ call per foreign key, easily 15-20 of them.
    if path == "/health" or path.startswith("/assets/") or "/options/" in path:
        return logging.DEBUG
    return logging.INFO


class AccessLogMiddleware:
    """Pure-ASGI access log + request-id correlation. See the module docstring."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get("x-request-id") or uuid4().hex[:8]
        # For log context only — auth/authorization is the OBO connection's job.
        # Header lookup is case-insensitive, so the canonical constant matches.
        # display_user applies the PII policy (email / hash / none).
        user = display_user(headers.get(CLIENT_PRINCIPAL_NAME))
        method: str = scope["method"]
        path: str = scope["path"]

        status_code = 500  # assume the worst until we see the response start

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                # Append the id as a raw ASGI header (lowercase name, byte value).
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", request_id.encode("latin-1")))
            await send(message)

        token = _request_id.set(request_id)
        start = time.perf_counter()
        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            # A genuinely unhandled error escaping to the server-error middleware.
            # Log it (with the id still set), then re-raise so the registered
            # handler still produces the 500 response.
            elapsed_ms = (time.perf_counter() - start) * 1000
            route = route_template(scope)
            logger.error(
                "%s %s -> 500 (%.1f ms) user=%s",
                method, path, elapsed_ms, user,
                extra=_access_fields(method, path, route, 500, elapsed_ms, user),
            )
            raise
        else:
            elapsed_ms = (time.perf_counter() - start) * 1000
            # path_params are on the scope now (routing ran); resolve the route
            # template for low-cardinality aggregation.
            route = route_template(scope)
            logger.log(
                _level_for(status_code, path),
                "%s %s -> %d (%.1f ms) user=%s",
                method, path, status_code, elapsed_ms, user,
                # The same facts, as discrete fields. When logs are exported to
                # Application Insights these become customDimensions, so an
                # operator can filter/aggregate by status, latency, route, or
                # user (e.g. all 5xx, or p95 duration per route) instead of
                # regex-parsing the rendered message string.
                extra=_access_fields(method, path, route, status_code, elapsed_ms, user),
            )
        finally:
            _request_id.reset(token)
