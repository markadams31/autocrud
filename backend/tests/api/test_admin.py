"""
POST /admin/refresh — re-reflect the schema and atomically swap the live
snapshot without a restart.

Reflection itself is mocked here (its real behaviour is covered by the
reflection unit tier and the integration tier); these tests verify the route's
own job: install the new snapshot, report the reflected tables grouped by
schema, and map a reflection failure to a safe INTERNAL_ERROR that leaks nothing.
"""

import pytest

from app import state
from app.routes import admin


@pytest.fixture(autouse=True)
def reset_refresh_throttle():
    """
    Clear the per-process refresh throttle before each test so independent tests
    that each POST /admin/refresh don't throttle one another (the throttle is a
    module global by design — see admin._reserve_refresh_slot).
    """
    admin.reset_refresh_throttle()
    yield
    admin.reset_refresh_throttle()


@pytest.fixture
def preserve_global_snapshot():
    """Restore whatever global snapshot was set, so refresh tests don't leak."""
    try:
        prev = state.get_snapshot()
    except RuntimeError:
        prev = None
    yield
    if prev is not None:
        state.set_snapshot(prev)


def test_refresh_swaps_snapshot_and_reports_tables(widget, monkeypatch, preserve_global_snapshot):
    # Reflection returns a known snapshot (the fixture's single dbo.Widget).
    monkeypatch.setattr(admin, "reflect_schemas", lambda: widget.snapshot)

    resp = widget.client.post("/admin/refresh")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["total"] == 1
    assert body["schemas"] == {"dbo": ["Widget"]}
    # The freshly reflected snapshot is now the live one.
    assert state.get_snapshot() is widget.snapshot


def test_refresh_failure_is_safe_internal_error(widget, monkeypatch, preserve_global_snapshot):
    def _boom():
        raise RuntimeError("reflection blew up: dsn=secret-connection-string")

    monkeypatch.setattr(admin, "reflect_schemas", _boom)

    resp = widget.client.post("/admin/refresh")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "INTERNAL_ERROR"
    # The underlying failure detail never reaches the client.
    assert "secret" not in body["message"]
    assert "blew up" not in body["message"]


def test_refresh_is_rate_limited(widget, monkeypatch, preserve_global_snapshot):
    calls = {"n": 0}

    def _count():
        calls["n"] += 1
        return widget.snapshot

    monkeypatch.setattr(admin, "reflect_schemas", _count)

    # First refresh runs; an immediate second is throttled with 429 and does NOT
    # re-run reflection (the reservation happens before the work).
    first = widget.client.post("/admin/refresh")
    assert first.status_code == 200, first.text

    second = widget.client.post("/admin/refresh")
    assert second.status_code == 429
    assert second.json()["code"] == "RATE_LIMITED"
    assert calls["n"] == 1

    # After the throttle is cleared, a refresh is allowed again.
    admin.reset_refresh_throttle()
    third = widget.client.post("/admin/refresh")
    assert third.status_code == 200, third.text
    assert calls["n"] == 2
