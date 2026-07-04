"""
test_telemetry.py — Application Insights wiring actually survives a real request.

telemetry.py is inert unless APPLICATIONINSIGHTS_CONNECTION_STRING is set, so
none of this ever runs in the rest of the suite — a regression here has zero
coverage anywhere else. That blind spot let three real incidents ship: an
azure-monitor-opentelemetry release whose exporter couldn't import against the
opentelemetry-sdk version the resolver picked; (once merely pinning
opentelemetry-sdk to fix that) an opentelemetry-instrumentation-fastapi version
too old for this app's FastAPI, which crashed every request with
`AttributeError: '_IncludedRouter' object has no attribute 'path'`; and
SQLAlchemyInstrumentor().instrument() reporting success while silently declining
to patch create_engine at all, because opentelemetry-instrumentation-sqlalchemy's
declared support caps below the SQLAlchemy 2.1 line this app requires (see
connection.py) — its default dependency-conflict handling logs a warning and
returns normally instead of raising, so instrument_sqlalchemy() had no way to
notice (fixed by switching to opentelemetry-instrumentation-dbapi, which patches
mssql_python.connect directly and has no SQLAlchemy version gate at all). In all
three cases the *setup call itself* reported success; only exercising the
actually-instrumented code path (a live request, or the patched attribute) ever
revealed the break. These cases set a real (fake-value) connection string and
check for real effects — a request flowing through the instrumented app, the
patched attribute actually being wrapped — so a regression that only breaks
under live traffic fails here instead of production.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.telemetry import configure_telemetry, instrument_fastapi, instrument_sqlalchemy

_FAKE_CONNECTION_STRING = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
    "IngestionEndpoint=https://example.invalid/"
)


@pytest.fixture
def telemetry_env(monkeypatch):
    """A fake-but-well-formed connection string — enables the real code path
    (distro configuration, FastAPI instrumentation) without ever reaching
    Azure: the exporter fails closed on export, which we don't exercise here.

    OTEL_METRICS_EXPORTER=none stops the SDK's background metrics-export
    thread from starting — otherwise it fires mid-test-run (or after, racing
    pytest's own teardown) trying to reach the fake endpoint, spamming
    unrelated "Retrying due to server request error" warnings. Traces/logs
    (what these tests actually exercise) are unaffected."""
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", _FAKE_CONNECTION_STRING)
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")


def test_configure_telemetry_succeeds(telemetry_env):
    # Regression guard for the azure-monitor-opentelemetry / opentelemetry-sdk
    # version-drift incident: this returned False (ImportError swallowed) when
    # the resolver picked an opentelemetry-sdk the exporter couldn't import.
    assert configure_telemetry() is True


def test_instrumented_app_serves_a_real_request(telemetry_env):
    # Regression guard for the second incident: configure_telemetry() and
    # instrument_fastapi() both reporting success is not sufficient — the
    # instrumentation patches Starlette's routing internals, and a version
    # mismatch there only surfaces once a request actually flows through them.
    configure_telemetry()
    app = FastAPI()
    assert instrument_fastapi(app) is True

    @app.get("/ping")
    def ping():
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_configure_telemetry_is_noop_without_connection_string(monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    assert configure_telemetry() is False


def test_instrument_sqlalchemy_patches_mssql_python_connect(telemetry_env):
    # Regression guard for the third incident: instrument_sqlalchemy() reporting
    # success was not sufficient evidence either — SQLAlchemyInstrumentor's
    # default dependency-conflict handling logged a warning and returned
    # normally instead of raising when the installed SQLAlchemy fell outside its
    # declared support range, so a silent no-op looked identical to success (see
    # git history for that incident). Now patches mssql_python.connect directly
    # via opentelemetry-instrumentation-dbapi, which has no SQLAlchemy version
    # gate — assert the return value agrees with the actual effect (the connect
    # attribute having been replaced), not just that a boolean came back True.
    import app.telemetry as telemetry_module
    import mssql_python

    original_connect = mssql_python.connect
    telemetry_module._sqlalchemy_instrumented = False
    try:
        result = instrument_sqlalchemy()
        actually_patched = mssql_python.connect is not original_connect
        assert result is True
        assert actually_patched is True
    finally:
        if telemetry_module._sqlalchemy_instrumented:
            from opentelemetry.instrumentation.dbapi import unwrap_connect

            unwrap_connect(mssql_python, "connect")
            telemetry_module._sqlalchemy_instrumented = False


def test_instrumented_mssql_connect_emits_a_span_nested_under_the_request(
    telemetry_env,
):
    # The patch existing is not enough on its own — confirm it actually emits a
    # span, and that the span nests under the request's trace (shared trace id,
    # parented to the request span), which is what makes AppDependencies useful
    # for "what did the database do during this specific request" rather than
    # an unrelated pile of spans with no correlation to anything.
    #
    # configure_telemetry() (via configure_azure_monitor) installs the global
    # TracerProvider itself, and OpenTelemetry only allows that once per
    # process — so this attaches an in-memory span processor to whatever
    # provider configure_telemetry() installed, rather than trying to
    # replace it, to actually capture the spans it emits.
    import sqlite3

    import app.telemetry as telemetry_module
    from opentelemetry import trace
    from opentelemetry.instrumentation.dbapi import trace_integration
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    configure_telemetry()

    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))

    # sqlite3 stands in for mssql_python here — both are DB-API 2.0 modules
    # with a module-level connect(), which is exactly the shape
    # instrument_sqlalchemy patches; this isolates the assertion (nesting
    # under a request span) from needing a reachable SQL Server.
    telemetry_module._sqlalchemy_instrumented = False
    trace_integration(sqlite3, "connect", "sqlite")

    app = FastAPI()
    instrument_fastapi(app)

    @app.get("/ping")
    def ping():
        # cursor(), not the Connection.execute() shorthand — sqlite3's shorthand
        # bypasses its own instrumented-cursor factory, and SQLAlchemy (what
        # this app actually uses in production, on top of mssql_python) always
        # goes through a real DB-API cursor, so this matches the real path.
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchall()
        return {"ok": True}

    TestClient(app).get("/ping")

    spans = {s.name: s for s in exporter.get_finished_spans()}
    request_span = spans["GET /ping"]
    db_span = spans["SELECT"]
    assert db_span.context.trace_id == request_span.context.trace_id
    assert db_span.parent is not None
