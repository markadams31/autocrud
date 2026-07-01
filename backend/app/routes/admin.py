"""
routes/admin.py — Operational endpoints.

Four endpoints:

  GET  /health          Readiness check. Returns 200 only if the application
                        is running, the schema snapshot is loaded, and the
                        database is reachable; 503 otherwise. Used by Azure
                        App Service to route traffic only to instances that
                        can actually serve requests.

  GET  /version         The running build's provenance (commit SHA + build
                        time, baked into the image — see app.build_info).
                        Served from process constants, no snapshot or database
                        needed. The frontend's About dialog reads it.

  GET  /config          Runtime configuration the browser needs — currently the
                        Application Insights connection string (or null), so the
                        frontend telemetry SDK can initialise against the same
                        resource. No snapshot or database needed.

  POST /admin/refresh   Re-reflect the database schema and rebuild all
                        Pydantic models without restarting the process.
                        Useful after DDL changes (new tables, columns,
                        constraints). Not surfaced in the frontend — an
                        advanced user navigates to it directly.

Authentication is handled at the infrastructure level by EasyAuth. All
endpoints are therefore accessible to any authenticated user. This is
intentional for /health (monitoring tools need it), acceptable for /version
(it exposes only the build id) and /config (the App Insights key is designed to
be client-embedded; serving it only to signed-in users is if anything tighter
than baking it into public JS), and acceptable for /admin/refresh (a schema
re-reflection is read-only and harmless to trigger unnecessarily).
"""

from __future__ import annotations

import logging
import threading
import time

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app import build_info, telemetry
from app.connection import reflection_engine
from app.errors import ApiError, ErrorCode
from app.reflection import reflect_schemas
from app.state import get_snapshot, set_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Database readiness
#
# /health is wired to App Service's health probe. Checking only the in-memory
# snapshot makes it a pure liveness check: if SQL becomes unreachable *after*
# startup (token expiry, failover, network), the snapshot is still loaded, so
# /health would keep returning 200 and App Service would keep routing traffic
# to an instance that fails every data request. A lightweight DB ping turns it
# into a readiness check that reflects whether the instance can actually serve.
#
# The result is cached for a few seconds so the frequent probe doesn't open a
# round-trip every time; a failure is re-checked once the short TTL lapses, so
# recovery is detected promptly. The ping uses the managed-identity reflection
# engine (no user token needed) and pool_pre_ping keeps the connection warm.
#
# Caveat: SQL is a shared dependency, so an outage marks every instance
# unhealthy at once — App Service keeps the last instance rather than pulling
# the whole site, and the signal surfaces the real problem in monitoring.
# ---------------------------------------------------------------------------

_DB_CHECK_TTL_SECONDS = 10.0
_db_check_lock = threading.Lock()
_db_check_cache: tuple[float, bool] | None = None  # (monotonic_ts, reachable)


def database_is_reachable() -> bool:
    """
    True if a trivial `SELECT 1` succeeds on the managed-identity engine, cached
    for _DB_CHECK_TTL_SECONDS. A FastAPI dependency so tests can override it.
    """
    global _db_check_cache
    now = time.monotonic()
    with _db_check_lock:
        if _db_check_cache is not None and now - _db_check_cache[0] < _DB_CHECK_TTL_SECONDS:
            return _db_check_cache[1]

    # Probe outside the lock — it does I/O. A couple of concurrent probes on a
    # cold cache is harmless and bounded by the pool.
    try:
        with reflection_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        reachable = True
    except Exception:
        logger.warning("Health check: database ping failed", exc_info=True)
        reachable = False

    with _db_check_lock:
        _db_check_cache = (time.monotonic(), reachable)
    return reachable


# ---------------------------------------------------------------------------
# Refresh throttle
#
# /admin/refresh re-reads the entire catalog and swaps a process-wide snapshot,
# so it is both relatively expensive and global in effect. EasyAuth makes it
# reachable by any authenticated user (see the module docstring), so without a
# throttle a user could trigger repeated re-reflections and exhaust database and
# CPU resources for everyone. The result is shared globally, so running it more
# than once per interval yields nothing anyway.
#
# The throttle is a single global minimum-interval gate (not per-user): the slot
# is reserved at the START of an attempt — before the slow reflection runs — so
# concurrent and back-to-back calls are bounded even while a refresh is in
# flight, and a failing reflection still counts against the limit (it costs the
# same database round-trips). The lock only guards the tiny check-and-reserve;
# the reflection itself runs outside it.
# ---------------------------------------------------------------------------

_REFRESH_MIN_INTERVAL_SECONDS = 120.0
_refresh_lock = threading.Lock()
_last_refresh_monotonic: float | None = None


def reset_refresh_throttle() -> None:
    """Clear the throttle so the next refresh is allowed immediately. For tests."""
    global _last_refresh_monotonic
    with _refresh_lock:
        _last_refresh_monotonic = None


def _reserve_refresh_slot() -> None:
    """
    Enforce the minimum interval between refreshes and reserve this attempt's
    slot. Raises ApiError(RATE_LIMITED, 429) if a refresh ran too recently.
    """
    global _last_refresh_monotonic
    now = time.monotonic()
    with _refresh_lock:
        if _last_refresh_monotonic is not None:
            elapsed = now - _last_refresh_monotonic
            if elapsed < _REFRESH_MIN_INTERVAL_SECONDS:
                retry_after = _REFRESH_MIN_INTERVAL_SECONDS - elapsed
                raise ApiError(
                    ErrorCode.RATE_LIMITED,
                    f"Schema refresh was run moments ago. Try again in "
                    f"{retry_after:.0f}s.",
                )
        # Reserve before the (slow) reflection so concurrent callers are gated too.
        _last_refresh_monotonic = now


@router.get("/health")
def health(db_reachable: bool = Depends(database_is_reachable)) -> dict:
    """
    Readiness check.

    Returns 200 with the number of reflected tables only if the application is
    running, the schema snapshot is loaded, AND the database is reachable.
    Returns 503 if the snapshot is missing (startup still in progress or failed)
    or if the database can't be reached — so App Service stops routing traffic
    to an instance that can't actually serve data.
    """
    try:
        snapshot = get_snapshot()
    except RuntimeError:
        # Snapshot not yet loaded — startup is still in progress or failed.
        raise ApiError(
            ErrorCode.DATABASE_UNAVAILABLE,
            "Schema snapshot not yet available — startup may still be in progress.",
        )

    if not db_reachable:
        raise ApiError(
            ErrorCode.DATABASE_UNAVAILABLE,
            "Database is not reachable.",
        )

    return {"status": "ok", "tables": len(snapshot.tables)}


@router.get("/version")
def version() -> dict:
    """
    The running build's provenance: the commit SHA the image was built from and
    its UTC build time, baked into the image at build time (see app.build_info).

    Served from process constants — no snapshot or database needed, so it answers
    even while startup is still in progress. The About dialog shows this; values
    of "dev" mean the app is running outside a CI-built image (local uvicorn).
    """
    return build_info.as_dict()


@router.get("/config")
def config() -> dict:
    """
    Runtime configuration the browser needs to bootstrap.

    Currently just the Application Insights connection string (or null when
    telemetry isn't configured — local dev, or a deployment without App Insights),
    so the frontend SDK initialises against the same resource and its telemetry
    correlates with the server's. Served from process state — no snapshot or
    database — so it answers even during startup.
    """
    return {"applicationInsights": {"connectionString": telemetry.connection_string()}}


@router.post("/admin/refresh")
def refresh_schema() -> dict:
    """
    Re-reflect the database schema and install a fresh snapshot.

    Reflects all schemas in config.DB_SCHEMAS from scratch, rebuilds all
    Pydantic models, and atomically swaps the new snapshot into place.
    In-flight requests finish against the previous snapshot; the next
    request picks up the new one.

    Returns the names of all reflected tables grouped by schema.

    Throttled to at most once per _REFRESH_MIN_INTERVAL_SECONDS across all
    callers; a request that arrives sooner gets 429 RATE_LIMITED.
    """
    _reserve_refresh_slot()

    try:
        snapshot = reflect_schemas()
    except Exception:
        logger.exception("Schema refresh failed")
        raise ApiError(
            ErrorCode.INTERNAL_ERROR,
            "Schema refresh failed — check server logs for details.",
        )

    set_snapshot(snapshot)

    by_schema: dict[str, list[str]] = {}
    for (schema, table) in snapshot.tables:
        by_schema.setdefault(schema, []).append(table)
    for schema in by_schema:
        by_schema[schema].sort()

    total = len(snapshot.tables)
    logger.info("Schema refreshed: %d table(s) across %d schema(s)", total, len(by_schema))

    return {
        "status":  "ok",
        "total":   total,
        "schemas": by_schema,
    }
