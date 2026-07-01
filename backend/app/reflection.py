"""
reflection.py — Schema introspection, column classification, and Pydantic
model generation across all configured schemas.

Public entry point
-------------------
    reflect_schemas() -> ReflectedSchema

Each call reflects all schemas in config.DB_SCHEMAS from scratch and
returns a brand-new ReflectedSchema. Nothing is mutated in place, so the
caller can swap in a new snapshot while in-flight requests finish against
the previous one. A stale read during a rare refresh is an accepted cost
— this module does no locking.

Output shape
-------------
Everything about a table lives in one TableInfo; everything about a column
lives in one ColumnInfo nested underneath it. Downstream consumers (the
metadata endpoint, CRUD routes, query layer) read from these directly —
nothing needs to be re-derived from raw SQLAlchemy objects.

Multi-schema keying
--------------------
Tables are keyed by (schema, name) throughout — in ReflectedSchema.tables
and in generated Pydantic model class names — so two schemas with a table
of the same name never collide.

Column classification
----------------------
Two categories:

  DB-owned       The database controls the value; the API never accepts it
                 from a client. Detected two ways:
                   - Structural: identity columns; computed and GENERATED ALWAYS
                     period columns; or a server_default that invokes a
                     value-generating SQL Server function (e.g. sysutcdatetime(),
                     newid()).
                   - Name-based: columns named in config.DB_AUDIT_COLUMNS.
                     These are populated by a stored procedure or trigger,
                     which SQL Server doesn't expose as column-level metadata
                     — there is no structural way to detect them.

  User-editable  Everything else; surfaced in the Create/Update models.

If a table's audit columns aren't wired to a stored procedure or trigger,
they'll stay NULL silently. That's an operational concern, not something
schema introspection can detect.

A note on reflection vs. privilege. SQLAlchemy derives a column's computed and
default status from object DEFINITION text, which SQL Server hides unless the
caller holds GRANT VIEW DEFINITION. A reflection identity that does not hold it
sees col.computed and col.server_default come back empty, so those columns would
be silently misclassified. The
classification therefore reads the underlying flags straight from sys.columns
(is_computed, generated_always_type, default_object_id — see _column_flags),
which are visible with only table access. This mirrors the FK / history /
period-column queries, which already bypass reflection for the same reason. One
distinction can't be made from sys.columns alone: the default's *text* is gated
too, so a value-generating default (newid()) can't be told from a constant one —
enough to keep such a column from being marked required, but not enough to mark
it DB-owned (it stays editable). The Terraform deployment grants the reflection
identity VIEW DEFINITION to close that last gap (see infra/.../sql.tf); the
structural reads here are the safety net that keeps classification correct even
on a database where it has not been granted.

Excluded types
---------------
Binary (VARBINARY, BINARY, IMAGE), rowversion/TIMESTAMP, XML, and
sql_variant are excluded from write payloads. Binary and XML are surfaced
read-only in metadata; sql_variant is omitted entirely (no fixed shape).
TIMESTAMP/rowversion columns are also server-generated — SQL Server will
reject writes at the engine level, so excluding them prevents a confusing
runtime error.

HIERARCHYID falls back to str and lands as EDITABLE — this SQLAlchemy
version's mssql dialect doesn't export a HIERARCHYID type to check
against. If your schema uses it, add it to _EXCLUDED_WRITE_TYPES once
a newer SQLAlchemy version exports the type.

Decimal handling
-----------------
DECIMAL/NUMERIC/MONEY/SMALLMONEY map to Python Decimal, never float,
to avoid silent precision loss on money values.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from typing import Annotated, Optional

from pydantic import Field, create_model
from sqlalchemy import MetaData, Table, bindparam, text
from sqlalchemy.sql.schema import Column
from sqlalchemy.dialects.mssql import (
    BIGINT, BINARY, BIT, CHAR, DATE, DATETIME, DATETIME2,
    DATETIMEOFFSET, DECIMAL, FLOAT, IMAGE, INTEGER, JSON, MONEY,
    NCHAR, NTEXT, NUMERIC, NVARCHAR, REAL, SMALLDATETIME,
    SMALLINT, SMALLMONEY, SQL_VARIANT, TEXT, TIME, TIMESTAMP,
    TINYINT, UNIQUEIDENTIFIER, VARBINARY, VARCHAR, XML,
)

from app.config import DB_SCHEMAS, DB_AUDIT_COLUMNS
from app.connection import reflection_engine, _connect_with_retry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Column classification
# ---------------------------------------------------------------------------

class ColumnKind(Enum):
    """Whether a column is writable by the client."""
    EDITABLE = auto()  # Client-writable; appears in Create/Update models.
    DB_OWNED = auto()  # Database controls the value; never client-writable.
    EXCLUDED = auto()  # Type not supported for writes; read-only in metadata.


# Trigger/default-populated columns (audit columns being the canonical case)
# are named in config.DB_AUDIT_COLUMNS — they live there, not here, because
# they vary per deployment. Matched case-insensitively in _classify().

# SQL Server functions that generate a value server-side. A column whose
# server_default contains one of these is DB-owned. Plain value defaults
# (e.g. DEFAULT 0) are not included — the client may still override those.
_DB_GENERATING_FUNCTIONS = {
    "sysutcdatetime", "sysdatetime", "sysdatetimeoffset",
    "getdate", "getutcdate", "current_timestamp",
    "newid", "newsequentialid",
}

# Types excluded from write payloads. Surfaced read-only in metadata as str
# where representable; sql_variant omitted entirely (no fixed shape).
_EXCLUDED_WRITE_TYPES = (
    VARBINARY, BINARY, IMAGE, TIMESTAMP,  # binary / rowversion
    XML,                                   # structured-but-opaque
    SQL_VARIANT,                           # no fixed shape
)

_UNREPRESENTABLE_TYPES = (SQL_VARIANT,)


def _server_default_generates_value(col: Column) -> bool:
    """
    True if the column's server_default calls a known value-generating function.

    Uses substring search rather than equality because SQLAlchemy reflects
    SQL Server defaults in varying forms — e.g. ((sysutcdatetime())),
    (sysutcdatetime()), ([dbo].[fn]()). The function names in
    _DB_GENERATING_FUNCTIONS are distinctive enough that false positives
    are not a realistic concern.
    """
    if col.server_default is None:
        return False
    raw = getattr(col.server_default, "arg", col.server_default)
    normalized = str(raw).lower()
    return any(fn in normalized for fn in _DB_GENERATING_FUNCTIONS)


def _is_db_owned(
    col: Column,
    generated_always: frozenset[tuple[str, str, str]],
    computed: frozenset[tuple[str, str, str]] = frozenset(),
) -> bool:
    """
    True if the database controls this column's value: identity, computed, a
    GENERATED ALWAYS period column, or a value-generating default.

    Computed and GENERATED ALWAYS columns are detected structurally, from the
    sys.columns flags gathered in reflect_schemas() (the `computed` and
    `generated_always` triple sets), NOT from SQLAlchemy's col.computed — because
    col.computed is derived from VIEW-DEFINITION-gated definition text and comes
    back empty under the least-privilege reflection identity. col.computed and a
    value-generating server_default are kept only as a redundant signal that
    fires when VIEW DEFINITION happens to be held. Does NOT detect trigger- or
    procedure-managed columns — those are caught by DB_AUDIT_COLUMNS in _classify.
    """
    key = (col.table.schema, col.table.name, col.name)
    if key in generated_always or key in computed:
        return True
    return (
        getattr(col, "identity", None) is not None
        or getattr(col, "computed", None) is not None
        or _server_default_generates_value(col)
    )


def _classify(
    col: Column,
    generated_always: frozenset[tuple[str, str, str]],
    computed: frozenset[tuple[str, str, str]] = frozenset(),
) -> ColumnKind:
    """
    Classify a column as EXCLUDED, DB_OWNED, or EDITABLE.

    DB_OWNED is reached two ways:
      - Structural (_is_db_owned): identity, computed, a value-generating
        default, or a GENERATED ALWAYS period column. Works regardless of
        column name.
      - Name-based (DB_AUDIT_COLUMNS): the one deliberate exception to
        "decide structurally." SQL Server has no metadata linking a stored
        procedure or trigger to the columns it writes, so audit columns
        populated that way can only be identified by name. Comparison is
        case-insensitive because SQL Server identifiers are.
    """
    if isinstance(col.type, _EXCLUDED_WRITE_TYPES):
        return ColumnKind.EXCLUDED
    if _is_db_owned(col, generated_always, computed) or col.name.lower() in DB_AUDIT_COLUMNS:
        return ColumnKind.DB_OWNED
    return ColumnKind.EDITABLE


# ---------------------------------------------------------------------------
# SQL Server type -> Python type
#
# Order matters: more specific dialect types must precede generic bases or
# isinstance() matches the wrong entry (e.g. BIGINT must precede Integer).
# ---------------------------------------------------------------------------

_TYPE_MAP: list[tuple[type, type]] = [
    (BIGINT, int), (SMALLINT, int), (TINYINT, int), (INTEGER, int),

    (BIT, bool),

    # Exact numeric types -> Decimal to avoid float precision loss.
    (DECIMAL, Decimal), (NUMERIC, Decimal),
    (MONEY, Decimal), (SMALLMONEY, Decimal),

    # Approximate numerics are genuinely floating-point.
    (REAL, float), (FLOAT, float),

    (DATETIMEOFFSET, str),  # Kept as str rather than a tz-aware type.
    (DATETIME2, datetime.datetime),
    (SMALLDATETIME, datetime.datetime),
    (DATETIME, datetime.datetime),
    (DATE, datetime.date),
    (TIME, datetime.time),

    (NVARCHAR, str), (NCHAR, str), (NTEXT, str),
    (VARCHAR, str), (CHAR, str), (TEXT, str),
    (UNIQUEIDENTIFIER, str),

    (JSON, dict),
]


def _python_type(col: Column) -> Optional[type]:
    """
    Closest Python type for a column's SQL type.

    Returns None for types with no fixed shape. Falls back to str for
    anything else unmapped so model-building never raises on an unusual type.
    """
    if isinstance(col.type, _UNREPRESENTABLE_TYPES):
        return None
    for sa_type, py_type in _TYPE_MAP:
        if isinstance(col.type, sa_type):
            return py_type
    logger.debug("No type mapping for %s (%r); defaulting to str", col.name, col.type)
    return str


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ColumnInfo:
    """
    Everything downstream consumers need to know about one column, computed
    once at reflection time so nothing re-inspects raw SQLAlchemy objects.
    """
    name: str
    kind: ColumnKind
    python_type: Optional[type]   # None only for unrepresentable types
    sql_type: str                 # str(col.type), for display/debugging
    nullable: bool
    is_primary_key: bool
    is_audit: bool                # True for columns named in config.DB_AUDIT_COLUMNS
    required_on_create: bool      # Client MUST supply this on create; see _is_required_on_create
    max_length: Optional[int]
    precision: Optional[int]
    scale: Optional[int]
    foreign_key: Optional[tuple[str, str, str]]  # (schema, table, column) or None

    @property
    def is_editable(self) -> bool:
        return self.kind is ColumnKind.EDITABLE

    @property
    def is_numeric(self) -> bool:
        """True for types where range filters make sense."""
        return self.python_type in (int, float, Decimal)

    @property
    def is_text(self) -> bool:
        """True for types where free-text search makes sense."""
        return self.python_type is str


@dataclass(frozen=True)
class TableInfo:
    """
    Everything about one reflected table: columns (in reflection order),
    primary key (in constraint-definition order), and the two Pydantic
    models built from its editable columns.
    """
    schema: str
    name: str
    columns: list[ColumnInfo]
    primary_key: list[str]          # Tables without a PK are skipped entirely.
    display_column: Optional[str]   # Best column for a human-readable row label; see _find_display_column.
    concurrency_token: Optional[str]  # rowversion/TIMESTAMP column name, or None; see _find_concurrency_token.
    sa_table: Table                 # SQLAlchemy Table object for building Core queries.
    create_model: type              # Pydantic model for POST
    update_model: type              # Pydantic model for PUT/PATCH

    @property
    def key(self) -> tuple[str, str]:
        return (self.schema, self.name)

    def column(self, name: str) -> Optional[ColumnInfo]:
        return next((c for c in self.columns if c.name == name), None)


@dataclass(frozen=True)
class ReflectedSchema:
    """
    Immutable snapshot of every configured schema. Tables are keyed by
    (schema, name) so same-named tables in different schemas never collide.
    """
    tables: dict[tuple[str, str], TableInfo] = field(default_factory=dict)

    def get(self, schema: str, table: str) -> Optional[TableInfo]:
        return self.tables.get((schema, table))

    def schemas(self) -> list[str]:
        return sorted({schema for schema, _ in self.tables})

    def tables_in(self, schema: str) -> list[TableInfo]:
        return sorted(
            (t for t in self.tables.values() if t.schema == schema),
            key=lambda t: t.name,
        )


# ---------------------------------------------------------------------------
# Temporal history tables — excluded entirely, detected structurally.
# ---------------------------------------------------------------------------

def _history_table_keys(conn, schemas: list[str]) -> set[tuple[str, str]]:
    """
    Return (schema, table) pairs for temporal history tables (temporal_type=1).
    Detected structurally so custom history table names are handled correctly.
    """
    stmt = text("""
        SELECT s.name, t.name
        FROM   sys.tables  t
        JOIN   sys.schemas s ON t.schema_id = s.schema_id
        WHERE  t.temporal_type = 1
          AND  s.name IN :schemas
    """).bindparams(bindparam("schemas", expanding=True))

    rows = conn.execute(stmt, {"schemas": schemas})
    return {(schema, table) for schema, table in rows}


@dataclass(frozen=True)
class _ColumnFlags:
    """
    Per-column structural facts read straight from sys.columns, each a set of
    (schema, table, column) triples.

    These exist because SQLAlchemy derives the same facts from object DEFINITION
    text — sys.computed_columns.definition for computed columns, the default
    constraint's definition for defaults — and that text is hidden without GRANT
    VIEW DEFINITION. Under the least-privilege reflection identity (a plain
    db_datareader), col.computed and col.server_default therefore come back empty
    and the columns get misclassified: a computed column that SQL Server will
    reject on write looks editable, and a NOT NULL column with a default looks
    required. The flags read here — is_computed, generated_always_type,
    default_object_id — are plain sys.columns attributes, visible with only table
    access, so they are correct regardless of VIEW DEFINITION. This is the same
    explicit-sys.* approach the FK, history, and period-column queries already use.

    `defaulted` is deliberately partial: default_object_id reveals THAT a column
    has a default, but the default's text (newid() vs a constant) stays gated, so
    a value-generating default can't be distinguished from a plain one at this
    privilege level — enough to fix required-on-create, not enough to mark the
    column DB_OWNED. See _server_default_generates_value (best-effort, needs the
    text and so only fires when VIEW DEFINITION is held).
    """
    computed: frozenset[tuple[str, str, str]] = frozenset()
    generated_always: frozenset[tuple[str, str, str]] = frozenset()
    defaulted: frozenset[tuple[str, str, str]] = frozenset()


def _column_flags(conn, schemas: list[str]) -> _ColumnFlags:
    """
    Read is_computed / generated_always_type / default_object_id for every column
    in the configured schemas in one pass — the structural source of truth for
    column classification, robust to the reflection identity's privilege.

    GENERATED ALWAYS period columns (ROW START / ROW END) and computed columns are
    both rejected by SQL Server on any explicit write, so both are DB_OWNED.
    """
    stmt = text("""
        SELECT s.name, t.name, c.name,
               c.is_computed, c.generated_always_type, c.default_object_id
        FROM   sys.columns c
        JOIN   sys.tables  t ON c.object_id = t.object_id
        JOIN   sys.schemas s ON t.schema_id = s.schema_id
        WHERE  s.name IN :schemas
    """).bindparams(bindparam("schemas", expanding=True))

    computed: set[tuple[str, str, str]] = set()
    generated_always: set[tuple[str, str, str]] = set()
    defaulted: set[tuple[str, str, str]] = set()
    for sname, tname, cname, is_computed, gen_always, default_obj in conn.execute(stmt, {"schemas": schemas}):
        key = (sname, tname, cname)
        if is_computed:
            computed.add(key)
        if gen_always:              # generated_always_type > 0 for period columns
            generated_always.add(key)
        if default_obj:             # default_object_id != 0 → has a default constraint
            defaulted.add(key)
    return _ColumnFlags(frozenset(computed), frozenset(generated_always), frozenset(defaulted))


def _foreign_key_map(conn, schemas: list[str]) -> dict[tuple[str, str, str], tuple[str, str, str]]:
    """
    Build the FK column map by querying sys.foreign_key_columns directly.

    Read from the system catalog using the same managed identity that reflects
    the tables, so it needs no permission beyond the metadata visibility the
    identity already has on those tables — in particular it does NOT require
    GRANT VIEW DEFINITION, which SQLAlchemy's own FK reflection would. This is
    the same explicit-sys.* approach used for history tables and GENERATED
    ALWAYS columns, so all reflected metadata comes from one consistent source.

    Only foreign keys whose REFERENCING column lives in a configured schema
    are returned (matching how tables are reflected). The referenced table may
    live in another schema; its real schema is taken from the catalog, never
    assumed.

    Keyed by (schema, table, column) of the referencing column.
    Value is (schema, table, column) of the referenced column.
    """
    stmt = text("""
        SELECT
            sch_p.name, tab_p.name, col_p.name,
            sch_r.name, tab_r.name, col_r.name
        FROM   sys.foreign_key_columns fkc
        JOIN   sys.tables  tab_p ON fkc.parent_object_id     = tab_p.object_id
        JOIN   sys.schemas sch_p ON tab_p.schema_id          = sch_p.schema_id
        JOIN   sys.columns col_p ON fkc.parent_object_id     = col_p.object_id
                                AND fkc.parent_column_id     = col_p.column_id
        JOIN   sys.tables  tab_r ON fkc.referenced_object_id = tab_r.object_id
        JOIN   sys.schemas sch_r ON tab_r.schema_id          = sch_r.schema_id
        JOIN   sys.columns col_r ON fkc.referenced_object_id = col_r.object_id
                                AND fkc.referenced_column_id = col_r.column_id
        WHERE  sch_p.name IN :schemas
    """).bindparams(bindparam("schemas", expanding=True))

    rows = conn.execute(stmt, {"schemas": schemas})
    return {
        (fk_schema, fk_table, fk_column): (ref_schema, ref_table, ref_column)
        for fk_schema, fk_table, fk_column, ref_schema, ref_table, ref_column in rows
    }


# ---------------------------------------------------------------------------
# Pydantic model builders
#
# Both builders consume the already-computed ColumnInfo list so column
# classification happens exactly once (in _build_column_info), not here.
# ---------------------------------------------------------------------------

def _is_auto_generated_pk(
    col: Column,
    pk_column_count: int,
    defaulted: frozenset[tuple[str, str, str]] = frozenset(),
) -> bool:
    """
    True if the database supplies this PK column's value, meaning it should
    be omitted from the Create model. Manual PKs (no identity/generating
    default) stay in the Create model so the caller can supply the value.

    autoincrement="auto" is SQLAlchemy's default but only actually triggers
    auto-generation for single-column integer PKs, so pk_column_count==1
    guards against incorrectly excluding a column from a composite PK.

    A single-column PK that merely *has* a default constraint (in `defaulted`,
    read structurally from sys.columns) is treated as auto-generated too: under
    the least-privilege reflection identity the default's text is hidden, so a
    NEWID()/sequence default can't be confirmed via _server_default_generates_value
    — but a default on a lone PK means the database can supply it. The same
    pk_column_count==1 guard keeps this from touching composite PKs.
    """
    return (
        col.autoincrement is True
        or (col.autoincrement == "auto" and pk_column_count == 1)
        or getattr(col, "identity", None) is not None
        or _server_default_generates_value(col)
        or (pk_column_count == 1 and (col.table.schema, col.table.name, col.name) in defaulted)
    )


def _is_required_on_create(
    col: Column,
    kind: ColumnKind,
    pk_column_count: int,
    defaulted: frozenset[tuple[str, str, str]] = frozenset(),
) -> bool:
    """
    True if a client MUST supply this column's value when creating a row.

    A column is required on create when it is client-editable, NOT NULL, has
    no default (neither a Python-side nor a server default), and is not an
    auto-generated primary key (those are excluded from the create payload
    entirely and supplied by the database).

    "Has a server default" is decided structurally via `defaulted` (sys.columns
    default_object_id), not col.server_default: the latter is reflected from
    VIEW-DEFINITION-gated text and is empty under the least-privilege identity,
    which would otherwise mark a NOT NULL defaulted column (e.g. IsActive DEFAULT 1,
    or a NEWID() column) as required and force the client to invent a value the
    database was meant to supply. col.server_default stays in the check as a
    redundant signal for when VIEW DEFINITION is held.

    This is the SINGLE definition of "required" used by both the generated
    create model and the metadata endpoint, so the form the frontend renders
    matches what the API actually enforces. Update payloads are always fully
    optional — partial-update semantics — and do not use this.
    """
    if kind is not ColumnKind.EDITABLE:
        return False
    if col.primary_key and _is_auto_generated_pk(col, pk_column_count, defaulted):
        return False
    return (
        not col.nullable
        and col.default is None
        and col.server_default is None
        and (col.table.schema, col.table.name, col.name) not in defaulted
    )


def _annotation(info: ColumnInfo):
    """
    Pydantic field annotation for a column.

    Applies the column's max length to string fields so an over-length value
    is rejected at validation time — with per-field detail in the response —
    instead of round-tripping to the database and coming back as a generic
    constraint violation. This is the one place the API pre-validates on
    behalf of the database; everything semantic (numeric precision/scale,
    CHECK constraints, FK existence) is left to the database, which remains
    the source of truth.
    """
    if info.python_type is str and info.max_length:
        return Annotated[str, Field(max_length=info.max_length)]
    return info.python_type


def _build_create_model(
    schema: str,
    table: Table,
    columns: list[ColumnInfo],
    defaulted: frozenset[tuple[str, str, str]] = frozenset(),
) -> type:
    """
    Pydantic model for POST payloads.

    A field is required if NOT NULL with no default; otherwise Optional.
    Auto-generated PKs are excluded (database supplies the value).
    """
    pk_count = len(list(table.primary_key.columns))
    raw_cols = {c.name: c for c in table.columns}
    fields: dict[str, tuple] = {}

    for info in columns:
        if info.kind is not ColumnKind.EDITABLE:
            continue
        col = raw_cols[info.name]
        if info.is_primary_key and _is_auto_generated_pk(col, pk_count, defaulted):
            continue

        # EDITABLE columns always have a resolved python_type — None is only
        # set for EXCLUDED types, which are filtered out above. If this
        # fires, _classify and _EXCLUDED_WRITE_TYPES have fallen out of sync.
        if info.python_type is None:
            raise RuntimeError(
                f"Column {info.name!r} is EDITABLE but has python_type=None — "
                f"check _classify() and _EXCLUDED_WRITE_TYPES."
            )
        annotation = _annotation(info)
        if info.required_on_create:
            fields[info.name] = (annotation, ...)
        else:
            fields[info.name] = (Optional[annotation], None)

    # create_model's typed overloads don't cover the dynamic **fields form (a
    # known Pydantic typing limitation); the runtime call is correct.
    return create_model(f"{schema}_{table.name}_Create", **fields)  # pyright: ignore[reportCallIssue, reportArgumentType]


def _build_update_model(schema: str, table: Table, columns: list[ColumnInfo]) -> type:
    """
    Pydantic model for PUT/PATCH payloads.

    All fields are Optional[T] = None. Combined with model_dump(exclude_unset=True)
    in the route layer, this lets the client distinguish "omit this field"
    (absent from the dump) from "explicitly clear this field" (present, None).
    PKs are excluded — they're supplied via the URL path, not the body.
    """
    editable = [
        info for info in columns
        if info.kind is ColumnKind.EDITABLE and not info.is_primary_key
    ]
    for info in editable:
        if info.python_type is None:  # Same invariant as _build_create_model.
            raise RuntimeError(
                f"Column {info.name!r} is EDITABLE but has python_type=None — "
                f"check _classify() and _EXCLUDED_WRITE_TYPES."
            )
    fields: dict[str, tuple] = {
        info.name: (Optional[_annotation(info)], None)
        for info in editable
    }
    # See _build_create_model: dynamic **fields isn't covered by the overloads.
    return create_model(f"{schema}_{table.name}_Update", **fields)  # pyright: ignore[reportCallIssue, reportArgumentType]


# ---------------------------------------------------------------------------
# Column / table assembly
# ---------------------------------------------------------------------------

# Keywords that suggest a column is a good human-readable label for a row.
# Checked as substrings against the lowercased column name.
_LABEL_HINTS = ("name", "label", "title", "description", "code")


def _find_display_column(columns: list[ColumnInfo], pk_names: set[str]) -> Optional[str]:
    """
    Find the best column to use as a human-readable label for a row.

    Used by the frontend to display a meaningful value in FK dropdowns and
    list views rather than a raw ID. Returns the column name, or None if
    no suitable column exists (caller falls back to the raw PK value).

    Priority:
      1. Non-PK editable column whose name contains the highest-priority
         display keyword (name > label > title > description > code).
         Hints are checked in order across all columns before moving to
         the next hint — so a column containing "name" always wins over
         one containing "code", regardless of column order in the table.
      2. First non-PK editable string column.
      3. None.
    """
    candidates = [c for c in columns if c.name not in pk_names and c.is_editable]

    for hint in _LABEL_HINTS:
        for col in candidates:
            if hint in col.name.lower():
                return col.name

    for col in candidates:
        if col.is_text:
            return col.name

    return None


def _find_concurrency_token(table: Table) -> Optional[str]:
    """
    Return the name of the table's rowversion/TIMESTAMP column, or None.

    A SQL Server table may have at most one rowversion (a.k.a. TIMESTAMP) column;
    it is an 8-byte binary value the engine bumps automatically on every change to
    the row, which makes it the ideal optimistic-concurrency token. When present,
    update/delete can require the client's expected value (via If-Match) and reject
    the write if the row has changed since it was read (see routes/crud.py). When
    absent, writes fall back to last-writer-wins — protection is purely additive.

    Detected by type (mssql ROWVERSION subclasses TIMESTAMP), not name, so any
    column name works. The column is already EXCLUDED from writes by _classify.
    """
    return next((c.name for c in table.columns if isinstance(c.type, TIMESTAMP)), None)


def _build_column_info(
    col: Column,
    generated_always: frozenset[tuple[str, str, str]],
    fk_map: dict[tuple[str, str, str], tuple[str, str, str]],
    computed: frozenset[tuple[str, str, str]] = frozenset(),
    defaulted: frozenset[tuple[str, str, str]] = frozenset(),
) -> ColumnInfo:
    pk_column_count = len(col.table.primary_key.columns)
    kind = _classify(col, generated_always, computed)
    is_audit = col.name.lower() in DB_AUDIT_COLUMNS
    fk = fk_map.get((col.table.schema or "", col.table.name, col.name))
    return ColumnInfo(
        name=col.name,
        kind=kind,
        python_type=_python_type(col),
        sql_type=str(col.type),
        nullable=bool(col.nullable),
        is_primary_key=col.primary_key,
        is_audit=is_audit,
        required_on_create=_is_required_on_create(col, kind, pk_column_count, defaulted),
        max_length=getattr(col.type, "length", None),
        precision=getattr(col.type, "precision", None),
        scale=getattr(col.type, "scale", None),
        foreign_key=fk,
    )


def _build_table_info(
    schema: str,
    table: Table,
    generated_always: frozenset[tuple[str, str, str]],
    fk_map: dict[tuple[str, str, str], tuple[str, str, str]],
    computed: frozenset[tuple[str, str, str]] = frozenset(),
    defaulted: frozenset[tuple[str, str, str]] = frozenset(),
) -> TableInfo:
    columns = [_build_column_info(c, generated_always, fk_map, computed, defaulted) for c in table.columns]
    pk_names = {c.name for c in table.primary_key.columns}

    # Disable SQLAlchemy's implicit RETURNING (the OUTPUT clause) for this table.
    # SQL Server rejects an OUTPUT clause on any table that has a trigger
    # (error 334), and this app reflects arbitrary schemas where a table may
    # carry triggers — audit triggers populating CreatedBy/ModifiedDate via
    # SUSER_SNAME() being the canonical case. With implicit RETURNING off,
    # SQLAlchemy fetches generated keys via SELECT SCOPE_IDENTITY() instead,
    # which is trigger-safe; result.inserted_primary_key still works, so the
    # create flow that fetches the new row back is unaffected.
    table.implicit_returning = False

    return TableInfo(
        schema=schema,
        name=table.name,
        columns=columns,
        primary_key=[c.name for c in table.primary_key.columns],
        display_column=_find_display_column(columns, pk_names),
        concurrency_token=_find_concurrency_token(table),
        sa_table=table,
        create_model=_build_create_model(schema, table, columns, defaulted),
        update_model=_build_update_model(schema, table, columns),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def reflect_schemas() -> ReflectedSchema:
    """
    Reflect every schema in config.DB_SCHEMAS and return a fresh snapshot.

    Each call builds and discards a local MetaData, so concurrent calls
    cannot corrupt shared state. A request holding an older snapshot simply
    finishes against it — no locking is needed or attempted.

    Reflecting multiple schemas into one MetaData is safe: SQLAlchemy keys
    each table by its fully-qualified name (schema.table) internally, so
    tables from different schemas never collide even if they share a name.

    Tables without a primary key are skipped — the routing layer has no way
    to address individual rows in such a table.
    """
    metadata = MetaData()

    # Acquire the connection through the same transient-fault retry as user
    # connections, rather than reflection_engine.connect() directly. Reflection
    # runs at startup (and on /admin/refresh), so this is a fresh engine's first
    # connect — the cold path — and a serverless database resuming from auto-pause,
    # or a failover, can drop it (Communication link failure, surfaced as
    # ResourceClosedError during the dialect's version probe). Without the retry
    # that aborts startup outright and the container restart-loops until the
    # database is warm. reflection_engine is read at call time so a test that swaps
    # it (integration conftest) is still honoured.
    conn = _connect_with_retry(reflection_engine)
    try:
        for schema in DB_SCHEMAS:
            metadata.reflect(bind=conn, schema=schema)
        history_keys = _history_table_keys(conn, DB_SCHEMAS)
        flags        = _column_flags(conn, DB_SCHEMAS)
        fk_map       = _foreign_key_map(conn, DB_SCHEMAS)
    finally:
        conn.close()

    if not fk_map:
        logger.info("No foreign key relationships found across configured schemas.")

    tables: dict[tuple[str, str], TableInfo] = {}
    skipped_no_pk: list[str] = []

    for table in metadata.tables.values():
        schema = table.schema
        if schema is None:
            # Tables are reflected per explicit schema (metadata.reflect(schema=…)),
            # so schema is always set — this guard just makes that provable to the
            # type checker for the keys and calls below.
            continue
        if (schema, table.name) in history_keys:
            continue
        if not table.primary_key.columns:
            skipped_no_pk.append(f"{schema}.{table.name}")
            continue
        tables[(schema, table.name)] = _build_table_info(
            schema, table, flags.generated_always, fk_map, flags.computed, flags.defaulted
        )

    if skipped_no_pk:
        logger.info("Skipped %d table(s) with no primary key: %s",
                     len(skipped_no_pk), ", ".join(sorted(skipped_no_pk)))
    if history_keys:
        logger.info("Excluded %d temporal history table(s)", len(history_keys))
    if flags.computed:
        logger.info("Detected %d computed column(s)", len(flags.computed))
    if flags.generated_always:
        logger.info("Detected %d GENERATED ALWAYS column(s) across temporal table(s)",
                     len(flags.generated_always))

    logger.info("Reflected %d table(s) across %d schema(s): %s",
                len(tables), len(DB_SCHEMAS), ", ".join(DB_SCHEMAS))

    return ReflectedSchema(tables=tables)
