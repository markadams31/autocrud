"""
connection.py — Database identity and connection management.

Two identities, two purposes:

  Managed identity (app)   Used ONLY for schema reflection. Reads metadata,
                           never data. Pooled engine, fixed shared identity;
                           a do_connect listener injects a token whenever
                           the pool opens a new physical connection.

  Signed-in user (OBO)     Used for ALL data access. Each user's token gets a
                           small, pooled Engine, cached by token, so repeated
                           requests presenting the same token reuse a
                           connection instead of paying a fresh TLS+ODBC login
                           every time. See "Per-token engine cache" below.

Connection acquisition is centralised in exactly two public surfaces:

  reflection_engine        The shared managed-identity engine. The reflection
                           layer acquires connections from it through the shared
                           transient-fault retry (_connect_with_retry), so a
                           cold-start drop doesn't abort startup.

  get_user_db()            The per-request, OBO-authenticated FastAPI dependency.
                           Every CRUD/query route depends on this and nothing
                           else for its connection.

Per-token engine cache
----------------------
Each pooled Engine is cached under the user's access token. An Entra access
token is bound to one user, so the token alone is a sufficient identity key:
a pool only ever serves requests presenting the exact token it was built
under, so a connection opened for one token can never be checked out under
another. The isolation is structural, not incidental.

A token refresh produces a new token, hence a new cache key; the old Engine
simply stops being looked up and is reclaimed (see TTL below). Nothing in
this module needs to know how long Entra tokens live.

The TTL is not a correctness mechanism — isolation comes from the key, not
the TTL. It IS load-bearing for resource usage, though: because tokens rotate
roughly hourly, each active user produces a steady stream of short-lived
engines, and the TTL governs how quickly the abandoned ones (and the Azure
SQL connections they hold) are reclaimed. It is set comfortably longer than a
typical token lifetime so an in-use engine is never evicted mid-request, but
short enough that dead engines don't pile up.

A pooled connection can still go stale if its token expires server-side while
idle. `pool_pre_ping=True` catches that on next checkout and opens a fresh one
instead of surfacing a confusing query failure.
"""

import base64
import json
import struct
import logging
import threading
import time
from typing import Iterator, Optional

import pyodbc
from cachetools import TTLCache
from azure.identity import DefaultAzureCredential
from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import OperationalError, InterfaceError, ResourceClosedError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.auth_headers import USER_ACCESS_TOKEN
from app.config import DB_SERVER, DB_DATABASE, DB_DRIVER
from app.errors import ApiError, ErrorCode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_DSN = f"Driver={{{DB_DRIVER}}};Server={DB_SERVER};Database={DB_DATABASE};Encrypt=yes;"
_SQLALCHEMY_URL = "mssql+pyodbc:///?odbc_connect=" + _DSN


# ---------------------------------------------------------------------------
# Token plumbing
#
# Azure SQL accepts an OAuth bearer token through a special ODBC connection
# attribute instead of a username/password. The token must be packed as its
# UTF-16-LE bytes, prefixed with a 4-byte little-endian length.
# ---------------------------------------------------------------------------

SQL_COPT_SS_ACCESS_TOKEN = 1256  # ODBC attribute id for the access token
AZURE_SQL_SCOPE          = "https://database.windows.net/.default"


def _pack_token(token: str) -> bytes:
    """Pack an access token into the byte structure the ODBC driver expects."""
    raw = token.encode("utf-16-le")
    return struct.pack(f"<I{len(raw)}s", len(raw), raw)


# ---------------------------------------------------------------------------
# Reflection engine — managed identity, pooled under a single shared identity.
# ---------------------------------------------------------------------------

_credential = DefaultAzureCredential()

reflection_engine = create_engine(
    _SQLALCHEMY_URL,
    pool_pre_ping=True,
    pool_recycle=1500,
)


@event.listens_for(reflection_engine, "do_connect")
def _inject_managed_identity_token(dialect, conn_rec, cargs, cparams):
    token_obj = _credential.get_token(AZURE_SQL_SCOPE)
    cparams["attrs_before"] = {SQL_COPT_SS_ACCESS_TOKEN: _pack_token(token_obj.token)}


# ---------------------------------------------------------------------------
# User token (data access)
#
# The signed-in user's access token arrives in an EasyAuth-injected header
# (USER_ACCESS_TOKEN, defined in app.auth_headers alongside the identity
# headers). It is the only authentication signal the app needs: it authenticates
# the database connection as the real user, and SQL Server enforces that user's
# grants on every statement. (The user's display name, used only for log
# context, lives in a separate header read in the route layer.)
# ---------------------------------------------------------------------------

# Treat a token as expired this many seconds before its real expiry, so a
# request that would race the expiry boundary refreshes instead of failing
# mid-flight at the database.
_TOKEN_EXPIRY_SKEW_SECONDS = 60


def _token_expiry(token: str) -> Optional[float]:
    """
    Best-effort read of an access token's `exp` claim (epoch seconds).

    The token is a JWT; we decode the payload segment WITHOUT verifying the
    signature — SQL Server remains the authority that validates the token. We
    only want to know whether it's already expired so we can pre-empt a confusing
    database login failure with a clean 401. Done with the standard library: no
    dependency, and no clash with the unrelated 'jwt' PyPI package (which shadows
    PyJWT). Returns None for anything we can't decode, so the caller falls back
    to letting the database decide.
    """
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore base64url padding
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = claims.get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


def _token_is_expired(token: str) -> bool:
    """True if the token's exp is in the past (within the refresh skew)."""
    exp = _token_expiry(token)
    if exp is None:
        return False  # can't tell — let SQL Server be the authority
    return time.time() >= exp - _TOKEN_EXPIRY_SKEW_SECONDS


def _user_token_from_request(request: Request) -> str:
    """
    Return the signed-in user's access token from the EasyAuth header.

    Raises UNAUTHENTICATED if the header is absent (not signed in) OR if the
    token is already expired. The expiry case matters: EasyAuth keeps injecting
    the *stale* token after it expires rather than refreshing automatically, so
    without this check the app would forward a dead token to Azure SQL and the
    user would see a confusing database error instead of a clean "session
    expired". Surfacing 401 lets the frontend refresh the session (/.auth/refresh)
    and replay the request transparently.
    """
    token = request.headers.get(USER_ACCESS_TOKEN)
    if not token:
        # No EasyAuth token reached us — not authenticated. Raised as an
        # ApiError so it returns the standard error shape, like everything else.
        raise ApiError(ErrorCode.UNAUTHENTICATED)
    if _token_is_expired(token):
        raise ApiError(
            ErrorCode.UNAUTHENTICATED,
            "Your session has expired. Please sign in again.",
        )
    return token


# ---------------------------------------------------------------------------
# Per-token engine cache
#
# A cached Engine owns a connection pool and underlying DBAPI connections.
# Letting an entry fall out of the cache would leave those resources open
# until garbage collection, so eviction must explicitly call Engine.dispose().
#
# cachetools evicts entries via two different paths:
#   - Size-based eviction (maxsize exceeded): popitem() is called.
#   - TTL expiry: popitem() is not called; expire() must be invoked
#     explicitly and returns the expired entries.
#
# Both paths are handled below. _engine_for() performs the expire() sweep on
# each access, piggybacking cleanup onto normal cache activity rather than
# running a separate background task.
#
# TTL is set comfortably longer than a typical Entra access-token lifetime
# (~60-90 min) so an engine is never evicted while its token is still valid
# and in use, but short enough that the engine abandoned at each token rotation
# is reclaimed within a couple of hours rather than lingering for most of a day.
# ---------------------------------------------------------------------------

_ENGINE_CACHE_TTL_SECONDS = 2 * 60 * 60


class _DisposingEngineCache(TTLCache):
    def popitem(self):
        key, engine = super().popitem()
        engine.dispose()
        return key, engine


_engine_cache = _DisposingEngineCache(maxsize=1024, ttl=_ENGINE_CACHE_TTL_SECONDS)
_engine_cache_lock = threading.Lock()


def _build_user_engine(token: str) -> Engine:
    """
    Build a small pooled Engine bound to a single access token.

    The pool is intentionally small because requests are expected to originate
    from an individual interactive user rather than a fan-out service. The
    creator closure captures the token by value; this is safe because each
    Engine is created specifically for one cache entry and never shared across
    tokens.
    """
    def _connect():
        return pyodbc.connect(
            _DSN, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _pack_token(token)}
        )

    return create_engine(
        "mssql+pyodbc://",
        creator=_connect,
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=2,
        pool_pre_ping=True,  # catches a token gone stale while idle
    )


def _engine_for(token: str) -> Engine:
    """
    Return the pooled Engine for this exact token, creating it on first use.

    The lock covers both the expire() sweep and the get-or-build-and-store
    sequence, ensuring that concurrent first requests for the same token
    cannot create multiple Engines and that cache mutation remains serialised.

    Engine creation is local and synchronous, so the lock is held only
    briefly. It never extends to checking out a database connection (that
    occurs in get_user_db(), outside this lock). Network I/O must never occur
    while this lock is held.
    """
    with _engine_cache_lock:
        engine = _engine_cache.get(token)
        if engine is None:
            engine = _build_user_engine(token)
            _engine_cache[token] = engine
        for _, expired_engine in _engine_cache.expire():
            expired_engine.dispose()
        return engine


def dispose_user_engines() -> None:
    """
    Dispose every cached user engine. Called from the application shutdown
    hook so connection pools are closed deterministically rather than at
    interpreter teardown.
    """
    with _engine_cache_lock:
        for _, engine in list(_engine_cache.items()):
            engine.dispose()
        _engine_cache.clear()


# ---------------------------------------------------------------------------
# Transient-fault retry on connection acquisition
#
# Azure SQL throttles, fails over, and scales — which shows up as a connection
# that can't be opened right now but will be fine in a moment. We retry ONLY
# the acquisition of a connection, before any statement has run, so there is
# nothing half-applied to reason about. Statement and transaction failures are
# deliberately NOT retried: a write may have taken effect before the error
# surfaced, and replaying it blindly is unsafe. Those still surface as
# DATABASE_UNAVAILABLE through the normal handler.
#
# Detection is by exception class, not SQL error number — consistent with
# errors.py. OperationalError is the usual transient case (throttle / failover /
# timeout). InterfaceError and ResourceClosedError are also retried because a
# fault that strikes while a brand-new engine runs its first-connect dialect
# initialization (SQLAlchemy probes the server version) is torn down and
# re-surfaced as one of those — the original OperationalError gets masked. That
# is exactly the shape a serverless database resuming from auto-pause produces,
# so retrying only OperationalError lets a cold-start fault escape the retry and
# abort the connect. Retrying any of them is safe: only acquisition is wrapped,
# so no statement has run. A truly permanent failure just exhausts the handful of
# attempts and then surfaces normally.
# ---------------------------------------------------------------------------

_CONNECT_RETRIES   = 3      # retries after the first try → up to 4 attempts
_CONNECT_BACKOFF_S = 0.25   # base delay; doubles each retry, plus small jitter

# Transient faults worth retrying at connection-acquisition time (see above).
_RETRYABLE_CONNECT_ERRORS = (OperationalError, InterfaceError, ResourceClosedError)


@retry(
    # Only acquisition is retried, and only on the transient classes. reraise=True
    # surfaces the original exception (not tenacity's RetryError) so the error
    # handler maps it to DATABASE_UNAVAILABLE.
    retry=retry_if_exception_type(_RETRYABLE_CONNECT_ERRORS),
    stop=stop_after_attempt(_CONNECT_RETRIES + 1),
    wait=wait_exponential_jitter(initial=_CONNECT_BACKOFF_S, jitter=0.1),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _connect_with_retry(engine: Engine) -> Connection:
    return engine.connect()


# ---------------------------------------------------------------------------
# User database connection acquisition
# ---------------------------------------------------------------------------

def get_user_db(request: Request) -> Iterator[Connection]:
    """
    Yield an OBO-authenticated connection for the signed-in user.

    Connection acquisition is retried on transient faults (see
    _connect_with_retry); the per-request transaction is not — it commits on
    success and rolls back if the route raises.
    """
    token  = _user_token_from_request(request)
    engine = _engine_for(token)

    conn = _connect_with_retry(engine)
    try:
        with conn.begin():
            yield conn
    finally:
        conn.close()
