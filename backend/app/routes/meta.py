"""
routes/meta.py — Metadata endpoints.

Four endpoints, each serving a distinct frontend need:

  GET /meta
      Lists the reflected schemas and the connected database name. The frontend
      calls this once on load to discover what schemas exist and to label the UI
      with the database it is connected to, before drilling into each schema.
      Served entirely from the in-memory snapshot — no database query.

  GET /meta/{schema}
      Lists every table in the schema that the signed-in user can access,
      with per-table permission flags. This is what populates the sidebar.
      Runs a single HAS_PERMS_BY_NAME query against the user's OBO
      connection — SQL Server resolves permissions for the actual signed-in
      identity, so the result is always accurate and never stale. Only
      tables present in the current snapshot AND accessible to the user are
      returned (the intersection).

  GET /meta/{schema}/{table}
      Full column-level metadata for one table: everything the frontend
      needs to render a form, a list view, and filter controls. Fetched
      once per table navigation and cached by the frontend for the session.
      No database query — served entirely from the in-memory snapshot.

  GET /meta/{schema}/{table}/options/{column}
      Value/label pairs for a FK column, used to populate dropdowns. The
      target table and its display column are resolved from the snapshot;
      the actual rows are fetched via the user's OBO connection. Fails
      gracefully to an empty list if the user lacks SELECT on the target
      table — a dropdown simply shows no options rather than erroring.
"""

from __future__ import annotations

import datetime as _dt
import logging
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.engine import Connection

from app import config
from app.dependencies import get_db, get_snapshot, get_table
from app.errors import ApiError, ErrorCode
from app.reflection import ColumnInfo, ReflectedSchema, TableInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meta", tags=["meta"])


# ---------------------------------------------------------------------------
# Frontend-friendly type names
#
# ColumnInfo.python_type is a Python type object — not directly useful to a
# React form renderer. This map translates it to a stable string the
# frontend can switch on to decide which input component to render.
# ---------------------------------------------------------------------------

_FIELD_TYPE: dict[Any, str] = {
    str:      "text",
    int:      "integer",
    float:    "number",
    Decimal:  "decimal",    # Surfaces as string in JSON; frontend treats as text input
    bool:     "boolean",
    "date":   "date",
    "datetime": "datetime",
    "time":   "time",
}

def _field_type(col: ColumnInfo) -> str:
    """
    Map a ColumnInfo to a frontend-friendly type string.

    Date/time types share python_type=datetime.datetime etc., so we check
    the python_type against the datetime module's types first, then fall
    back to the general map.
    """
    pt = col.python_type
    if pt is _dt.datetime:
        return "datetime"
    if pt is _dt.date:
        return "date"
    if pt is _dt.time:
        return "time"
    return _FIELD_TYPE.get(pt, "text")


# ---------------------------------------------------------------------------
# Column serialisation
# ---------------------------------------------------------------------------

def _column_response(col: ColumnInfo) -> dict:
    """
    Serialise a ColumnInfo to the JSON shape the frontend consumes.

    Every field name is chosen for clarity to a frontend developer reading
    the API response — no abbreviations, no internal jargon.
    """
    fk = col.foreign_key  # (schema, table, column) or None
    return {
        "name":         col.name,
        "field_type":   _field_type(col),
        "nullable":     col.nullable,
        "required":     col.required_on_create,
        "editable":     col.is_editable,
        "is_primary_key":  col.is_primary_key,
        "is_audit":        col.is_audit,
        "max_length":      col.max_length,
        "precision":       col.precision,
        "scale":           col.scale,
        "foreign_key": {
            "schema": fk[0],
            "table":  fk[1],
            "column": fk[2],
        } if fk else None,
    }


# ---------------------------------------------------------------------------
# GET /meta
# ---------------------------------------------------------------------------

@router.get("")
def list_schemas(
    snapshot: ReflectedSchema = Depends(get_snapshot),
) -> dict:
    """
    List the configured schemas that have at least one reflected table.

    Served entirely from the in-memory snapshot — no database query. This is
    the entry point the frontend calls once on load to discover what schemas
    exist, before drilling into each via GET /meta/{schema}. Returning the
    list here (rather than baking it into the frontend) keeps the SPA fully
    schema-driven: point a deployment at a different database and the sidebar
    reshapes itself with no frontend change.

    Also returns the connected database name so the frontend can label itself
    with the data it's actually showing rather than a generic product name.
    """
    return {"database": config.DB_DATABASE, "schemas": snapshot.schemas()}


# ---------------------------------------------------------------------------
# GET /meta/{schema}
# ---------------------------------------------------------------------------

@router.get("/{schema}")
def list_tables(
    schema: str,
    snapshot: ReflectedSchema = Depends(get_snapshot),
    db:       Connection      = Depends(get_db),
) -> dict:
    """
    List tables in a schema that the signed-in user can access.

    Returns the intersection of:
      - Tables present in the current reflection snapshot
      - Tables the user has at least SELECT on (per HAS_PERMS_BY_NAME)

    Also returns INSERT/UPDATE/DELETE flags per table so the frontend can
    show or hide New/Edit/Delete controls without attempting the operation.

    Permission check uses the OBO connection (the user's identity), not the
    reflection engine (managed identity), so the result reflects actual
    user grants.
    """
    tables_in_schema = snapshot.tables_in(schema)
    if not tables_in_schema:
        # Schema exists in config but has no reflected tables — either the
        # schema name is wrong or all tables lack a PK. Return empty rather
        # than 404 so the frontend sidebar renders without error.
        return {"schema": schema, "tables": []}

    # Build the permission check in one query: one row per table, four
    # permission columns. Using a VALUES clause avoids N separate queries.
    # QUOTENAME wraps identifiers to handle names with spaces or special chars.
    table_names = [t.name for t in tables_in_schema]

    # `placeholders` is interpolated into the SQL below, but it only ever holds
    # bind-parameter markers — "(:t0), (:t1), ..." — generated from a range(),
    # never any caller-supplied value or identifier. Every actual value (the
    # table names and the schema) travels as a bound parameter in `params`, so
    # there is no injection surface. nosec: B608 is a false positive here.
    placeholders = ", ".join(f"(:t{i})" for i in range(len(table_names)))
    params = {f"t{i}": name for i, name in enumerate(table_names)}

    # Implicitly-concatenated literals so the one interpolated piece — the FROM
    # clause with `placeholders` — and its nosec annotation share a line. Only
    # bind-parameter markers are interpolated; all values are bound parameters.
    perm_query = text(
        "SELECT t.tname, "
        "HAS_PERMS_BY_NAME(QUOTENAME(:schema) + '.' + QUOTENAME(t.tname), 'OBJECT', 'SELECT') AS can_select, "
        "HAS_PERMS_BY_NAME(QUOTENAME(:schema) + '.' + QUOTENAME(t.tname), 'OBJECT', 'INSERT') AS can_insert, "
        "HAS_PERMS_BY_NAME(QUOTENAME(:schema) + '.' + QUOTENAME(t.tname), 'OBJECT', 'UPDATE') AS can_update, "
        "HAS_PERMS_BY_NAME(QUOTENAME(:schema) + '.' + QUOTENAME(t.tname), 'OBJECT', 'DELETE') AS can_delete "
        f"FROM (VALUES {placeholders}) AS t(tname)"  # nosec B608 — only ":tN" bind markers; values are bound (see above)
    )

    params["schema"] = schema

    try:
        rows = db.execute(perm_query, params).fetchall()
    except Exception as e:
        logger.warning("Permission check failed for schema '%s': %s", schema, e)
        # If the permission query itself fails, return an empty list rather
        # than a 500 — the user simply sees no tables.
        return {"schema": schema, "tables": []}

    # Build a lookup: table_name -> {can_select, can_insert, ...}
    perms: dict[str, dict] = {}
    for row in rows:
        perms[row.tname] = {
            "can_select": bool(row.can_select),
            "can_insert": bool(row.can_insert),
            "can_update": bool(row.can_update),
            "can_delete": bool(row.can_delete),
        }

    # Return only tables the user can SELECT. If they can't read it,
    # it shouldn't appear in the sidebar at all.
    visible = []
    for table in tables_in_schema:
        p = perms.get(table.name, {})
        if not p.get("can_select"):
            continue
        visible.append({
            "name":           table.name,
            "display_column": table.display_column,
            "primary_key":    table.primary_key,
            "permissions": {
                "insert": p.get("can_insert", False),
                "update": p.get("can_update", False),
                "delete": p.get("can_delete", False),
            },
        })

    logger.debug(
        "meta/%s: %d/%d tables visible to user",
        schema, len(visible), len(tables_in_schema),
    )
    return {"schema": schema, "tables": visible}


# ---------------------------------------------------------------------------
# GET /meta/{schema}/{table}
# ---------------------------------------------------------------------------

@router.get("/{schema}/{table}")
def describe_table(
    table: TableInfo = Depends(get_table),
) -> dict:
    """
    Full column metadata for one table.

    Served entirely from the in-memory snapshot — no database query.
    The frontend fetches this on first navigation to a table and caches
    it for the session.
    """
    return {
        "schema":            table.schema,
        "name":              table.name,
        "primary_key":       table.primary_key,
        "display_column":    table.display_column,
        # Name of the rowversion column the client echoes back as If-Match to get
        # optimistic-concurrency protection on update/delete, or null if the table
        # has no rowversion (writes then fall back to last-writer-wins).
        "concurrency_token": table.concurrency_token,
        "columns":           [_column_response(c) for c in table.columns],
    }


# ---------------------------------------------------------------------------
# GET /meta/{schema}/{table}/options/{column}
# ---------------------------------------------------------------------------

# Cap on FK dropdown options. A lookup table with more distinct values than
# this needs a typeahead/search endpoint, not a full dropdown — that's future
# work; this bound keeps an accidentally-large lookup from returning the whole
# table to the browser.
_OPTIONS_LIMIT = 1000


@router.get("/{schema}/{table}/options/{column}")
def get_options(
    column:   str,
    table:    TableInfo      = Depends(get_table),
    snapshot: ReflectedSchema = Depends(get_snapshot),
    db:       Connection      = Depends(get_db),
) -> list:
    """
    Value/label pairs for a FK column, used to populate dropdowns.

    Resolves the target table from the column's foreign_key metadata, then
    fetches up to _OPTIONS_LIMIT distinct (pk_value, display_label) pairs
    ordered by label.

    Fails gracefully:
      - Column not found in table → 400
      - Column has no FK → 400
      - Target table not in snapshot → empty list (schema may have changed)
      - User lacks SELECT on target table → empty list (SQL error caught)
    """
    col_info = table.column(column)
    if col_info is None:
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            f"Column '{column}' not found in '{table.schema}.{table.name}'.",
        )

    if col_info.foreign_key is None:
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            f"Column '{column}' is not a foreign key column.",
        )

    fk_schema, fk_table, fk_column = col_info.foreign_key

    # Resolve the target table from the snapshot so we can find its
    # display column without a separate database query.
    target = snapshot.get(fk_schema, fk_table)
    if target is None:
        logger.warning(
            "options: FK target '%s.%s' not in snapshot (schema may have changed)",
            fk_schema, fk_table,
        )
        return []

    # Use display_column for the label if available, fall back to the PK
    # column itself (value == label) if not.
    label_name = target.display_column or fk_column

    # Build the query from the target table's reflected Column objects rather than
    # interpolating identifier strings into raw SQL. SQLAlchemy quotes the
    # identifiers and parameterises the limit, so there is no string-built SQL and
    # the columns are guaranteed to exist on the table. The guard below is
    # defence-in-depth against a snapshot/catalog drift between reflection and now.
    t = target.sa_table
    if fk_column not in t.c or label_name not in t.c:
        logger.warning(
            "options: column '%s'/'%s' missing on target '%s.%s' (schema may have changed)",
            fk_column, label_name, fk_schema, fk_table,
        )
        return []

    value_col = t.c[fk_column]
    label_col = t.c[label_name]
    stmt = (
        select(value_col.label("value"), label_col.label("label"))
        .distinct()
        .order_by(label_col)
        .limit(_OPTIONS_LIMIT)
    )

    try:
        rows = db.execute(stmt).fetchall()
    except Exception as e:
        # Most likely the user lacks SELECT on the target table.
        # Return empty list — dropdown shows no options rather than erroring.
        logger.warning(
            "options: failed to fetch from '%s.%s': %s",
            fk_schema, fk_table, e,
        )
        return []

    return [
        {
            "value": row.value,
            "label": str(row.label) if row.label is not None else str(row.value),
        }
        for row in rows
    ]
