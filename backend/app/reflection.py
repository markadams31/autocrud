"""
reflection.py — Schema introspection, column classification, and Pydantic
model generation for every configured schema.

Public entry point
------------------
    reflect_schemas() -> SchemaSnapshot

Each call reflects config.DB_SCHEMAS from scratch and returns a brand-new,
immutable SchemaSnapshot. Nothing is mutated in place, so the caller can swap
a new snapshot in while in-flight requests finish against the previous one
(see app.state). Tables are keyed by (schema, name) throughout, so same-named
tables in different schemas never collide.

How facts are gathered — two sources, one boundary
--------------------------------------------------
SQLAlchemy reflection supplies the facts it reads reliably at any privilege
level: column names, types (with lengths/precision), nullability, primary
keys, identity, and MS_Description comments. One additional catalog pass
(_catalog_facts) supplies the facts SQLAlchemy either can't see or can't see
reliably, merged into a single ColumnFacts per column:

  is_computed / is_generated_always   sys.columns flags. SQLAlchemy derives
                                      these from VIEW-DEFINITION-gated
                                      definition text, so they reflect empty
                                      under an identity lacking that grant;
                                      the flags are visible with any object
                                      permission and are always correct.
  has_default / default_text          default_object_id (ungated) plus the
                                      default's definition text (gated —
                                      None without VIEW DEFINITION, which
                                      degrades classification gracefully:
                                      a value-generating default can't be
                                      told from a constant one, so the
                                      column stays editable-and-optional).
  foreign_key                         sys.foreign_key_columns: one set-based
                                      query for all schemas, keyed exactly
                                      as the snapshot needs.

Every classification decision reads each fact from exactly one source —
there are no fallbacks consulting SQLAlchemy's gated attributes.

The reflection identity needs only GRANT VIEW DEFINITION (database scope) —
not db_datareader; reflection reads metadata, never rows. Validated against
SQL Server 2025: a VIEW-DEFINITION-only login reflects with full parity to
sysadmin (the integration matrix re-runs under one). VIEW DEFINITION also
keeps alias-UDT columns visible: a user-defined type is its own securable,
and without the grant its columns silently vanish from reflection.

Column classification
---------------------
  EXCLUDED   The column's type can't be accepted in a write payload:
             binary/rowversion, XML, sql_variant, and the opaque registered
             types (vector, geometry, geography). Surfaced read-only.
  DB_OWNED   The database controls the value: identity, computed, GENERATED
             ALWAYS period columns, a value-generating default (newid(),
             sysutcdatetime(), ...), or a name listed in
             config.DB_AUDIT_COLUMNS (trigger-populated audit columns have
             no structural signature — SQL Server exposes no link between a
             trigger and the columns it writes).
  EDITABLE   Everything else; appears in the generated Create/Update models.

Read/write policy is decided here, once
---------------------------------------
ColumnInfo carries the decisions the routes act on, so no consumer
re-inspects SQLAlchemy types at request time:

  searchable     free-text LIKE is valid against this column (real string
                 types only — LIKE on xml/json/binary raises server-side).
  filterable     value comparisons are valid (excludes binary and the types
                 with no comparable Python value).
  read_as_text   SELECTs must CAST this column to NVARCHAR: the driver
                 returns raw CLR bytes for hierarchyid/geometry/geography
                 and sql_variant has no fixed JSON shape; the CAST yields
                 WKT / the path string / the value as text
                 (see routes/crud._read_columns).

Types the mssql dialect doesn't model (SQL Server 2025 json/vector, the CLR
types) are registered into the dialect's reflection map by app.mssql_types,
so they arrive here as real named types and classify through the same
isinstance machinery as every built-in.

DECIMAL/NUMERIC/MONEY/SMALLMONEY map to Python Decimal, never float, to
avoid silent precision loss on money values.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from functools import cached_property
from typing import Annotated, NamedTuple, Optional

from pydantic import BaseModel, Field, create_model
from sqlalchemy import MetaData, Table, bindparam, text
from sqlalchemy.sql.schema import Column
from sqlalchemy.dialects.mssql import (
    BIGINT, BINARY, BIT, CHAR, DATE, DATETIME, DATETIME2,
    DATETIMEOFFSET, DECIMAL, FLOAT, IMAGE, INTEGER, MONEY,
    NCHAR, NTEXT, NUMERIC, NVARCHAR, REAL, SMALLDATETIME,
    SMALLINT, SMALLMONEY, SQL_VARIANT, TEXT, TIME, TIMESTAMP,
    TINYINT, UNIQUEIDENTIFIER, VARBINARY, VARCHAR, XML,
)

from app.config import DB_SCHEMAS, DB_AUDIT_COLUMNS
from app.connection import reflection_engine, _connect_with_retry
# Importing app.mssql_types registers VECTOR/GEOMETRY/GEOGRAPHY/HIERARCHYID/
# JSON into the mssql dialect's reflection map before the first
# metadata.reflect(), so those columns arrive as real named types.
from app.mssql_types import VECTOR, GEOMETRY, GEOGRAPHY, HIERARCHYID

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Classification vocabulary
# ---------------------------------------------------------------------------

class ColumnKind(Enum):
    """Who may write this column."""
    EDITABLE = auto()  # Client-writable; appears in Create/Update models.
    DB_OWNED = auto()  # Database controls the value; never client-writable.
    EXCLUDED = auto()  # Type not accepted in writes; read-only in metadata.


# SQL Server functions that generate a value server-side. A default whose text
# contains one of these makes the column DB-owned; a plain value default
# (DEFAULT 0) does not — the client may still override it. Matched by substring
# because defaults reflect in varying wrappings: ((sysutcdatetime())),
# (newid()), ([dbo].[fn]()).
_VALUE_GENERATING_FUNCTIONS = (
    "sysutcdatetime", "sysdatetime", "sysdatetimeoffset",
    "getdate", "getutcdate", "current_timestamp",
    "newid", "newsequentialid",
)

# Types never accepted in a write payload.
_EXCLUDED_WRITE_TYPES = (
    VARBINARY, BINARY, IMAGE, TIMESTAMP,   # binary / rowversion
    XML,                                    # structured-but-opaque
    SQL_VARIANT,                            # no fixed shape
    VECTOR, GEOMETRY, GEOGRAPHY,            # machine-generated / spatial
)

# Types the read path must CAST to NVARCHAR: the driver returns raw CLR bytes
# for the CLR UDTs, and sql_variant has no fixed JSON shape. The CAST yields
# WKT for spatial, the path string for hierarchyid, the value as text for
# sql_variant (see routes/crud._read_columns).
_READ_AS_TEXT_TYPES = (HIERARCHYID, GEOMETRY, GEOGRAPHY, SQL_VARIANT)

# Genuine string types — the only ones a free-text LIKE may run against.
# LIKE against xml raises server-side (error 8116), and binary "text" isn't
# text at all, so searchability is an explicit allowlist.
_SEARCHABLE_TYPES = (NVARCHAR, NCHAR, NTEXT, VARCHAR, CHAR, TEXT)


# ---------------------------------------------------------------------------
# Catalog facts — the one extra pass beyond SQLAlchemy reflection
# ---------------------------------------------------------------------------

class ForeignKeyRef(NamedTuple):
    """The referenced side of a foreign key, as (schema, table, column)."""
    schema: str
    table: str
    column: str


@dataclass(frozen=True)
class ColumnFacts:
    """
    Per-column catalog facts SQLAlchemy reflection can't supply reliably.
    See the module docstring for why each is read from sys.* directly.
    """
    is_computed: bool = False
    is_generated_always: bool = False
    has_default: bool = False
    default_text: Optional[str] = None     # None without VIEW DEFINITION
    foreign_key: Optional[ForeignKeyRef] = None

    @property
    def generates_value(self) -> bool:
        """True if the default calls a value-generating function. Needs the
        gated default text; False when it wasn't readable — the column then
        stays editable-and-optional rather than DB-owned."""
        if not self.default_text:
            return False
        lowered = self.default_text.lower()
        return any(fn in lowered for fn in _VALUE_GENERATING_FUNCTIONS)


_NO_FACTS = ColumnFacts()


class CatalogFacts(dict):
    """ColumnFacts by (schema, table, column); missing columns get defaults."""

    def for_column(self, col: Column) -> ColumnFacts:
        return self.get((col.table.schema, col.table.name, col.name), _NO_FACTS)


def _catalog_facts(conn, schemas: list[str]) -> CatalogFacts:
    """
    Build the per-column facts map in two set-based queries: one over
    sys.columns (flags + the default's gated definition text), one over
    sys.foreign_key_columns. Only FKs whose referencing column lives in a
    configured schema are recorded; the referenced side's schema/table come
    from the catalog and may point outside the configured set (consumers
    degrade such links gracefully).
    """
    flags_stmt = text("""
        SELECT s.name, t.name, c.name,
               c.is_computed, c.generated_always_type, c.default_object_id,
               dc.definition
        FROM   sys.columns c
        JOIN   sys.tables  t ON c.object_id = t.object_id
        JOIN   sys.schemas s ON t.schema_id = s.schema_id
        LEFT   JOIN sys.default_constraints dc
                 ON dc.object_id = c.default_object_id
        WHERE  s.name IN :schemas
    """).bindparams(bindparam("schemas", expanding=True))

    fk_stmt = text("""
        SELECT sch_p.name, tab_p.name, col_p.name,
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

    fks: dict[tuple[str, str, str], ForeignKeyRef] = {
        (s, t, c): ForeignKeyRef(rs, rt, rc)
        for s, t, c, rs, rt, rc in conn.execute(fk_stmt, {"schemas": schemas})
    }

    facts = CatalogFacts()
    for s, t, c, computed, gen_always, default_obj, default_text in conn.execute(
        flags_stmt, {"schemas": schemas}
    ):
        key = (s, t, c)
        facts[key] = ColumnFacts(
            is_computed=bool(computed),
            is_generated_always=bool(gen_always),   # > 0 for period columns
            has_default=bool(default_obj),          # != 0 → default constraint
            default_text=default_text,              # None when gated or absent
            foreign_key=fks.get(key),
        )
    return facts


def _history_table_keys(conn, schemas: list[str]) -> set[tuple[str, str]]:
    """
    (schema, table) pairs for temporal history tables (temporal_type = 1),
    detected structurally so custom history-table names are handled. Excluded
    from the snapshot entirely: SQL Server rejects direct writes to them, and
    row history is a read veneer over the main table.
    """
    stmt = text("""
        SELECT s.name, t.name
        FROM   sys.tables  t
        JOIN   sys.schemas s ON t.schema_id = s.schema_id
        WHERE  t.temporal_type = 1
          AND  s.name IN :schemas
    """).bindparams(bindparam("schemas", expanding=True))
    return {(s, t) for s, t in conn.execute(stmt, {"schemas": schemas})}


# ---------------------------------------------------------------------------
# Per-column decisions — pure functions of (SQLAlchemy column, ColumnFacts)
# ---------------------------------------------------------------------------

def _classify(col: Column, facts: ColumnFacts) -> ColumnKind:
    """EXCLUDED (by type) wins over everything; then DB-owned; else editable."""
    if isinstance(col.type, _EXCLUDED_WRITE_TYPES):
        return ColumnKind.EXCLUDED
    if (
        col.identity is not None            # identity: ungated, SQLAlchemy-reliable
        or facts.is_computed
        or facts.is_generated_always
        or facts.generates_value
        or col.name.lower() in DB_AUDIT_COLUMNS
    ):
        return ColumnKind.DB_OWNED
    return ColumnKind.EDITABLE


def _is_auto_generated_pk(col: Column, facts: ColumnFacts, pk_column_count: int) -> bool:
    """
    True if the database supplies this PK column's value, so it is omitted
    from the Create model. Identity is the ordinary case. A *single-column*
    PK with any default constraint counts too — the database can supply it
    (NEWID()/sequence), and even when the default's text is privilege-hidden,
    has_default alone is enough to know the client may omit it. Manual PKs
    (no identity, no default) stay in the Create model, required.
    """
    return (
        col.identity is not None
        or (pk_column_count == 1 and facts.has_default)
    )


def _is_required_on_create(
    col: Column, kind: ColumnKind, facts: ColumnFacts, pk_column_count: int
) -> bool:
    """
    The single definition of "the client MUST supply this on create", used by
    both the generated Create model and the metadata endpoint so the form the
    frontend renders matches what the API enforces: editable, NOT NULL, no
    default, and not a database-supplied PK. Update payloads are always fully
    optional (partial-update semantics) and don't use this.
    """
    if kind is not ColumnKind.EDITABLE:
        return False
    if col.primary_key and _is_auto_generated_pk(col, facts, pk_column_count):
        return False
    return not col.nullable and not facts.has_default


_TYPE_MAP: list[tuple[type, type]] = [
    (BIGINT, int), (SMALLINT, int), (TINYINT, int), (INTEGER, int),

    (BIT, bool),

    # Exact numerics -> Decimal to avoid float precision loss on money values.
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
]


def _python_type(col: Column) -> type:
    """
    Closest Python type for the column's SQL type; str for anything unmapped
    (opaque and registered types included) so model-building never raises.
    Order in _TYPE_MAP matters: specific dialect types precede generic bases
    or isinstance() would match the wrong entry.
    """
    for sa_type, py_type in _TYPE_MAP:
        if isinstance(col.type, sa_type):
            return py_type
    return str


def _max_length(col: Column) -> Optional[int]:
    """
    The column's usable character limit, or None where there isn't one.
    Legacy TEXT/NTEXT need the exception: their reflected "length" is the
    16-byte LOB pointer (8 after the ntext halving), not a real limit —
    surfacing it would make the generated models reject valid input.
    """
    if isinstance(col.type, (TEXT, NTEXT)):
        return None
    return getattr(col.type, "length", None)


def _is_filterable(col: Column) -> bool:
    """
    True where value comparisons (=, <, BETWEEN, IN, LIKE) are meaningful.
    Binary and rowversion compare as bytes (useless through JSON); the
    registered opaque types (CLR/vector/json) have no comparable Python value
    (python_type raises); xml needs naming explicitly — it subclasses Text so
    it *looks* comparable, but the server rejects every operator against it
    (error 8116). Rejecting here turns those into a clean client 400 without
    a database round-trip.
    """
    if isinstance(col.type, XML):
        return False
    try:
        py = col.type.python_type
    except NotImplementedError:  # SQLAlchemy < 2.1 raised for opaque types...
        return False
    return py not in (bytes, object)  # ...2.1 returns `object` instead


# ---------------------------------------------------------------------------
# Snapshot shapes — everything downstream consumers read
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ColumnInfo:
    """
    Everything the routes need to know about one column, decided once at
    reflection time. No consumer re-derives facts from SQLAlchemy objects.
    """
    name: str
    kind: ColumnKind
    python_type: type            # str for unmapped/opaque types
    sql_type: str                # str(col.type), for display
    nullable: bool
    is_primary_key: bool
    is_audit: bool               # named in config.DB_AUDIT_COLUMNS
    required_on_create: bool
    searchable: bool             # free-text LIKE is valid against this column
    filterable: bool             # value comparisons are valid
    read_as_text: bool           # reads CAST it to NVARCHAR (CLR/sql_variant)
    comment: Optional[str]       # MS_Description extended property
    max_length: Optional[int]
    precision: Optional[int]
    scale: Optional[int]
    foreign_key: Optional[ForeignKeyRef]

    @property
    def is_editable(self) -> bool:
        return self.kind is ColumnKind.EDITABLE


@dataclass(frozen=True)
class TableInfo:
    """One reflected table: columns in reflection order, PK in constraint
    order, and the two Pydantic models generated from its editable columns."""
    schema: str
    name: str
    columns: tuple[ColumnInfo, ...]
    primary_key: tuple[str, ...]      # Tables without a PK are never reflected.
    display_column: Optional[str]     # Best human-readable row label, or None.
    concurrency_token: Optional[str]  # rowversion column name, or None.
    sa_table: Table                   # For building Core queries.
    create_model: type[BaseModel]     # POST payload validation.
    update_model: type[BaseModel]     # PUT/PATCH payload validation.

    @property
    def key(self) -> tuple[str, str]:
        return (self.schema, self.name)

    @cached_property
    def _columns_by_name(self) -> dict[str, ColumnInfo]:
        return {c.name: c for c in self.columns}

    def column(self, name: str) -> Optional[ColumnInfo]:
        return self._columns_by_name.get(name)


@dataclass(frozen=True)
class SchemaSnapshot:
    """
    Immutable snapshot of every configured schema, plus when it was taken
    (surfaced by /admin/refresh so operators can see snapshot age).
    """
    tables: dict[tuple[str, str], TableInfo] = field(default_factory=dict)
    reflected_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

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
# Pydantic model generation
# ---------------------------------------------------------------------------

def _annotation(info: ColumnInfo):
    """
    Field annotation for a column. Max length is enforced at validation time
    (with per-field detail) rather than round-tripping to the database as a
    truncation error; everything semantic — precision, CHECK rules, FK
    existence — is left to the database, which stays the source of truth.
    """
    if info.python_type is str and info.max_length:
        return Annotated[str, Field(max_length=info.max_length)]
    return info.python_type


def _build_models(
    schema: str, table: Table, columns: list[ColumnInfo], facts: CatalogFacts
) -> tuple[type[BaseModel], type[BaseModel]]:
    """
    The Create and Update models for a table, from its editable columns.

    Create: fields required per required_on_create; database-supplied PKs
    omitted entirely (manual PKs stay, required). Update: every editable
    non-PK field is Optional[T] = None — combined with
    model_dump(exclude_unset=True) in the routes, a client can distinguish
    "omit this field" from "explicitly clear it".
    """
    pk_count = len(table.primary_key.columns)

    create_fields: dict[str, tuple] = {}
    update_fields: dict[str, tuple] = {}
    for info in columns:
        if not info.is_editable:
            continue
        annotation = _annotation(info)
        if not info.is_primary_key:
            update_fields[info.name] = (Optional[annotation], None)
        col = table.columns[info.name]
        if info.is_primary_key and _is_auto_generated_pk(col, facts.for_column(col), pk_count):
            continue
        if info.required_on_create:
            create_fields[info.name] = (annotation, ...)
        else:
            create_fields[info.name] = (Optional[annotation], None)

    # create_model's typed overloads don't cover the dynamic **fields form (a
    # known Pydantic typing limitation); the runtime calls are correct.
    return (
        create_model(f"{schema}_{table.name}_Create", **create_fields),  # pyright: ignore[reportCallIssue, reportArgumentType]
        create_model(f"{schema}_{table.name}_Update", **update_fields),  # pyright: ignore[reportCallIssue, reportArgumentType]
    )


# ---------------------------------------------------------------------------
# Table assembly
# ---------------------------------------------------------------------------

# Substrings that suggest a column is a good human-readable row label, in
# priority order: a column containing "name" beats one containing "code"
# regardless of column order in the table.
_LABEL_HINTS = ("name", "label", "title", "description", "code")


def _find_display_column(columns: list[ColumnInfo]) -> Optional[str]:
    """
    The best column to label a row with in FK dropdowns and list views, or
    None (callers fall back to the raw PK value): highest-priority label hint
    first, then the first editable string column.
    """
    candidates = [c for c in columns if c.is_editable and not c.is_primary_key]
    for hint in _LABEL_HINTS:
        for col in candidates:
            if hint in col.name.lower():
                return col.name
    for col in candidates:
        if col.python_type is str:
            return col.name
    return None


def _find_concurrency_token(table: Table) -> Optional[str]:
    """
    The table's rowversion column name, or None. SQL Server allows at most one
    rowversion per table; the engine bumps it on every write, which makes it
    the optimistic-concurrency token the If-Match flow checks (routes/crud.py).
    Detected by type — any column name works.
    """
    return next((c.name for c in table.columns if isinstance(c.type, TIMESTAMP)), None)


def _build_column_info(col: Column, facts: ColumnFacts, pk_column_count: int) -> ColumnInfo:
    kind = _classify(col, facts)
    return ColumnInfo(
        name=col.name,
        kind=kind,
        python_type=_python_type(col),
        sql_type=str(col.type),
        nullable=bool(col.nullable),
        is_primary_key=col.primary_key,
        is_audit=col.name.lower() in DB_AUDIT_COLUMNS,
        required_on_create=_is_required_on_create(col, kind, facts, pk_column_count),
        searchable=isinstance(col.type, _SEARCHABLE_TYPES),
        filterable=_is_filterable(col),
        read_as_text=isinstance(col.type, _READ_AS_TEXT_TYPES),
        comment=col.comment,
        max_length=_max_length(col),
        precision=getattr(col.type, "precision", None),
        scale=getattr(col.type, "scale", None),
        foreign_key=facts.foreign_key,
    )


def _build_table_info(
    table: Table, facts: CatalogFacts, *, schema: Optional[str] = None
) -> TableInfo:
    # `schema` overrides the snapshot key only for test harnesses that run
    # hand-built tables against a schemaless engine; reflected tables always
    # carry their real schema (reflect_schemas reflects per explicit schema).
    schema = schema or table.schema
    assert schema is not None

    # SQL Server rejects an OUTPUT clause on any table with a trigger (error
    # 334), and reflected schemas may carry triggers (audit triggers being the
    # canonical case). With implicit RETURNING off, SQLAlchemy fetches
    # generated keys via SCOPE_IDENTITY() instead, which is trigger-safe.
    table.implicit_returning = False

    pk_count = len(table.primary_key.columns)
    columns = [
        _build_column_info(col, facts.for_column(col), pk_count)
        for col in table.columns
    ]
    created, updated = _build_models(schema, table, columns, facts)

    return TableInfo(
        schema=schema,
        name=table.name,
        columns=tuple(columns),
        primary_key=tuple(c.name for c in table.primary_key.columns),
        display_column=_find_display_column(columns),
        concurrency_token=_find_concurrency_token(table),
        sa_table=table,
        create_model=created,
        update_model=updated,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def reflect_schemas() -> SchemaSnapshot:
    """
    Reflect every schema in config.DB_SCHEMAS and return a fresh snapshot.

    Each call builds and discards a local MetaData, so concurrent calls can't
    corrupt shared state. Tables without a primary key are skipped — the
    routes have no way to address individual rows in them. Temporal history
    tables are excluded entirely.

    The connection goes through the shared transient-fault retry: reflection
    runs at startup (and on /admin/refresh), which is a fresh engine's first,
    cold connect — a serverless database resuming from auto-pause can drop
    it, and without the retry that aborts startup outright. reflection_engine
    is read at call time so tests can substitute it.
    """
    metadata = MetaData()

    conn = _connect_with_retry(reflection_engine)
    try:
        for schema in DB_SCHEMAS:
            # resolve_fks=False: the default recursively reflects every
            # FK-referenced table — including tables in schemas outside
            # DB_SCHEMAS, which must never enter the snapshot (their catalog
            # facts wouldn't be gathered and they'd misclassify). Cross-table
            # links come from _catalog_facts, not SQLAlchemy FK objects.
            metadata.reflect(bind=conn, schema=schema, resolve_fks=False)
        history_keys = _history_table_keys(conn, DB_SCHEMAS)
        facts = _catalog_facts(conn, DB_SCHEMAS)
    finally:
        conn.close()

    tables: dict[tuple[str, str], TableInfo] = {}
    skipped_no_pk: list[str] = []
    for table in metadata.tables.values():
        schema = table.schema
        if schema is None or schema not in DB_SCHEMAS:
            continue  # belt-and-braces with resolve_fks=False above
        if (schema, table.name) in history_keys:
            continue
        if not table.primary_key.columns:
            skipped_no_pk.append(f"{schema}.{table.name}")
            continue
        tables[(schema, table.name)] = _build_table_info(table, facts)

    if skipped_no_pk:
        logger.info("Skipped %d table(s) with no primary key: %s",
                    len(skipped_no_pk), ", ".join(sorted(skipped_no_pk)))
    if history_keys:
        logger.info("Excluded %d temporal history table(s)", len(history_keys))
    logger.info("Reflected %d table(s) across %d schema(s): %s",
                len(tables), len(DB_SCHEMAS), ", ".join(DB_SCHEMAS))

    return SchemaSnapshot(tables=tables)
