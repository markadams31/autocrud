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
notice. In all three cases the *setup call itself* reported success; only
exercising the actually-instrumented code path (a live request, or the patched
attribute) ever revealed the break. These cases set a real (fake-value)
connection string and check for real effects — a request flowing through the
instrumented app, the create_engine attribute actually being wrapped — so a
regression that only breaks under live traffic fails here instead of production.
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


def test_instrument_sqlalchemy_return_value_matches_whether_it_actually_patched(
    telemetry_env,
):
    # Regression guard for the third incident: instrument_sqlalchemy() returning
    # True was not sufficient evidence either — SQLAlchemyInstrumentor's default
    # dependency-conflict handling logs a warning and returns normally instead of
    # raising when the installed SQLAlchemy falls outside its declared support
    # range (true here: opentelemetry-instrumentation-sqlalchemy caps at
    # SQLAlchemy <2.1, this app requires the 2.1 line — see connection.py), so a
    # silent no-op looked identical to success. Assert the return value agrees
    # with the actual effect (sqlalchemy.create_engine having been replaced),
    # not that the effect happened — currently there IS no compatible release,
    # so a "yes it worked" result here would itself be the regression.
    import app.telemetry as telemetry_module
    import sqlalchemy

    original_create_engine = sqlalchemy.create_engine
    telemetry_module._sqlalchemy_instrumented = False
    try:
        result = instrument_sqlalchemy()
        actually_patched = sqlalchemy.create_engine is not original_create_engine
        assert result is actually_patched
    finally:
        if telemetry_module._sqlalchemy_instrumented:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

            SQLAlchemyInstrumentor().uninstrument()
            telemetry_module._sqlalchemy_instrumented = False
