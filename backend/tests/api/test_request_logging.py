"""
The per-request access-log middleware (app.middleware.AccessLogMiddleware): a request id
on every response, an honoured inbound id, one access line per request, and
severity that tracks the response status.
"""

import logging

from app import middleware
from app.middleware import (
    RequestContextLogFilter,
    _level_for,
    display_user,
    install_request_id_log_factory,
    route_template,
)


def test_response_carries_a_generated_request_id(widget):
    resp = widget.client.post("/api/dbo/Widget/query", json={})
    assert resp.status_code == 200
    rid = resp.headers.get("X-Request-ID")
    assert rid and len(rid) >= 8


def test_inbound_request_id_is_echoed(widget):
    resp = widget.client.post(
        "/api/dbo/Widget/query", json={}, headers={"X-Request-ID": "trace-abc"}
    )
    assert resp.headers.get("X-Request-ID") == "trace-abc"


def test_logs_one_access_line_at_info(widget, caplog):
    with caplog.at_level(logging.INFO, logger="app.access"):
        widget.client.post("/api/dbo/Widget/query", json={})
    lines = [r for r in caplog.records if r.name == "app.access"]
    assert len(lines) == 1
    msg = lines[0].getMessage()
    assert "POST /api/dbo/Widget/query -> 200" in msg
    assert lines[0].levelno == logging.INFO


def test_client_error_logs_at_warning(widget, caplog):
    # An empty bulk-delete is a 400 — the access line should be a WARNING.
    with caplog.at_level(logging.INFO, logger="app.access"):
        resp = widget.client.post("/api/dbo/Widget/bulk-delete", json={"ids": []})
    assert resp.status_code == 400
    warnings = [
        r for r in caplog.records
        if r.name == "app.access" and r.levelno == logging.WARNING
    ]
    assert any("-> 400" in r.getMessage() for r in warnings)


def test_request_id_correlates_route_and_access_logs(widget, caplog):
    # Stamp captured records with the current request id, exactly as the
    # production logging config does (the same filter on its handler).
    caplog.handler.addFilter(RequestContextLogFilter())
    with caplog.at_level(logging.INFO):
        resp = widget.client.post("/api/dbo/Widget", json={"Name": "Correlated"})
    assert resp.status_code == 201
    rid = resp.headers["X-Request-ID"]
    assert rid and rid != "-"

    # The create's own route log and the access line both carry that one id —
    # so filtering logs by it shows everything the request did.
    crud = [r for r in caplog.records if r.name == "app.routes.crud"]
    access = [r for r in caplog.records if r.name == "app.access"]
    assert crud, "expected the route to log an INSERT line"
    assert access, "expected an access line"
    assert all(getattr(r, "request_id", None) == rid for r in crud + access)


def test_request_id_present_without_a_handler_filter():
    # Regression: the production format string references %(request_id)s. During
    # startup, third-party records reach the console handler before any filter is
    # attached; without the record factory the formatter raised and buried the
    # real logs under "Logging error" tracebacks. The factory must put
    # request_id on every record at creation, so formatting can't fail.
    install_request_id_log_factory()
    record = logging.getLogger("some.third.party").makeRecord(
        "some.third.party", logging.INFO, __file__, 0, "hello", None, None
    )
    assert hasattr(record, "request_id")
    fmt = logging.Formatter("%(request_id)s %(message)s")
    # Would raise ValueError("Formatting field not found ...") before the fix.
    assert "hello" in fmt.format(record)


def test_level_for_severity_and_noise():
    # Errors and client failures stand out; routine reads are INFO; the health
    # probe, hashed static assets, and FK-dropdown option lookups are demoted to
    # DEBUG to keep INFO (and the App Insights ingest) readable.
    assert _level_for(200, "/api/dbo/Widget/query") == logging.INFO
    assert _level_for(200, "/health") == logging.DEBUG
    assert _level_for(200, "/assets/index-abc.js") == logging.DEBUG
    assert _level_for(200, "/meta/dbo/Widget/options/OwnerID") == logging.DEBUG
    assert _level_for(404, "/api/dbo/Widget/9999") == logging.WARNING
    assert _level_for(500, "/api/dbo/Widget/query") == logging.ERROR
    # A failing health check is not "noise" — it surfaces at its status severity.
    assert _level_for(503, "/health") == logging.ERROR
    # An options lookup that errors is also not noise.
    assert _level_for(500, "/meta/dbo/Widget/options/OwnerID") == logging.ERROR


def test_route_template_collapses_ids():
    # The matched template, not the concrete path — so ids don't explode log /
    # metric cardinality. path_params is what Starlette stamps onto the scope.
    scope = {
        "path": "/api/ppm/Task/12",
        "path_params": {"schema": "ppm", "table": "Task", "pk": "12"},
    }
    assert route_template(scope) == "/api/{schema}/{table}/{pk}"
    # No params (an unrouted/static path) → unchanged.
    assert route_template({"path": "/health", "path_params": {}}) == "/health"


def test_display_user_policies(monkeypatch):
    # email (default): the address as-is; absent → "-".
    monkeypatch.setattr(middleware, "LOG_USER_IDENTITY", "email")
    assert display_user("ada@example.com") == "ada@example.com"
    assert display_user(None) == "-"
    # none: never logged.
    monkeypatch.setattr(middleware, "LOG_USER_IDENTITY", "none")
    assert display_user("ada@example.com") == "-"
    # hash: stable pseudonym, not the address, and the same input maps the same.
    monkeypatch.setattr(middleware, "LOG_USER_IDENTITY", "hash")
    h1 = display_user("ada@example.com")
    h2 = display_user("ada@example.com")
    assert h1 == h2
    assert "ada@example.com" not in h1 and h1.startswith("u_")
    assert display_user("bob@example.com") != h1
