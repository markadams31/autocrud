"""
Browser-facing HTTP hardening (app.security): security headers + CSP on every
response, the CSP exemption for the API-docs pages, gzip compression of large
responses, and Cache-Control on served static files.
"""

import base64
import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app
from app.security import CachingStaticFiles, build_csp

client = TestClient(app)


# ---------------------------------------------------------------------------
# Security headers + CSP on real responses
# ---------------------------------------------------------------------------

def test_security_headers_present_on_every_response():
    # /openapi.json needs no database, but still passes through the middleware
    # stack — the headers must be on it (and on the JSON API) too, not just HTML.
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert resp.headers["cross-origin-opener-policy"] == "same-origin"
    assert "default-src 'self'" in resp.headers["content-security-policy"]


def test_csp_exempts_docs_pages_but_keeps_other_headers():
    # Swagger UI loads from a CDN and runs inline scripts; the strict CSP would
    # break it, so the docs route is exempt — while the cheaper hardening stays.
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "content-security-policy" not in resp.headers
    assert resp.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

def test_large_response_is_gzipped_when_accepted():
    resp = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    # Caches must key on the encoding so a gzipped body isn't served to a client
    # that didn't ask for it.
    assert "accept-encoding" in resp.headers.get("vary", "").lower()


def test_response_not_gzipped_without_accept_encoding():
    resp = client.get("/openapi.json", headers={"Accept-Encoding": "identity"})
    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers


# ---------------------------------------------------------------------------
# CSP construction
# ---------------------------------------------------------------------------

def test_build_csp_pins_inline_scripts_by_hash(tmp_path):
    inline = b"\n  (function(){ document.title = 'x' })()\n"
    expected = "'sha256-" + base64.b64encode(hashlib.sha256(inline).digest()).decode() + "'"
    (tmp_path / "index.html").write_bytes(
        b"<head><script>" + inline + b"</script>"
        b"<script type=\"module\" src=\"/assets/index-abc.js\"></script></head>"
    )

    csp = build_csp(str(tmp_path))

    # The inline script is allowed by its hash; the src= bundle is not hashed
    # (it's covered by 'self'), so exactly one hash appears.
    assert expected in csp
    assert csp.count("sha256-") == 1
    assert "script-src 'self' " in csp


def test_build_csp_without_a_frontend_has_no_script_hashes():
    csp = build_csp(None)
    assert "sha256-" not in csp
    assert "script-src 'self'" in csp
    # 'unsafe-inline' is allowed for styles (toolkit injects them) but never scripts.
    assert "'unsafe-inline'" in csp.split("style-src")[1].split(";")[0]
    assert "'unsafe-inline'" not in csp.split("script-src")[1]


# ---------------------------------------------------------------------------
# Static-asset caching
# ---------------------------------------------------------------------------

@pytest.fixture
def spa_client(tmp_path):
    """A bare app serving CachingStaticFiles from a faux Vite build directory."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-BjVZy8k2.js").write_text("console.log(1)")
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "favicon.svg").write_text("<svg/>")

    spa = FastAPI()
    spa.mount("/", CachingStaticFiles(directory=str(tmp_path), html=True), name="static")
    return TestClient(spa)


def test_hashed_assets_are_cached_immutably(spa_client):
    resp = spa_client.get("/assets/index-BjVZy8k2.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_index_html_is_never_cached_hard(spa_client):
    # Served at "/" via the SPA fallback; must revalidate so new deploys' asset
    # URLs are picked up.
    resp = spa_client.get("/")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"


def test_other_static_files_get_a_modest_cache(spa_client):
    resp = spa_client.get("/favicon.svg")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=3600"
