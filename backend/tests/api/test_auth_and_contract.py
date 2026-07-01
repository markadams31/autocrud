"""
Auth gating, malformed-request handling, the error-response shape, and the
liveness endpoint.
"""

import base64
import json
import logging
import time

from fastapi.testclient import TestClient

from app import state
from app.dependencies import get_db


def _jwt(exp) -> str:
    def seg(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{seg({'alg': 'none'})}.{seg({'exp': exp})}.sig"


def test_missing_easyauth_token_is_401(snapshot_only):
    # No X-MS-TOKEN-AAD-ACCESS-TOKEN header → unauthenticated, before any DB work.
    resp = snapshot_only.client.get("/api/dbo/Widget/1")
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHENTICATED"


def test_query_without_token_is_401(snapshot_only):
    resp = snapshot_only.client.post("/api/dbo/Widget/query", json={})
    assert resp.status_code == 401


def test_expired_token_is_401_before_db_work(snapshot_only):
    # An expired EasyAuth token surfaces as a clean 401 (not a DB error), so the
    # frontend can refresh the session and replay the request.
    resp = snapshot_only.client.get(
        "/api/dbo/Widget/1",
        headers={"X-MS-TOKEN-AAD-ACCESS-TOKEN": _jwt(time.time() - 60)},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHENTICATED"


def test_single_column_pk_takes_the_value_whole(widget):
    # A single-column PK is never split on commas (so a natural key like "A,B"
    # addresses correctly — see QA-3 / _pk_filter). Widget's PK is an int, so
    # "1,2" is simply a value matching no row → 404, not a synthetic "arity" 400.
    # (Composite-PK arity is validated in tests/unit/test_query_safety.py.)
    resp = widget.client.get("/api/dbo/Widget/1,2")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_error_response_shape(widget):
    body = widget.client.get("/api/dbo/Widget/99999").json()
    assert set(body) >= {"code", "message"}
    assert isinstance(body["code"], str)
    assert isinstance(body["message"], str)


def test_health_ok_when_snapshot_loaded(widget):
    # /health reads the global snapshot directly (not via dependency override).
    state.set_snapshot(widget.snapshot)
    resp = widget.client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["tables"] == 1
    # Echoes the build SHA so the deploy smoke check can confirm the new container
    # is serving the SHA it shipped ("dev" outside a CI-built image).
    assert isinstance(body["sha"], str) and body["sha"]


def test_health_503_when_database_unreachable(widget):
    # Readiness, not just liveness: a loaded snapshot but an unreachable DB must
    # report unhealthy so App Service stops routing to an instance that can't serve.
    from app.routes.admin import database_is_reachable

    state.set_snapshot(widget.snapshot)
    widget.client.app.dependency_overrides[database_is_reachable] = lambda: False
    resp = widget.client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["code"] == "DATABASE_UNAVAILABLE"


def test_config_returns_app_insights_connection_string(snapshot_only):
    # /config feeds the frontend telemetry SDK. No snapshot/DB needed. In tests
    # no connection string is configured, so it's null — the frontend then no-ops.
    resp = snapshot_only.client.get("/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "applicationInsights" in body
    assert body["applicationInsights"]["connectionString"] is None


def test_version_reports_build_info(snapshot_only):
    # /version is static process info — no snapshot or DB needed — so the About
    # dialog can show which build is running even during startup. Shape only: the
    # values are "dev" outside a CI-built image but a real SHA/time in one.
    resp = snapshot_only.client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"sha", "time"}
    assert isinstance(body["sha"], str) and body["sha"]
    assert isinstance(body["time"], str) and body["time"]


def test_malformed_request_body_is_422_validation_error(widget):
    # A non-int page fails FastAPI's own request-model validation, which the
    # RequestValidationError handler turns into the standard VALIDATION_ERROR
    # shape with per-field detail.
    resp = widget.client.post("/api/dbo/Widget/query", json={"page": "not-an-int"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "page" in body["fields"]


def test_unhandled_exception_returns_a_safe_500(widget, caplog):
    # An error that isn't an ApiError or a mapped DB error must still come back
    # as the generic INTERNAL_ERROR contract — never a stack trace or raw text —
    # and be logged at ERROR by the access middleware.
    def _boom():
        raise RuntimeError("kaboom: secret connection string")

    widget.client.app.dependency_overrides[get_db] = _boom
    # raise_server_exceptions=False so we observe the 500 *response* the handler
    # produces, as a real client would, rather than the re-raised exception.
    client = TestClient(widget.client.app, raise_server_exceptions=False)
    with caplog.at_level(logging.INFO, logger="app.access"):
        resp = client.get("/api/dbo/Widget/1")

    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert "kaboom" not in body["message"]       # the raw error never leaks
    assert "secret" not in body["message"]
    errors = [
        r for r in caplog.records
        if r.name == "app.access" and r.levelno == logging.ERROR
    ]
    assert any("-> 500" in r.getMessage() for r in errors)
