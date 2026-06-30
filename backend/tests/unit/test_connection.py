"""
Connection plumbing that can be tested without a database: access-token
packing, the EasyAuth token header extraction, and the disposing engine cache
(resource cleanup is the whole point of the cache, so it's worth proving).
"""

import base64
import json
import struct
import time
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError, ResourceClosedError

from app.auth_headers import USER_ACCESS_TOKEN
from app.connection import (
    _CONNECT_RETRIES,
    _DisposingEngineCache,
    _connect_with_retry,
    _pack_token,
    _token_expiry,
    _token_is_expired,
    _user_token_from_request,
    dispose_user_engines,
)
from app.errors import ApiError, ErrorCode


def _jwt(exp) -> str:
    """A minimal unsigned JWT carrying just an `exp` claim, for expiry tests."""
    def seg(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{seg({'alg': 'none'})}.{seg({'exp': exp})}.sig"


# ── Token packing ────────────────────────────────────────────────────────────

def test_pack_token_layout():
    token = "header.payload.signature"
    packed = _pack_token(token)
    raw = token.encode("utf-16-le")
    # 4-byte little-endian length prefix, then the UTF-16-LE bytes.
    assert struct.unpack("<I", packed[:4])[0] == len(raw)
    assert packed[4:] == raw


def test_pack_token_handles_unicode():
    packed = _pack_token("café")
    assert packed[4:] == "café".encode("utf-16-le")


# ── EasyAuth token header ────────────────────────────────────────────────────

def test_token_extracted_from_header():
    req = SimpleNamespace(headers={USER_ACCESS_TOKEN: "the-token"})
    assert _user_token_from_request(req) == "the-token"


def test_missing_token_raises_unauthenticated():
    req = SimpleNamespace(headers={})
    with pytest.raises(ApiError) as ei:
        _user_token_from_request(req)
    assert ei.value.code == ErrorCode.UNAUTHENTICATED


# ── Token expiry ─────────────────────────────────────────────────────────────

def test_token_expiry_reads_exp_claim():
    assert _token_expiry(_jwt(1_700_000_000)) == 1_700_000_000.0


def test_token_expiry_is_none_for_non_jwt():
    # A non-decodable token can't be judged — caller falls back to the database.
    assert _token_expiry("opaque-not-a-jwt") is None


def test_fresh_token_is_not_expired():
    assert _token_is_expired(_jwt(time.time() + 3600)) is False


def test_past_exp_is_expired():
    assert _token_is_expired(_jwt(time.time() - 10)) is True


def test_expiry_skew_treats_near_expiry_as_expired():
    # Within the refresh skew (60s) counts as expired so we refresh proactively.
    assert _token_is_expired(_jwt(time.time() + 30)) is True


def test_undecodable_token_is_not_treated_as_expired():
    assert _token_is_expired("opaque-not-a-jwt") is False


def test_expired_token_header_raises_unauthenticated():
    req = SimpleNamespace(headers={USER_ACCESS_TOKEN: _jwt(time.time() - 60)})
    with pytest.raises(ApiError) as ei:
        _user_token_from_request(req)
    assert ei.value.code == ErrorCode.UNAUTHENTICATED


def test_fresh_token_header_is_returned():
    fresh = _jwt(time.time() + 3600)
    req = SimpleNamespace(headers={USER_ACCESS_TOKEN: fresh})
    assert _user_token_from_request(req) == fresh


# ── Disposing engine cache ───────────────────────────────────────────────────

class _FakeEngine:
    def __init__(self):
        self.disposed = False

    def dispose(self):
        self.disposed = True


def test_size_eviction_disposes_engine():
    cache = _DisposingEngineCache(maxsize=1, ttl=10_000)
    first, second = _FakeEngine(), _FakeEngine()
    cache["token-a"] = first
    cache["token-b"] = second          # over maxsize → evicts token-a
    assert first.disposed is True
    assert "token-a" not in cache
    assert cache["token-b"] is second


def test_dispose_user_engines_disposes_and_clears():
    from app import connection

    a, b = _FakeEngine(), _FakeEngine()
    connection._engine_cache["t1"] = a
    connection._engine_cache["t2"] = b

    dispose_user_engines()

    assert a.disposed and b.disposed
    assert len(connection._engine_cache) == 0


# ── Connection-acquisition retry ─────────────────────────────────────────────
# The retry wraps ONLY engine.connect() (no statement has run), so retrying is
# safe. It must cover ResourceClosedError: a transient fault during a fresh
# engine's first-connect dialect init re-surfaces the masked OperationalError as
# that, which is exactly what a serverless database resuming from auto-pause
# produces. Retrying only OperationalError would let that abort the connect.

class _FlakyEngine:
    """An engine whose connect() raises `error` on the first `fail_times` calls."""
    def __init__(self, error, fail_times, result=None):
        self._error = error
        self._fail_times = fail_times
        self._result = result
        self.calls = 0

    def connect(self):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._error
        return self._result


@pytest.fixture
def no_retry_sleep(monkeypatch):
    """Skip the real backoff sleep so retry tests run instantly."""
    monkeypatch.setattr(_connect_with_retry.retry, "sleep", lambda *_a, **_k: None)


def test_connect_retry_recovers_from_resource_closed_error(no_retry_sleep):
    sentinel = object()
    engine = _FlakyEngine(ResourceClosedError("This Connection is closed"), fail_times=2, result=sentinel)
    assert _connect_with_retry(engine) is sentinel
    assert engine.calls == 3   # two failures retried, third succeeds


def test_connect_retry_recovers_from_operational_error(no_retry_sleep):
    sentinel = object()
    engine = _FlakyEngine(OperationalError("SELECT 1", {}, Exception("transient")), fail_times=1, result=sentinel)
    assert _connect_with_retry(engine) is sentinel
    assert engine.calls == 2


def test_connect_retry_exhausts_and_reraises_original(no_retry_sleep):
    err = ResourceClosedError("This Connection is closed")
    engine = _FlakyEngine(err, fail_times=99)
    with pytest.raises(ResourceClosedError):
        _connect_with_retry(engine)
    assert engine.calls == _CONNECT_RETRIES + 1   # all attempts used, then reraise


def test_connect_retry_does_not_retry_unexpected_error(no_retry_sleep):
    # A non-transient error surfaces immediately — no wasted attempts.
    engine = _FlakyEngine(ValueError("boom"), fail_times=99)
    with pytest.raises(ValueError):
        _connect_with_retry(engine)
    assert engine.calls == 1


def test_reflection_acquires_connection_through_retry(monkeypatch):
    # Startup reflection must go through the retry, not reflection_engine.connect()
    # directly, so a cold-start drop doesn't abort startup. Guards against a
    # regression to a bare .connect().
    import app.reflection as reflection

    seen = {}
    def _fake(engine):
        seen["engine"] = engine
        raise RuntimeError("stop before real reflection")

    monkeypatch.setattr(reflection, "_connect_with_retry", _fake)
    with pytest.raises(RuntimeError, match="stop before real reflection"):
        reflection.reflect_schemas()
    assert seen["engine"] is reflection.reflection_engine
