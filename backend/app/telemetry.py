"""
telemetry.py — Export logs, traces, and request telemetry to Azure Application Insights.

What this module does
---------------------
When an Application Insights resource is provisioned for the App Service, its
connection string is injected as APPLICATIONINSIGHTS_CONNECTION_STRING (see
infra/.../app_service.tf). `configure_telemetry()` reads that string and wires the
standard `azure-monitor-opentelemetry` distro so the telemetry the app emits (the
access log, the per-route QUERY/UPDATE/DELETE lines, unhandled-exception
tracebacks) reaches Azure instead of living only in the container's stdout — where
it is retrievable solely as a manual log-zip download, not queryable in Log
Analytics, barely retained, and impossible to alert on.

The distro ships:

  * every Python `logging` record  -> App Insights `traces`     (AppTraces)
  * unhandled exceptions           -> App Insights `exceptions`  (AppExceptions)
  * one span per HTTP request       -> App Insights `requests`    (AppRequests)

The per-request id stamped by app.middleware and the structured fields attached
to the access log (method, path, route, status, duration_ms, user) ride along as
customDimensions, so logs become *queryable* — `traces | where customDimensions.http_status >= 500`
instead of grepping a text dump.

Init order matters. This is called from main.py at import time, *before*
`app = FastAPI(...)` is constructed: the distro instruments the FastAPI class, so
only apps built after the call are traced. Configuring it first also means every
log record emitted while handling a request inherits that request span's trace
context, so AppTraces and AppRequests share an OperationId and App Insights'
end-to-end transaction view works (rather than every trace carrying an all-zero
operation id).

Gating
------
Everything here is gated on APPLICATIONINSIGHTS_CONNECTION_STRING being set. When
it is absent — local dev, tests, CI — `configure_telemetry()` is a complete no-op
and the dependency is never imported, so running without Azure is unaffected.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# The App Service injects this; its presence is what switches export on.
_CONNECTION_STRING_ENV = "APPLICATIONINSIGHTS_CONNECTION_STRING"

# Set by instrument_sqlalchemy() and read by configure_telemetry() so the "enabled"
# confirmation lands in the configured log. instrument_sqlalchemy runs at import,
# before logging is set up (see its docstring), so it can't emit a visible line of
# its own — configure_telemetry, which runs after basicConfig, reports it instead.
_sqlalchemy_instrumented = False


def telemetry_enabled() -> bool:
    """True when an App Insights connection string is configured."""
    return bool(os.getenv(_CONNECTION_STRING_ENV))


def _sampling_ratio() -> float:
    """
    Fraction of request traces to keep, from APPINSIGHTS_SAMPLING_RATIO (0.0–1.0,
    default 1.0 = keep everything). Lower it to cap ingestion cost on a chatty
    deployment; the app's read endpoints fan out (one /meta/options call per FK
    on a page load), so volume can climb fast. Logs/exceptions are unaffected —
    this samples request spans. Invalid values fall back to 1.0.
    """
    try:
        ratio = float(os.getenv("APPINSIGHTS_SAMPLING_RATIO", "1.0"))
    except ValueError:
        return 1.0
    return min(1.0, max(0.0, ratio))


def configure_telemetry() -> bool:
    """
    Enable Azure Monitor export if a connection string is present.

    Returns True when telemetry was configured, False when it was skipped (no
    connection string, or the optional dependency isn't installed). Never raises:
    a telemetry problem must not stop the app from serving requests.

    Called from main.py at import time, before `app = FastAPI(...)` — see the
    module docstring for why ordering matters (FastAPI instrumentation + trace
    correlation).
    """
    if not telemetry_enabled():
        logger.info(
            "%s not set — Application Insights export disabled "
            "(logs go to stdout only).",
            _CONNECTION_STRING_ENV,
        )
        return False

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
    except ImportError:
        # The package is an optional runtime dependency. If the image was built
        # without it, degrade gracefully to stdout-only logging.
        logger.warning(
            "azure-monitor-opentelemetry is not installed — "
            "Application Insights export disabled."
        )
        return False

    # Telemetry must never take down the app: a bad connection string, a network
    # issue, or an exporter bug should degrade to stdout-only logging, not crash
    # startup. Everything below runs under one guard.
    try:
        # Reads the connection string from the environment. Attaches an
        # OpenTelemetry logging handler to the root logger (so every app log
        # record is exported) and sets up the trace/metric exporters.
        configure_azure_monitor(
            # This app has no live-metrics dashboard need; leaving Quickpulse on
            # spun up a manager that logged init chatter and timed out reaching
            # the live endpoint (an AppExceptions ServiceResponseTimeoutError).
            enable_live_metrics=False,
            # Request-span sampling (default keep-all); see _sampling_ratio.
            sampling_ratio=_sampling_ratio(),
            # Only instrument what this app actually uses. The distro otherwise
            # probes for Django/Flask/psycopg2/Azure-SDK instrumentations that
            # aren't installed and logs a DependencyConflict / ModuleNotFoundError
            # for each on every cold start — noise that lands in AppExceptions and
            # masks real errors. FastAPI is disabled here because the distro's
            # class-level auto-instrumentation produced no request spans in this
            # app; instrument_fastapi() below does it explicitly per-app instead.
            # requests/urllib stay on (defaults) for outbound dependency spans.
            instrumentation_options={
                "azure_sdk": {"enabled": False},
                "django": {"enabled": False},
                "fastapi": {"enabled": False},
                "flask": {"enabled": False},
                "psycopg2": {"enabled": False},
            },
        )
    except Exception:  # noqa: BLE001 — never let telemetry setup stop the app
        logger.exception(
            "Azure Monitor configuration failed — "
            "continuing with stdout-only logging."
        )
        return False

    logger.info("Application Insights telemetry export enabled.")
    if _sqlalchemy_instrumented:
        # The patch was installed earlier (before app.connection was imported);
        # confirm it here, where logging is configured and the line is visible.
        logger.info("SQLAlchemy database instrumentation enabled.")
    return True


def instrument_sqlalchemy() -> bool:
    """
    Trace every database statement as an App Insights dependency (AppDependencies).

    Patches `sqlalchemy.create_engine` so each Engine built afterwards emits a span
    per executed statement — the SQL text, the target server/database, and the
    duration — nested under the active request span (shared OperationId). Database
    time that previously hid inside a request's total latency (e.g. a slow
    /meta/options FK lookup) becomes individually visible and attributable.

    Global patching, rather than instrumenting a named engine, because this app has
    no fixed set of engines: connection.py builds a fresh pooled Engine per
    signed-in user on demand, alongside the managed-identity reflection engine.
    Patching create_engine catches them all — but only engines created *after* the
    patch, and connection.py binds `create_engine` by name when it is imported.
    main.py therefore calls this *before* importing app.connection.

    Because it runs that early — before _configure_logging() sets up the root
    handler — a success line emitted here would be dropped, so this function only
    surfaces failures; configure_telemetry() reports success once logging is up.

    Gated on telemetry being enabled, idempotent, and best-effort: a missing
    optional dependency or any setup error degrades to no DB spans, never a failed
    startup.
    """
    global _sqlalchemy_instrumented
    if not telemetry_enabled() or _sqlalchemy_instrumented:
        return _sqlalchemy_instrumented
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        # No engine argument: wrap create_engine so every engine built afterwards
        # is traced (the per-user OBO engines have no fixed handle to pass here).
        SQLAlchemyInstrumentor().instrument()
    except Exception:  # noqa: BLE001 — DB spans are a bonus, never fatal
        logger.warning(
            "SQLAlchemy instrumentation unavailable — no database dependency spans.",
            exc_info=True,
        )
        return False

    _sqlalchemy_instrumented = True
    return True


def instrument_fastapi(app) -> bool:
    """
    Emit one request span per HTTP request (App Insights AppRequests) and give
    every log record emitted during a request the span's trace context — so
    AppTraces and AppRequests share an OperationId and the end-to-end transaction
    view works (instead of every trace carrying an all-zero operation id).

    Called from main.py right after `app = FastAPI(...)` and after the app's own
    middleware are registered — at import time, before the server starts, so the
    OpenTelemetry middleware can still be added (it can't be, once the app is
    running) and so it sits *outermost*, wrapping the access-log middleware so the
    access summary line is captured inside the request span too.

    Explicit per-app instrumentation rather than the distro's class-level
    auto-instrumentation, which produced no spans for this app even when
    configured before the app was constructed. Best-effort and gated on telemetry
    being enabled: never blocks startup.
    """
    if not telemetry_enabled():
        return False
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:  # noqa: BLE001 — request spans are a bonus, never fatal
        logger.warning(
            "FastAPI request instrumentation unavailable — "
            "no AppRequests / trace correlation.",
            exc_info=True,
        )
        return False

    logger.info("FastAPI request instrumentation enabled.")
    return True
