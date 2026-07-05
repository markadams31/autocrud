"""
Local auth mode (DB_AUTH_MODE=local) — the zero-Azure development path.

Three properties under test:

  Config gate      local mode demands credentials, and is refused outright on
                   App Service (WEBSITE_SITE_NAME set) — the guard that keeps a
                   misconfigured deployment from silently falling back to a
                   shared SQL login in production.

  Data path        get_user_db in local mode uses the shared local engine and
                   never reads (or requires) the EasyAuth token header; in
                   entra mode the 401-on-missing-token behaviour is unchanged.

  Identity         /me reports the SQL login in local mode so the UI shows who
                   writes are attributed to, and stays header-driven in entra
                   mode.
"""

import pytest
from pydantic import ValidationError

from app import config as config_module
from app import connection
from app.config import Settings
from app.errors import ApiError


BASE_ENV = {"db_server": "s", "db_database": "d", "db_schemas": ["dbo"]}


# ── Config gate ───────────────────────────────────────────────────────────────

def test_default_mode_is_entra():
    s = Settings(**BASE_ENV)
    assert s.db_auth_mode == "entra"


def test_local_mode_requires_credentials():
    with pytest.raises(ValidationError, match="DB_USERNAME and DB_PASSWORD"):
        Settings(**BASE_ENV, db_auth_mode="local")


def test_local_mode_accepts_credentials():
    s = Settings(**BASE_ENV, db_auth_mode="local", db_username="sa", db_password="x")
    assert s.db_auth_mode == "local"


def test_mode_is_normalised_and_validated():
    assert Settings(
        **BASE_ENV, db_auth_mode=" LOCAL ", db_username="sa", db_password="x"
    ).db_auth_mode == "local"
    with pytest.raises(ValidationError, match="entra, local"):
        Settings(**BASE_ENV, db_auth_mode="both")


def test_local_mode_is_refused_on_app_service(monkeypatch):
    # WEBSITE_SITE_NAME is set by App Service on every deployed app; its
    # presence must make local mode fail loudly at startup, not fall through.
    monkeypatch.setenv("WEBSITE_SITE_NAME", "autocrud-app-dev")
    with pytest.raises(ValidationError, match="refused on App Service"):
        Settings(**BASE_ENV, db_auth_mode="local", db_username="sa", db_password="x")


def test_entra_mode_is_fine_on_app_service(monkeypatch):
    monkeypatch.setenv("WEBSITE_SITE_NAME", "autocrud-app-dev")
    assert Settings(**BASE_ENV).db_auth_mode == "entra"


# ── Data path ─────────────────────────────────────────────────────────────────

class _FakeEngine:
    """Stands in for the local Engine: hands out a context-manager connection."""

    class _Conn:
        def begin(self):
            class _Tx:
                def __enter__(self, *a): return self
                def __exit__(self, *a): return False
            return _Tx()

        def close(self):
            pass

    def connect(self):
        return self._Conn()


def _request_without_token():
    from fastapi import Request
    return Request({"type": "http", "headers": [], "method": "GET", "path": "/"})


def test_get_user_db_local_mode_needs_no_token(monkeypatch):
    fake = _FakeEngine()
    monkeypatch.setattr(config_module, "DB_AUTH_MODE", "local")
    monkeypatch.setattr(connection, "_get_local_engine", lambda: fake)
    # No EasyAuth header on the request — local mode must not care.
    gen = connection.get_user_db(_request_without_token())
    conn = next(gen)
    assert isinstance(conn, _FakeEngine._Conn)
    gen.close()


def test_get_user_db_entra_mode_still_401s_without_token(monkeypatch):
    monkeypatch.setattr(config_module, "DB_AUTH_MODE", "entra")
    gen = connection.get_user_db(_request_without_token())
    with pytest.raises(ApiError) as exc:
        next(gen)
    assert exc.value.status_code == 401


def test_local_engine_is_built_once(monkeypatch):
    calls = []
    monkeypatch.setattr(connection, "_local_engine", None)
    monkeypatch.setattr(
        connection, "create_engine", lambda *a, **k: calls.append(1) or _FakeEngine()
    )
    e1 = connection._get_local_engine()
    e2 = connection._get_local_engine()
    assert e1 is e2
    assert calls == [1]
    monkeypatch.setattr(connection, "_local_engine", None)  # don't leak the fake


# ── Identity ──────────────────────────────────────────────────────────────────

def test_me_reports_the_sql_login_in_local_mode(monkeypatch, widget):
    monkeypatch.setattr(config_module, "DB_AUTH_MODE", "local")
    monkeypatch.setattr(config_module, "DB_USERNAME", "sa")
    resp = widget.client.get("/me")
    assert resp.json() == {"name": "sa (local)", "id": None, "authenticated": True}


def test_me_headers_still_win_in_local_mode(monkeypatch, widget):
    # A proxied identity header (if someone runs the dev auth proxy anyway)
    # takes precedence over the synthetic local identity.
    monkeypatch.setattr(config_module, "DB_AUTH_MODE", "local")
    resp = widget.client.get("/me", headers={"X-MS-CLIENT-PRINCIPAL-NAME": "real@user"})
    assert resp.json()["name"] == "real@user"


def test_me_is_unchanged_in_entra_mode(widget):
    resp = widget.client.get("/me")
    assert resp.json() == {"name": None, "id": None, "authenticated": False}
