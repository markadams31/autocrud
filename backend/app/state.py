"""
state.py — The application's single piece of mutable global state: the
current schema snapshot.

The problem it solves
---------------------
reflect_schemas() returns an immutable SchemaSnapshot describing every
table the app can see. The rest of the application needs to read from that
snapshot on every request — but it also needs to be replaceable at runtime
when an admin hits /admin/refresh after a schema change, without restarting
the process.

So we need exactly one mutable reference ("which snapshot is current?")
that everything else reads through. That reference lives here, in one place,
rather than as a free-floating module global in main.py.

Concurrency
-----------
Refresh replaces the reference with a brand-new snapshot; it never mutates
the existing one. Reads take the current reference and use it for the whole
request. In CPython, rebinding a module-level name is atomic under the GIL,
so a reader either sees the old snapshot or the new one — never a
half-updated structure. A request that picked up the old snapshot finishes
against it consistently; the next request sees the new one. This is the
accepted, deliberately lock-free model: a refresh is rare and a one-request
overlap is harmless.

Usage
-----
    from app.state import set_snapshot, get_snapshot
    set_snapshot(reflect_schemas())   # startup and on refresh
    snapshot = get_snapshot()         # per request (via dependency)
"""

from __future__ import annotations

import logging

from app.reflection import SchemaSnapshot

logger = logging.getLogger(__name__)


# The one mutable reference. None until the first successful reflection at
# startup. _current is module-private; all access goes through the two
# functions below so the "atomic rebind" contract lives in exactly one place.
_current: SchemaSnapshot | None = None


def set_snapshot(snapshot: SchemaSnapshot) -> None:
    """
    Install a new snapshot as the current one.

    Called once at startup (from the lifespan handler) and again on each
    successful /admin/refresh. The replacement is a single name rebind —
    atomic under the GIL — so in-flight readers are unaffected.
    """
    global _current
    table_count = len(snapshot.tables)
    _current = snapshot
    logger.info("Schema snapshot installed: %d table(s)", table_count)


def get_snapshot() -> SchemaSnapshot:
    """
    Return the current snapshot.

    Raises RuntimeError if called before startup reflection has populated
    it — that would be a programming error (a request reaching a route
    before the lifespan handler ran), not a client problem, so it should
    surface loudly rather than as a handled API error.
    """
    if _current is None:
        raise RuntimeError(
            "Schema snapshot requested before initialisation. "
            "reflect_schemas() must run in the startup lifespan before any "
            "request is served."
        )
    return _current
