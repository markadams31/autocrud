"""
dependencies.py — Shared FastAPI dependencies for all route modules.

Every route that needs the schema snapshot, a database connection, or a
resolved TableInfo imports from here. Keeping these in one place means:

  - No route implements "look up a table and 404 if missing" independently
  - The snapshot and connection dependencies have one definition each
  - If the resolution logic ever changes, there is one place to change it

The three dependencies
----------------------
get_snapshot    Read-only access to the current ReflectedSchema. Used by
                every meta and CRUD route. Returns the snapshot that was
                current when the request arrived; if a refresh happens mid-
                request the route simply finishes against the older snapshot.

get_db          The per-request OBO-authenticated database connection. Re-
                exported from connection.py so routes import from one place.
                Yields a SQLAlchemy Connection that is transaction-managed
                (committed on success, rolled back on exception).

get_table       Resolves a (schema, table) pair from URL path parameters
                into a TableInfo. Raises 404 cleanly via ApiError if the
                table is not in the current snapshot. Used as a sub-
                dependency by every CRUD and most meta routes.

Usage in a route
----------------
    from app.dependencies import get_snapshot, get_db, get_table
    from app.reflection import TableInfo, ReflectedSchema
    from sqlalchemy.engine import Connection

    @router.get("/{schema}/{table}")
    def some_route(
        table: TableInfo = Depends(get_table),
        db:    Connection = Depends(get_db),
    ):
        ...

get_table already depends on get_snapshot internally, so routes that use
get_table don't need to declare get_snapshot separately unless they also
need the full snapshot for cross-table lookups (e.g. the options endpoint
resolving a FK target table).
"""

from __future__ import annotations

from fastapi import Depends, Path

from app.connection import get_user_db as get_db
from app.errors import ApiError, ErrorCode
from app.reflection import ReflectedSchema, TableInfo
from app.state import get_snapshot as _get_snapshot_raw


# ---------------------------------------------------------------------------
# Snapshot dependency
# ---------------------------------------------------------------------------

def get_snapshot() -> ReflectedSchema:
    """
    Return the current schema snapshot.

    FastAPI calls this as a dependency; it is synchronous and cheap (just
    reads a module-level reference). Routes receive the ReflectedSchema
    that was current when their request began.
    """
    return _get_snapshot_raw()


# ---------------------------------------------------------------------------
# Table resolution dependency
# ---------------------------------------------------------------------------

def get_table(
    schema: str = Path(..., description="Schema name, e.g. 'dbo'"),
    table:  str = Path(..., description="Table name"),
    snapshot: ReflectedSchema = Depends(get_snapshot),
) -> TableInfo:
    """
    Resolve URL path parameters {schema} and {table} into a TableInfo.

    Raises a clean 404 ApiError if the combination is not in the current
    snapshot — either because the table doesn't exist, or because a schema
    refresh removed it. Routes declare this as a dependency and receive a
    fully-populated TableInfo with columns, PK, models, and display_column
    already computed.
    """
    info = snapshot.get(schema, table)
    if info is None:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            f"Table '{schema}.{table}' not found.",
        )
    return info


# ---------------------------------------------------------------------------
# Re-exports — routes import everything they need from this one module
# ---------------------------------------------------------------------------

__all__ = ["get_snapshot", "get_db", "get_table"]
