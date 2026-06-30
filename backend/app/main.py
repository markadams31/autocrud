"""
main.py — FastAPI application entry point.

Responsibilities
----------------
  1. Define the custom JSON response class (Decimal → str, datetime → ISO 8601)
  2. Register a single exception handler that converts ApiError — and any
     unhandled exception — into the standard error JSON shape
  3. Install the per-request access-log middleware
  4. Wire up the four route modules under their URL prefixes
  5. Run schema reflection at startup via the lifespan handler
  6. Mount the React SPA at / as a catch-all after all API routes

Everything else (connection management, schema reflection, route logic,
error mapping) lives in its own module. main.py is intentionally thin.

URL structure
-------------
  /api/{schema}/{table}/query         POST  — search / filter / paginate
  /api/{schema}/{table}               POST  — create row
  /api/{schema}/{table}/{pk}          GET   — fetch row
  /api/{schema}/{table}/{pk}          PUT   — update row (partial semantics)
  /api/{schema}/{table}/{pk}          PATCH — update row (partial semantics)
  /api/{schema}/{table}/{pk}          DELETE

  /api/{schema}/{table}/bulk-create   POST  — import many rows in one transaction
  /api/{schema}/{table}/bulk-update   POST  — apply one change to many rows
  /api/{schema}/{table}/bulk-delete   POST  — delete many rows

  /meta                               GET   — schemas + connected database name
  /meta/{schema}                      GET   — permission-filtered table list
  /meta/{schema}/{table}              GET   — column metadata for one table
  /meta/{schema}/{table}/options/{col} GET  — FK dropdown values

  /admin/refresh                      POST  — re-reflect schema
  /health                             GET   — liveness check

  /me                                 GET   — signed-in user (EasyAuth identity)

  /                                   SPA catch-all (mounted last)
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

# Imported first so database query tracing can be wired before app.connection is
# imported below: instrument_sqlalchemy patches sqlalchemy.create_engine so every
# Engine built afterwards emits a span per statement to App Insights — the
# reflection engine (built when app.connection is imported) and the per-user OBO
# engines (built per request). connection.py binds `create_engine` at its own
# import, so the patch has to be in place first. No-op when telemetry is disabled.
from app.telemetry import (
    configure_telemetry,
    instrument_fastapi,
    instrument_sqlalchemy,
)

instrument_sqlalchemy()

from app.connection import reflection_engine, dispose_user_engines
from app.errors import ApiError, ErrorCode
from app.middleware import (
    AccessLogMiddleware,
    RequestContextLogFilter,
    install_request_id_log_factory,
)
from app.security import CachingStaticFiles, SecurityHeadersMiddleware, build_csp
from app.reflection import reflect_schemas
from app.routes import admin, crud, identity, meta
from app.state import set_snapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom JSON response class
#
# FastAPI's default JSON encoder renders Decimal as a float (loses precision)
# and raises on datetime objects. We replace it once here so every route
# that returns a plain dict gets correct serialisation automatically —
# no per-route conversion calls needed.
#
# Rules:
#   Decimal  → string  ("1234.56") — preserves precision, round-trips via
#              Pydantic coercion on write
#   datetime → ISO 8601 string, with timezone offset if present
#   date     → ISO 8601 date string  ("2024-06-15")
#   time     → ISO 8601 time string  ("13:45:00")
#   UUID     → canonical string      ("xxxxxxxx-xxxx-...")
#   bytes    → hex string (safety fallback; binary cols excluded from reads)
#   Everything else JSON already handles passes through unchanged.
# ---------------------------------------------------------------------------

class _AppEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        if isinstance(obj, datetime.date):
            return obj.isoformat()
        if isinstance(obj, datetime.time):
            return obj.isoformat()
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, (bytes, bytearray, memoryview)):
            return bytes(obj).hex()
        return super().default(obj)


class AppJSONResponse(JSONResponse):
    """JSONResponse that uses _AppEncoder for all content."""
    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            cls=_AppEncoder,
            ensure_ascii=False,    # preserve Unicode characters in strings
            allow_nan=False,       # NaN/Infinity are not valid JSON
        ).encode("utf-8")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _configure_logging() -> None:
    """
    Configure application logging from LOG_LEVEL, then enable Azure Monitor export.

    Runs at import time (see the call below `app = FastAPI(...)` further down):
    Azure Monitor must be configured *before* the FastAPI app is constructed so
    the distro's instrumentation traces it and log records inherit each request's
    trace context — see app.telemetry. force=True gives us full control of the
    root handler's format and level.

    Order matters: install the request-id factory first (so nothing can emit a
    record the %(request_id)s formatter chokes on), configure the console handler,
    quiet third-party loggers (so the exporter's init chatter is damped), *then*
    attach the Azure Monitor exporter (after basicConfig's force=True, so it isn't
    wiped), and finally stamp the request-id filter onto every handler.

    NOT done here: disabling uvicorn's duplicate access log — that must happen
    after uvicorn installs its own logging, so it lives in the lifespan startup.
    """
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    # Make `request_id` present on every record at creation time, *before* any
    # logging happens — so the %(request_id)s formatter never trips over a record
    # emitted before the handler filter is attached (e.g. by the Azure Monitor
    # exporter during configure_telemetry below). Belt to the filter's braces.
    install_request_id_log_factory()

    # Emit timestamps in UTC so they're unambiguous regardless of the host's
    # timezone, and include the date and milliseconds so sub-second ordering and
    # the day are clear from the log line itself, not only from the container
    # runtime's own prefix.
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=level,
        # %(request_id)s ties every line to its request — see RequestContextLogFilter.
        format="%(asctime)s.%(msecs)03dZ  %(request_id)-8s  %(name)-34s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )

    # Silence high-volume third-party loggers so they don't obscure application
    # output — set before configure_telemetry so the exporter's own init chatter
    # (azure.*) is damped too.
    for name in ("sqlalchemy.engine", "sqlalchemy.pool", "azure", "urllib3", "msal"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Ship logs/traces/exceptions/requests to Application Insights when a
    # connection string is configured. No-op locally. Adds its own handler to the
    # root logger, which is why it runs after basicConfig and before the filter loop.
    configure_telemetry()

    # Stamp every record with the current request id so one id correlates the
    # access line and all the route logs for a request. The filter goes on the
    # handler(s) so it runs before formatting/export and request_id is always
    # present — on the exporter's records as a customDimension, too.
    for handler in logging.getLogger().handlers:
        handler.addFilter(RequestContextLogFilter())

    logger.info("Log level: %s", logging.getLevelName(level))


# Configure logging + telemetry now, before the app is constructed — see the
# function docstring and app.telemetry for why the ordering matters.
_configure_logging()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup: silence uvicorn's duplicate access log and reflect the schema.
    Shutdown: dispose the reflection engine and every per-user engine so their
    connection pools close deterministically rather than at interpreter exit.
    """
    # Uvicorn's default access logger emits its own "GET / HTTP/1.1 200" line for
    # every request — a duplicate of our richer app.access line (which adds the
    # request id, user, route, and latency), minus all of that context. Disable
    # it here, after uvicorn has installed its own logging, so each request
    # produces one access line, not two, and log volume halves.
    logging.getLogger("uvicorn.access").disabled = True

    logger.info("Starting up — reflecting database schema...")
    snapshot = reflect_schemas()
    set_snapshot(snapshot)
    logger.info("Startup complete.")
    yield
    logger.info("Shutting down — disposing connection pools...")
    dispose_user_engines()
    reflection_engine.dispose()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Auto CRUD",
    description="Metadata-driven CRUD API over Azure SQL Database.",
    default_response_class=AppJSONResponse,
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Exception handlers
#
# One handler covers all ApiError instances raised deliberately by route
# code. A second catches everything else and returns a generic 500 so no
# unhandled exception ever leaks a stack trace or internal message to the
# client.
# ---------------------------------------------------------------------------

@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> AppJSONResponse:
    return AppJSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> AppJSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    error = ApiError(ErrorCode.INTERNAL_ERROR)
    return AppJSONResponse(status_code=error.status_code, content=error.to_dict())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> AppJSONResponse:
    fields = {}
    for err in exc.errors():
        loc = err.get("loc", ())
        fields[str(loc[-1]) if loc else "unknown"] = err.get("msg", "Invalid value")
    api_error = ApiError(ErrorCode.VALIDATION_ERROR, fields=fields or None)
    return AppJSONResponse(status_code=api_error.status_code, content=api_error.to_dict())


# ---------------------------------------------------------------------------
# Middleware
#
# add_middleware nests last-added outermost, so the registration below runs as:
#
#   AccessLog → SecurityHeaders → GZip → route/SPA      (request, top→bottom)
#   route/SPA → GZip → SecurityHeaders → AccessLog      (response, bottom→top)
#
#   AccessLog       — outermost: sets the request-id context for everything
#                     inside, and its latency/X-Request-ID cover compression too.
#                     One access line per request (method, path, status, latency)
#                     plus request correlation; see app.middleware.
#   SecurityHeaders — security headers + CSP on every response (app.security).
#   GZip            — innermost: gzip-compresses responses (incl. the ~880 KB JS
#                     and CSS bundles, which App Service for Linux containers does
#                     not compress for us). level 6 trades a little ratio for much
#                     less per-request CPU than the level-9 default.
#
# The CSP is built from the *served* index.html so its script-src pins the inline
# theme bootstrapper by hash (see app.security.build_csp); _FRONTEND_DIST below.
# ---------------------------------------------------------------------------

_FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "frontend", "dist")
_SPA_MOUNTED = os.path.isdir(_FRONTEND_DIST)

app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)
app.add_middleware(
    SecurityHeadersMiddleware,
    csp=build_csp(_FRONTEND_DIST if _SPA_MOUNTED else None),
)
app.add_middleware(AccessLogMiddleware)

# Add the App Insights request-span middleware last, so it nests outermost and
# wraps the access log — the access summary line is then captured inside the
# request span (shared OperationId). No-op when telemetry is disabled.
instrument_fastapi(app)


# ---------------------------------------------------------------------------
# Routes
#
# Registration order matters for path matching:
#   - /api routes before /meta before /admin before /health
#   - Within /api, the router already registers /query before /{pk}
#   - The SPA static mount must be last — it catches everything not matched
#     above, including deep client-side routes like /tables/dbo/Organisation
# ---------------------------------------------------------------------------

app.include_router(crud.router)
app.include_router(meta.router)
app.include_router(admin.router)
app.include_router(identity.router)


# ---------------------------------------------------------------------------
# React SPA — mounted last as catch-all
#
# html=True tells StaticFiles to serve index.html for any path that doesn't
# match a file, enabling client-side routing in the React app.
# CachingStaticFiles adds Cache-Control: content-hashed bundles under assets/
# are cached immutably for a year, index.html is served no-cache (see
# app.security). _FRONTEND_DIST is resolved relative to this file (above), not
# the process's working directory, so the SPA mounts correctly regardless of
# where uvicorn is launched from. Skipped if the directory doesn't exist (e.g.
# frontend not yet built in a development environment) so the API remains usable
# on its own.
# ---------------------------------------------------------------------------

if _SPA_MOUNTED:
    app.mount(
        "/",
        CachingStaticFiles(directory=_FRONTEND_DIST, html=True),
        name="static",
    )
else:
    logger.warning(
        "frontend/dist not found — SPA not mounted. "
        "API endpoints are still available."
    )
