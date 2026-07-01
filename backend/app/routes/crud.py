"""
routes/crud.py — Data access endpoints.

Endpoints per table, all routed by (schema, table):

  POST   /api/{schema}/{table}/query        Search, filter, sort, paginate
  GET    /api/{schema}/{table}/{pk}         Fetch one row by primary key
  POST   /api/{schema}/{table}              Insert a row (201)
  PUT    /api/{schema}/{table}/{pk}         Full-payload update (partial semantics)
  PATCH  /api/{schema}/{table}/{pk}         Partial update
  DELETE /api/{schema}/{table}/{pk}         Delete a row
  POST   /api/{schema}/{table}/bulk-delete  Delete many rows atomically
  POST   /api/{schema}/{table}/bulk-update  Apply one change to many rows atomically
  POST   /api/{schema}/{table}/bulk-create  Import many rows atomically

All data access uses the OBO-authenticated connection from get_db(), so
SQL Server enforces the signed-in user's actual permissions on every
operation. The API never checks permissions itself — if the user can't
SELECT/INSERT/UPDATE/DELETE a table, the database rejects the query and
the route returns a clean PERMISSION_DENIED error.

Primary keys
------------
Composite PKs are represented in the URL as comma-separated values in the
same order as the table's primary key constraint definition. For example,
a TagMap row with OrgID=5 and TagID=3 is addressed as:

    GET /api/dbo/TagMap/5,3

Values are coerced to the column's Python type before comparison. By
convention, primary-key values contain no commas, so no escaping is needed.

Write payload scrubbing
-----------------------
Server-controlled columns can never be set by the client:

  Pass 1 — Pydantic validation against the generated model. Unknown fields
            are stripped; types are coerced/validated; server-controlled
            columns aren't in the model at all, so they can't survive.

  Pass 2 — Belt-and-suspenders removal of any column that shouldn't be
            written, in case a future model change lets one through. On
            create, only non-editable (database-owned) columns are removed —
            a manual primary key the client must supply stays. On update,
            primary keys are removed too (they're addressed via the URL).

Database-managed columns
------------------------
Audit columns — and anything else named in config.DB_AUDIT_COLUMNS — are
classified database-owned and scrubbed above, so the application never writes
them. The database populates them itself via DEFAULT constraints and AFTER
UPDATE triggers; under the OBO connection SUSER_SNAME() resolves to the
signed-in user, so the database records the real caller without the
application supplying anything.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import and_, func, insert, delete, select, update, or_
from sqlalchemy.engine import Connection

from app.auth_headers import CLIENT_PRINCIPAL_NAME
from app.config import BULK_MAX_ROWS
from app.dependencies import get_db, get_table
from app.errors import ApiError, ErrorCode, map_database_exception
from app.reflection import ColumnKind, TableInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["data"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SortRequest(BaseModel):
    column:    str = ""
    direction: str = "asc"   # "asc" | "desc"


class QueryRequest(BaseModel):
    search:    str                  = ""    # LIKE across all text columns
    # column → filter. Two accepted forms:
    #   - operator form: {"op": "gt", "value": 100}  (see _filter_clause)
    #   - shorthand: a bare scalar (equality) or a list (IN)
    filters:   dict[str, Any]       = {}
    sort:      SortRequest          = SortRequest()
    page:      int                  = 1     # 1-based
    page_size: int                  = 50    # rows per page, capped below


class BulkDeleteRequest(BaseModel):
    """
    Body for POST /{schema}/{table}/bulk-delete. Two mutually exclusive modes:

      Explicit   ids = [[pk...], ...]   delete exactly these rows (each item is
                                        the PK values in primary-key order).

      All-matching  all_matching = true, plus the same search/filters the grid
                    used — delete every row matching, even ones not loaded in
                    the client. The server re-evaluates the filter, so what gets
                    deleted is the current matching set, not a stale row list.
    """
    ids:          list[list[Any]] = []
    all_matching: bool            = False
    search:       str             = ""
    filters:      dict[str, Any]  = {}


class BulkCreateRequest(BaseModel):
    """
    Body for POST /{schema}/{table}/bulk-create — import many rows at once
    (typically from a filled-in CSV template). `rows` is a list of column→value
    maps, each validated and scrubbed exactly like a single insert. The whole
    import is atomic: every row is validated first, then inserted inside one
    transaction, so either all rows land or none do.
    """
    rows: list[dict[str, Any]] = []


class BulkUpdateRequest(BaseModel):
    """
    Body for POST /{schema}/{table}/bulk-update — "one change → many rows".

    Targeting mirrors bulk-delete exactly (an explicit `ids` list, or
    `all_matching` with the same search/filters the grid used). `values` is the
    set of column→new-value pairs applied to every targeted row in a single
    UPDATE. Only the supplied columns are written; everything else is left
    untouched. The same scrubbing as a single-row update applies, so primary
    keys and server-controlled columns can never be set here.
    """
    ids:          list[list[Any]] = []
    all_matching: bool            = False
    search:       str             = ""
    filters:      dict[str, Any]  = {}
    values:       dict[str, Any]  = {}


_MAX_PAGE_SIZE = 500


# Operators that compare against the column with a Python comparison operator.
_COMPARATORS = {
    "eq":  lambda c, v: c == v,
    "ne":  lambda c, v: c != v,
    "gt":  lambda c, v: c > v,
    "gte": lambda c, v: c >= v,
    "lt":  lambda c, v: c < v,
    "lte": lambda c, v: c <= v,
}

# Operators that build a LIKE pattern. The user's value is wildcard-escaped and
# wrapped per operator; matching follows the column's collation, like search.
_LIKE_PATTERNS = {
    "contains":   lambda v: f"%{v}%",
    "startswith": lambda v: f"{v}%",
    "endswith":   lambda v: f"%{v}",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user_from_request(request: Request) -> Optional[str]:
    """
    Extract the signed-in user's display name from the EasyAuth header, for
    log context only. Returns None if the header is absent (e.g. local dev
    without the proxy). Authentication and authorization are handled entirely
    by the OBO connection — nothing in the request path depends on this value.
    """
    return request.headers.get(CLIENT_PRINCIPAL_NAME)


def _execute(db: Connection, stmt):
    """
    Execute a statement, translating database exceptions into the API's error
    contract. ApiError passes through untouched (it's already in the contract);
    everything else goes through map_database_exception, so a denied grant
    surfaces as PERMISSION_DENIED rather than a raw 500.

    The original database error is logged before it's mapped away — the client
    only gets a generic, safe message (e.g. CONSTRAINT_VIOLATION), so the precise
    cause (which constraint/table) lives only here. `e.orig` is the raw DBAPI
    error, which names the constraint/table but not the row's values. Correlate
    it with a request via the X-Request-ID in the log line.
    """
    try:
        return db.execute(stmt)
    except ApiError:
        raise
    except Exception as e:
        # Pass the statement's table so a unique/FK violation can name the exact
        # column the user edited (the raw message only names the referenced side).
        mapped = map_database_exception(e, getattr(stmt, "table", None))
        level = logging.ERROR if mapped.status_code >= 500 else logging.WARNING
        logger.log(level, "Database error → %s: %s", mapped.code.value, getattr(e, "orig", e))
        raise mapped


def _pk_filter(table: TableInfo, pk_str: str):
    """
    Build a SQLAlchemy WHERE clause for a primary key lookup.

    For composite PKs, pk_str is comma-separated values in PK column order.
    Each value is coerced to the column's Python type; falls back to raw
    string if coercion fails (SQL Server will cast or reject as appropriate).
    """
    t = table.sa_table
    pk_cols = list(t.primary_key.columns)
    values  = pk_str.split(",")

    if len(values) != len(pk_cols):
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            f"Expected {len(pk_cols)} primary key value(s), got {len(values)}.",
        )

    conditions = []
    for col, val in zip(pk_cols, values):
        try:
            typed = col.type.python_type(val.strip())
        except Exception:
            typed = val.strip()
        conditions.append(col == typed)

    return and_(*conditions)


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards so a user's text is matched literally (escape='\\')."""
    return (
        term.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _require_comparable(sa_col) -> None:
    """
    Reject a value filter on a column the database can't compare or LIKE —
    varbinary/rowversion (bytes) and other opaque types (xml). Offering a text
    operator such as "contains" on these produced a driver error (or a bare 500)
    that the grid then silently swallowed; fail clean with a 400 instead. Null
    checks (isnull/notnull) don't compare a value, so they skip this.
    """
    try:
        pytype = sa_col.type.python_type
    except (NotImplementedError, AttributeError):
        pytype = None
    if pytype is None or pytype is bytes:
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            "Filtering is not supported for this column type.",
        )


def _filter_clause(sa_col, raw):
    """
    Build a WHERE clause for one column filter, or return None to skip it.

    Two request forms are accepted:

      Operator form  {"op": <operator>, "value": <value>}
        eq | ne | gt | gte | lt | lte     value compared directly
        contains | startswith | endswith  LIKE on the (escaped) text value
        between                           value is a 2-item [low, high] list
        in                                value is a list
        isnull | notnull                  no value

      Shorthand (no "op" key)
        a bare scalar  -> equality
        a list         -> IN

    A value-requiring operator with a missing/empty value returns None so the
    caller drops the filter rather than building a meaningless clause (e.g. a
    half-typed filter chip in the UI doesn't constrain the query).
    """
    # Shorthand: bare scalar (equality) or list (IN) — both compare by value.
    if not isinstance(raw, dict):
        _require_comparable(sa_col)
        if isinstance(raw, list):
            return sa_col.in_(raw) if raw else sa_col.in_([None])
        return sa_col == raw

    op  = raw.get("op", "eq")
    val = raw.get("value")

    if op == "isnull":
        return sa_col.is_(None)
    if op == "notnull":
        return sa_col.is_not(None)

    # Every remaining operator compares against the column's value (LIKE too),
    # which varbinary/xml can't do — reject before building a clause the driver
    # would only fail on.
    _require_comparable(sa_col)

    if op == "between":
        if not isinstance(val, (list, tuple)) or len(val) != 2:
            return None
        low, high = val
        if low is None or high is None or low == "" or high == "":
            return None
        return sa_col.between(low, high)

    if op == "in":
        return sa_col.in_(val) if isinstance(val, list) and val else None

    if op in _LIKE_PATTERNS:
        if val is None or val == "":
            return None
        pattern = _LIKE_PATTERNS[op](_escape_like(str(val)))
        return sa_col.like(pattern, escape="\\")

    comparator = _COMPARATORS.get(op)
    if comparator is None or val is None or val == "":
        return None
    return comparator(sa_col, val)


def _search_clause(table: TableInfo, search: str):
    """
    Build a LIKE-across-all-text-columns clause for a free-text search, or None
    if there's nothing to search (empty term, or no text columns).
    """
    term = search.strip()
    if not term:
        return None
    text_cols = [col for col in table.columns if col.is_text]
    if not text_cols:
        return None
    t = table.sa_table
    escaped = _escape_like(term)
    return or_(*[t.c[col.name].like(f"%{escaped}%", escape="\\") for col in text_cols])


def _query_where(table: TableInfo, search: str, filters: dict[str, Any]) -> list:
    """
    All WHERE clauses for a search + column-filter query.

    Shared by the query endpoint and the bulk operations' "all matching" mode (via
    _resolve_bulk_target), so that "apply to everything matching" acts on exactly
    the set the grid shows for the same search and filters. Unknown columns and
    incomplete filters are skipped (see _filter_clause).
    """
    clauses = []
    search_clause = _search_clause(table, search)
    if search_clause is not None:
        clauses.append(search_clause)

    t = table.sa_table
    for col_name, raw in filters.items():
        if col_name not in t.c:
            continue
        clause = _filter_clause(t.c[col_name], raw)
        if clause is not None:
            clauses.append(clause)
    return clauses


def _pk_in_clause(table: TableInfo, ids: list[list[Any]]):
    """
    Build a WHERE clause matching any of the given primary keys.

    `ids` is a list of rows, each a list of PK values in primary-key column
    order (single-column PKs are one-element lists). Each value is coerced to
    its column's Python type, mirroring _pk_filter. Composite keys compile to
    OR-of-ANDs: (a=? AND b=?) OR (a=? AND b=?) ...
    """
    t = table.sa_table
    pk_cols = list(t.primary_key.columns)

    rows = []
    for values in ids:
        if len(values) != len(pk_cols):
            raise ApiError(
                ErrorCode.BAD_REQUEST,
                f"Expected {len(pk_cols)} primary key value(s) per row, got {len(values)}.",
            )
        conditions = []
        for col, val in zip(pk_cols, values):
            try:
                typed = col.type.python_type(val)
            except Exception:
                typed = val
            conditions.append(col == typed)
        rows.append(and_(*conditions))
    return or_(*rows)


def _bulk_count(db: Connection, t, where) -> int:
    """Count rows matching an optional WHERE clause (no clause = the whole table)."""
    stmt = select(func.count()).select_from(t)
    if where is not None:
        stmt = stmt.where(where)
    return _execute(db, stmt).scalar() or 0


def _resolve_bulk_target(
    table: TableInfo,
    body: "BulkDeleteRequest | BulkUpdateRequest",
    db: Connection,
    verb: str,
):
    """
    Resolve the WHERE clause for a bulk delete/update and enforce BULK_MAX_ROWS.

    Shared by bulk-delete and bulk-update, which target rows identically (an
    explicit `ids` list, or "all matching" the search/filters). Returns
    (where, run):

      run is False              the selection is provably empty — caller no-ops
      run is True, where None    apply to every row (all-matching, no filter)
      run is True, where <expr>  apply to the matching / selected rows

    Raises BAD_REQUEST for an empty explicit selection or one over the cap; `verb`
    ("delete"/"update") only shapes the messages. Over-cap requests are refused
    rather than truncated or split — splitting would break the all-or-nothing
    guarantee of the single transaction.
    """
    if body.all_matching:
        clauses = _query_where(table, body.search, body.filters)
        where   = and_(*clauses) if clauses else None
        total   = _bulk_count(db, table.sa_table, where)
        if total == 0:
            return None, False
        if total > BULK_MAX_ROWS:
            raise ApiError(
                ErrorCode.BAD_REQUEST,
                f"This would {verb} {total} rows, more than the {BULK_MAX_ROWS}-row "
                f"limit for a single operation. Narrow the filter and try again.",
            )
        return where, True

    if not body.ids:
        raise ApiError(ErrorCode.BAD_REQUEST, f"No rows were selected to {verb}.")
    if len(body.ids) > BULK_MAX_ROWS:
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            f"You selected {len(body.ids)} rows, more than the {BULK_MAX_ROWS}-row "
            f"limit for a single operation.",
        )
    return _pk_in_clause(table, body.ids), True


def _validate_write(table: TableInfo, body: dict, model_attr: str) -> dict:
    """
    Validate a write payload against the table's Pydantic model.

    model_attr is 'create_model' or 'update_model'. Returns the validated
    dict with only supplied fields (exclude_unset=True preserves the
    explicit-null vs omitted distinction for nullable columns).

    Raises ApiError(VALIDATION_ERROR) with per-field detail on failure so
    the frontend can highlight the specific inputs that need fixing.
    """
    model = getattr(table, model_attr)
    try:
        return model(**body).model_dump(exclude_unset=True)
    except ValidationError as e:
        fields = {}
        for err in e.errors():
            loc = err.get("loc", ())
            field = str(loc[-1]) if loc else "unknown"
            fields[field] = err.get("msg", "Invalid value")
        raise ApiError(ErrorCode.VALIDATION_ERROR, fields=fields)


def _scrub_create_payload(table: TableInfo, body: dict) -> dict:
    """
    Validate and scrub a write payload for an insert.

    Pass 1 — Pydantic validation against the create model (strips unknown
             fields, coerces/validates types). Server-controlled columns aren't
             in the model, so they can't survive.
    Pass 2 — belt-and-suspenders removal of any database-owned column. A manual
             primary key is editable, so it stays; the database owns the rest.

    Shared by the single-row create and the bulk create so both insert exactly
    the same set of columns from the same input.
    """
    payload = _validate_write(table, body, "create_model")
    non_editable = {
        c.name for c in table.columns
        if c.kind is not ColumnKind.EDITABLE
    }
    return {k: v for k, v in payload.items() if k not in non_editable}


def _scrub_update_payload(table: TableInfo, body: dict) -> dict:
    """
    Validate and scrub a write payload for an update.

    Pass 1 — Pydantic validation against the update model (strips unknown
             fields, coerces/validates types, preserves explicit nulls via
             exclude_unset so a nullable column can be cleared).
    Pass 2 — belt-and-suspenders removal of primary keys (addressed via the URL
             on a single update, matched separately on a bulk one) and any
             server-controlled column.

    Shared by the single-row and bulk updates so both write exactly the same
    set of columns from the same input. Returns the scrubbed dict; the caller
    decides what an empty result means (no-op vs. error).
    """
    validated = _validate_write(table, body, "update_model")
    pk_names = set(table.primary_key)
    non_editable = {
        c.name for c in table.columns
        if c.kind is not ColumnKind.EDITABLE
    }
    return {
        k: v for k, v in validated.items()
        if k not in pk_names and k not in non_editable
    }


def _row_to_dict(row) -> dict:
    """
    Convert a result row to a plain dict, hex-encoding any binary values.

    FastAPI's jsonable_encoder runs before our custom JSON encoder and would
    utf-8-decode raw bytes — lossy, and an outright failure on bytes that aren't
    valid utf-8 (a rowversion is an arbitrary 8-byte value). Hex-encoding here is
    stable and round-trips: a rowversion read this way comes back byte-for-byte
    when the client echoes it as the If-Match token on the next write.
    """
    return {
        key: (bytes(value).hex() if isinstance(value, (bytes, bytearray, memoryview)) else value)
        for key, value in row._mapping.items()
    }


def _fetch_row(table: TableInfo, pk_str: str, db: Connection) -> dict:
    """
    Fetch and return one row by primary key as a plain dict.
    Raises 404 ApiError if not found.
    """
    t   = table.sa_table
    row = _execute(db, select(t).where(_pk_filter(table, pk_str))).fetchone()
    if row is None:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            f"No row found in '{table.schema}.{table.name}' with key '{pk_str}'.",
        )
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Optimistic concurrency (rowversion / If-Match)
#
# When a table has a rowversion column (TableInfo.concurrency_token), a read
# returns its current value (hex-encoded by the JSON encoder, since rowversion is
# binary). A client that wants protection echoes that value back as an If-Match
# header on update/delete; we add it to the WHERE so the write only lands if the
# row hasn't changed since it was read. Zero rows affected then means either the
# row is gone (404) or its version moved on (409 CONFLICT) — distinguished by a
# follow-up existence check. A table WITH a rowversion *requires* If-Match on
# writes (see _require_if_match) — an unguarded write would silently overwrite a
# concurrent edit — so last-writer-wins applies only to tables with no rowversion.
# ---------------------------------------------------------------------------

def _decode_token(raw: str) -> bytes:
    """
    Decode an If-Match rowversion token back to the 8-byte value to compare in
    SQL. Reads serialize rowversion via bytes.hex() (see main._AppEncoder), so the
    client sends that hex string back; we tolerate an optional 0x prefix and ETag
    quoting. A malformed token is a client error, not a silent no-op.
    """
    s = raw.strip().strip('"')
    if s[:2].lower() == "0x":
        s = s[2:]
    try:
        return bytes.fromhex(s)
    except ValueError:
        raise ApiError(ErrorCode.BAD_REQUEST, "Malformed If-Match concurrency token.")


def _concurrency_clause(table: TableInfo, if_match: Optional[str]):
    """
    WHERE clause asserting the row still carries the client's expected rowversion,
    or None when no precondition applies (table has no rowversion column, or the
    client sent no If-Match). The bytes bind directly against the rowversion
    column — its type is binary (mssql TIMESTAMP subclasses _Binary).
    """
    token_col = table.concurrency_token
    if not token_col or not if_match:
        return None
    return table.sa_table.c[token_col] == _decode_token(if_match)


def _require_if_match(table: TableInfo, if_match: Optional[str]) -> None:
    """
    A write to a table with a rowversion MUST carry the client's expected version
    (If-Match), so a concurrent edit is detected instead of silently overwritten.
    Reject the unguarded write rather than lose another user's change. (Tables
    without a rowversion have no version to check and are unaffected.)
    """
    if table.concurrency_token and not if_match:
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            "This record uses optimistic concurrency; reload it and retry so the "
            "write carries its current version (the If-Match header).",
        )


def _row_exists(table: TableInfo, pk_str: str, db: Connection) -> bool:
    """True if a row with this primary key currently exists (committed state)."""
    t = table.sa_table
    count = _execute(
        db, select(func.count()).select_from(t).where(_pk_filter(table, pk_str))
    ).scalar()
    return (count or 0) > 0


def _raise_write_miss(table: TableInfo, pk_str: str, had_precondition: bool, db: Connection):
    """
    Raise the right error when an update/delete affected zero rows. With a
    concurrency precondition in play, a still-present row means a version
    conflict (409); otherwise — or if the row is genuinely gone — it's a 404.
    """
    if had_precondition and _row_exists(table, pk_str, db):
        raise ApiError(ErrorCode.CONFLICT)
    raise ApiError(
        ErrorCode.NOT_FOUND,
        f"No row found in '{table.schema}.{table.name}' with key '{pk_str}'.",
    )


# ---------------------------------------------------------------------------
# POST /api/{schema}/{table}/query
# ---------------------------------------------------------------------------

@router.post("/{schema}/{table}/query")
def query_rows(
    body:  QueryRequest,
    table: TableInfo  = Depends(get_table),
    db:    Connection = Depends(get_db),
) -> dict:
    """
    Search, filter, sort, and paginate rows.

    POST is used instead of GET so the structured query — search, filters
    (including IN lists), sort, and pagination — travels as a typed JSON body
    rather than a sprawl of query-string parameters. That keeps it validated by
    the Pydantic model above and keeps filter values, which may be sensitive,
    out of URLs, access logs, and browser history.

    Search uses LIKE against all text columns (case sensitivity follows the
    column's collation — CI_AS databases are case-insensitive by default).
    Filters are per-column operator clauses — equality, comparisons, ranges
    (between), text matching (contains/startswith/endswith), set membership
    (in), and null tests — see _filter_clause. Sort defaults to the primary
    key for stable pagination.
    """
    t          = table.sa_table
    stmt       = select(t)
    count_stmt = select(func.count()).select_from(t)

    # Search (LIKE across text columns) + per-column operator filters. Shared
    # with bulk-delete so "delete all matching" matches what the grid shows.
    for clause in _query_where(table, body.search, body.filters):
        stmt       = stmt.where(clause)
        count_stmt = count_stmt.where(clause)

    # Sorting — explicit column first, PK columns appended for stability
    if body.sort.column and body.sort.column in t.c:
        sa_col = t.c[body.sort.column]
        stmt = stmt.order_by(
            sa_col.desc() if body.sort.direction == "desc" else sa_col.asc()
        )

    pk_cols = [t.c[name] for name in table.primary_key if name != body.sort.column]
    if pk_cols:
        stmt = stmt.order_by(*pk_cols)

    # Pagination. Reject an over-cap page_size rather than silently clamp it, so a
    # client can't miscount pages against a size the server didn't actually use.
    if body.page_size > _MAX_PAGE_SIZE:
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            f"page_size must be at most {_MAX_PAGE_SIZE}.",
        )
    page      = max(1, body.page)
    page_size = max(1, body.page_size)
    offset    = (page - 1) * page_size

    total = _execute(db, count_stmt).scalar() or 0
    rows  = _execute(db, stmt.offset(offset).limit(page_size)).fetchall()
    pages = max(1, -(-total // page_size))  # ceiling division

    logger.debug(
        "QUERY %s.%s  search=%r  page=%d/%d  total=%d",
        table.schema, table.name, body.search, page, pages, total,
    )

    return {
        "data":      [_row_to_dict(r) for r in rows],
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "pages":     pages,
    }


# ---------------------------------------------------------------------------
# GET /api/{schema}/{table}/{pk}
# ---------------------------------------------------------------------------

@router.get("/{schema}/{table}/{pk}")
def get_row(
    pk:    str,
    table: TableInfo  = Depends(get_table),
    db:    Connection = Depends(get_db),
) -> dict:
    """Fetch a single row by primary key."""
    return _fetch_row(table, pk, db)


# ---------------------------------------------------------------------------
# POST /api/{schema}/{table}
# ---------------------------------------------------------------------------

@router.post("/{schema}/{table}", status_code=status.HTTP_201_CREATED)
def create_row(
    body:    dict[str, Any],
    request: Request,
    table:   TableInfo  = Depends(get_table),
    db:      Connection = Depends(get_db),
) -> dict:
    """
    Insert a new row and return it.

    Server-controlled columns (identity, computed, value-generating defaults,
    and anything named in config.DB_AUDIT_COLUMNS) are not part of the create
    model and are scrubbed again below, so the database populates them itself.
    A manual primary key, if the table has one, IS accepted. The inserted row
    is fetched and returned so the client receives all database-generated
    values (identity PKs, computed columns, server defaults).
    """
    user    = _user_from_request(request)
    payload = _scrub_create_payload(table, body)

    result = _execute(db, insert(table.sa_table).values(**payload))

    # Build the PK string to fetch the inserted row back.
    # inserted_primary_key is a tuple in PK column order.
    if result.inserted_primary_key:
        pk_str = ",".join(str(v) for v in result.inserted_primary_key)
    else:
        # Rare: no PK available (shouldn't happen — tables without PKs are
        # excluded at reflection time). Return a minimal success response.
        logger.warning("INSERT %s.%s: no inserted_primary_key returned", table.schema, table.name)
        return {"status": "created"}

    logger.info("INSERT %s.%s  pk=%s  user=%s", table.schema, table.name, pk_str, user)
    return _fetch_row(table, pk_str, db)


# ---------------------------------------------------------------------------
# PUT /api/{schema}/{table}/{pk}
# PATCH /api/{schema}/{table}/{pk}
# ---------------------------------------------------------------------------

@router.put("/{schema}/{table}/{pk}")
@router.patch("/{schema}/{table}/{pk}")
def update_row(
    pk:      str,
    body:    dict[str, Any],
    request: Request,
    table:   TableInfo  = Depends(get_table),
    db:      Connection = Depends(get_db),
) -> dict:
    """
    Update an existing row by primary key and return the updated row.

    Both PUT and PATCH use partial-update semantics: only fields present
    in the request body are written. Fields absent from the body are left
    unchanged. Sending a field explicitly as null clears it (if the column
    is nullable) — Pydantic's exclude_unset preserves this distinction.

    Two-pass scrubbing ensures server-controlled columns can never be set
    by the client regardless of what the request body contains.

    Optimistic concurrency: a table with a rowversion column *requires* an
    If-Match header carrying the version the client read; the update only lands
    when the row still carries it — otherwise a 409 CONFLICT (the row changed
    since it was read). A write with no If-Match is rejected (400) rather than
    silently overwriting a concurrent edit. See the concurrency helpers above.
    """
    user = _user_from_request(request)

    # Validate + scrub (PKs and server-controlled columns can never be written).
    payload = _scrub_update_payload(table, body)

    if not payload:
        # Nothing to update after scrubbing — return the current row rather
        # than issuing a no-op UPDATE.
        return _fetch_row(table, pk, db)

    if_match = request.headers.get("if-match")
    _require_if_match(table, if_match)
    precondition = _concurrency_clause(table, if_match)

    stmt = update(table.sa_table).where(_pk_filter(table, pk)).values(**payload)
    if precondition is not None:
        stmt = stmt.where(precondition)
    result = _execute(db, stmt)

    if result.rowcount == 0:
        _raise_write_miss(table, pk, precondition is not None, db)

    logger.info("UPDATE %s.%s  pk=%s  user=%s", table.schema, table.name, pk, user)
    return _fetch_row(table, pk, db)


# ---------------------------------------------------------------------------
# DELETE /api/{schema}/{table}/{pk}
# ---------------------------------------------------------------------------

@router.delete("/{schema}/{table}/{pk}")
def delete_row(
    pk:      str,
    request: Request,
    table:   TableInfo  = Depends(get_table),
    db:      Connection = Depends(get_db),
) -> dict:
    """
    Delete a row by primary key.

    Optimistic concurrency: a table with a rowversion column *requires* an
    If-Match header carrying the version the client read; the delete only lands
    when the row still carries it — otherwise a 409 CONFLICT. A delete with no
    If-Match is rejected (400) so a stale view can't unknowingly remove a newer
    revision.
    """
    # Record who deleted what: a delete removes the row, so unlike INSERT/UPDATE
    # the database's audit columns can't preserve the actor — the application log
    # is the only place "who deleted this" survives. Keep this consistent with
    # the INSERT/UPDATE/bulk lines, which all carry user=.
    user = _user_from_request(request)

    if_match = request.headers.get("if-match")
    _require_if_match(table, if_match)
    precondition = _concurrency_clause(table, if_match)

    stmt = delete(table.sa_table).where(_pk_filter(table, pk))
    if precondition is not None:
        stmt = stmt.where(precondition)
    result = _execute(db, stmt)

    if result.rowcount == 0:
        _raise_write_miss(table, pk, precondition is not None, db)

    logger.info("DELETE %s.%s  pk=%s  user=%s", table.schema, table.name, pk, user)
    # Return the count of rows removed (always 1 here) so the response matches
    # bulk-delete's {"deleted": <count>} shape — single- and many-row deletes
    # speak the same contract instead of single-delete echoing the pk string.
    return {"deleted": result.rowcount}


# ---------------------------------------------------------------------------
# POST /api/{schema}/{table}/bulk-delete
# ---------------------------------------------------------------------------

@router.post("/{schema}/{table}/bulk-delete")
def bulk_delete_rows(
    body:    BulkDeleteRequest,
    request: Request,
    table:   TableInfo  = Depends(get_table),
    db:      Connection = Depends(get_db),
) -> dict:
    """
    Delete many rows in a single, atomic operation.

    Atomicity is structural: the whole route runs inside the one transaction
    opened by get_db (see connection.get_user_db), which commits only if the
    route returns and rolls back if it raises. So if any row's deletion violates
    a constraint (e.g. it's still referenced by a foreign key), the database
    raises, the transaction rolls back, and nothing is deleted — never a partial
    batch. SQL Server still enforces the user's DELETE grant on every row.

    Two modes (see BulkDeleteRequest): an explicit list of primary keys, or
    "all matching" the supplied search/filters. Both are capped at
    BULK_MAX_ROWS to bound the size of the single transaction; a request over
    the cap is refused with BAD_REQUEST rather than silently truncated or split
    (splitting would break the all-or-nothing guarantee).
    """
    user = _user_from_request(request)
    t    = table.sa_table

    where, run = _resolve_bulk_target(table, body, db, "delete")
    if not run:
        return {"deleted": 0}

    stmt = delete(t).where(where) if where is not None else delete(t)
    result = _execute(db, stmt)

    deleted = result.rowcount if result.rowcount is not None else 0
    logger.info(
        "BULK DELETE %s.%s  deleted=%d  all_matching=%s  user=%s",
        table.schema, table.name, deleted, body.all_matching, user,
    )
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# POST /api/{schema}/{table}/bulk-update
# ---------------------------------------------------------------------------

@router.post("/{schema}/{table}/bulk-update")
def bulk_update_rows(
    body:    BulkUpdateRequest,
    request: Request,
    table:   TableInfo  = Depends(get_table),
    db:      Connection = Depends(get_db),
) -> dict:
    """
    Apply one set of column values to many rows in a single, atomic operation
    ("one change → many rows").

    Atomicity is structural, exactly as for bulk delete: the whole route runs
    inside the one transaction opened by get_db, so if the new values violate a
    constraint on any row (a unique key, a foreign key, NOT NULL, a CHECK) the
    database raises, the transaction rolls back, and no row is changed — never a
    partial batch. SQL Server still enforces the user's UPDATE grant.

    `values` is validated and scrubbed through the same path as a single-row
    update, so primary keys and server-controlled columns can't be written and
    types are coerced/validated up front (a bad value comes back as a per-field
    VALIDATION_ERROR before the database is touched). Targeting matches
    bulk-delete: an explicit `ids` list or "all matching" the search/filters,
    both capped at BULK_MAX_ROWS and refused (changing nothing) when over.
    """
    user = _user_from_request(request)
    t    = table.sa_table

    # Validate the change first — cheap, no database round-trip — so an empty or
    # invalid payload fails before we count or touch any rows.
    payload = _scrub_update_payload(table, body.values)
    if not payload:
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            "No editable fields were provided to update.",
        )

    where, run = _resolve_bulk_target(table, body, db, "update")
    if not run:
        return {"updated": 0}

    stmt = update(t).values(**payload)
    if where is not None:
        stmt = stmt.where(where)
    result = _execute(db, stmt)

    updated = result.rowcount if result.rowcount is not None else 0
    logger.info(
        "BULK UPDATE %s.%s  updated=%d  fields=%s  all_matching=%s  user=%s",
        table.schema, table.name, updated, list(payload), body.all_matching, user,
    )
    return {"updated": updated}


# ---------------------------------------------------------------------------
# POST /api/{schema}/{table}/bulk-create
# ---------------------------------------------------------------------------

@router.post("/{schema}/{table}/bulk-create")
def bulk_create_rows(
    body:    BulkCreateRequest,
    request: Request,
    table:   TableInfo  = Depends(get_table),
    db:      Connection = Depends(get_db),
) -> dict:
    """
    Import many rows in a single, atomic operation (typically a filled-in CSV
    template). Either every row lands or none do.

    Two phases. First, every row is validated and scrubbed through the same path
    as a single insert (_scrub_create_payload) — no database is touched yet — and
    any per-row failures are collected and returned together as a VALIDATION_ERROR
    with a `rows` map, so the client can highlight every bad cell in one pass.
    Only if all rows validate are they inserted, one statement at a time, inside
    the one transaction get_db opens. A constraint the up-front validation can't
    see (a duplicate key, a missing foreign key) makes that row's insert raise;
    the transaction rolls back, nothing is committed, and the error carries the
    offending `row` index. SQL Server still enforces the user's INSERT grant.

    Capped at BULK_MAX_ROWS to bound the single transaction; an over-cap import
    is refused (creating nothing) rather than truncated.
    """
    user = _user_from_request(request)
    t    = table.sa_table

    if not body.rows:
        raise ApiError(ErrorCode.BAD_REQUEST, "No rows to import.")
    if len(body.rows) > BULK_MAX_ROWS:
        raise ApiError(
            ErrorCode.BAD_REQUEST,
            f"This would import {len(body.rows)} rows, more than the "
            f"{BULK_MAX_ROWS}-row limit for a single operation. Split the file "
            f"and try again.",
        )

    # Phase 1 — validate/scrub every row, collecting per-row field errors. No
    # database access, so a malformed file fails fast and completely.
    payloads: list[dict] = []
    row_errors: dict[str, dict[str, str]] = {}
    for i, raw in enumerate(body.rows):
        try:
            payloads.append(_scrub_create_payload(table, raw))
        except ApiError as e:
            if e.code is ErrorCode.VALIDATION_ERROR and e.fields is not None:
                row_errors[str(i)] = e.fields
            else:
                raise

    if row_errors:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            "Some rows have values that need fixing.",
            rows=row_errors,
        )

    # Phase 2 — insert row by row inside the one transaction, so a database
    # constraint failure is attributable to a specific line. Any raise rolls the
    # whole batch back (structural atomicity), so nothing is partially imported.
    for i, payload in enumerate(payloads):
        try:
            db.execute(insert(t).values(**payload))
        except ApiError:
            raise
        except Exception as e:
            mapped = map_database_exception(e, t)
            raise ApiError(mapped.code, f"Row {i + 1}: {mapped.message}", row=i)

    created = len(payloads)
    logger.info(
        "BULK CREATE %s.%s  created=%d  user=%s", table.schema, table.name, created, user,
    )
    return {"created": created}
