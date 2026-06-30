"""
Column classification — the core reflection logic, exercised against every
mssql dialect type and every database-owned mechanism without a database.

A column is classified EDITABLE (client may write), DB_OWNED (database controls
the value), or EXCLUDED (type not safely writable). See reflection._classify.
"""

import pytest
from sqlalchemy import Column, Computed, Identity, MetaData, Table, text
from sqlalchemy.dialects.mssql import (
    BIGINT, BINARY, BIT, CHAR, DATE, DATETIME, DATETIME2, DATETIMEOFFSET,
    DECIMAL, FLOAT, IMAGE, INTEGER, MONEY, NCHAR, NTEXT, NUMERIC, NVARCHAR,
    REAL, SMALLDATETIME, SMALLINT, SMALLMONEY, SQL_VARIANT, TEXT, TIME,
    TIMESTAMP, TINYINT, UNIQUEIDENTIFIER, VARBINARY, VARCHAR, XML,
)

from app.reflection import (
    ColumnKind,
    _classify,
    _find_concurrency_token,
    _is_db_owned,
    _server_default_generates_value,
)


def make_column(name, *args, **kw):
    """Return a single bound Column (it needs a parent table for .table)."""
    md = MetaData()
    t = Table("T", md, Column(name, *args, **kw), schema="dbo")
    return t.c[name]


# ── Plain, writable types → EDITABLE ─────────────────────────────────────────

EDITABLE_TYPES = [
    BIGINT(), INTEGER(), SMALLINT(), TINYINT(),
    BIT(),
    DECIMAL(18, 2), NUMERIC(10, 4), MONEY(), SMALLMONEY(),
    REAL(), FLOAT(),
    DATETIME2(), DATETIME(), SMALLDATETIME(), DATE(), TIME(), DATETIMEOFFSET(),
    NVARCHAR(50), NCHAR(10), NTEXT(), VARCHAR(50), CHAR(10), TEXT(),
    UNIQUEIDENTIFIER(),
]


@pytest.mark.parametrize("sa_type", EDITABLE_TYPES, ids=lambda t: type(t).__name__)
def test_plain_types_are_editable(sa_type):
    col = make_column("Field", sa_type)
    assert _classify(col, set()) is ColumnKind.EDITABLE


# ── Types unsafe to write → EXCLUDED ─────────────────────────────────────────

EXCLUDED_TYPES = [VARBINARY(), BINARY(), IMAGE(), TIMESTAMP(), XML(), SQL_VARIANT()]


@pytest.mark.parametrize("sa_type", EXCLUDED_TYPES, ids=lambda t: type(t).__name__)
def test_unsupported_types_are_excluded(sa_type):
    col = make_column("Field", sa_type)
    assert _classify(col, set()) is ColumnKind.EXCLUDED


# ── Concurrency token (rowversion) detection ─────────────────────────────────

def test_rowversion_is_detected_as_concurrency_token():
    md = MetaData()
    t = Table(
        "Doc", md,
        Column("DocID", INTEGER(), primary_key=True),
        Column("Title", NVARCHAR(100)),
        Column("RowVersion", TIMESTAMP()),
        schema="dbo",
    )
    assert _find_concurrency_token(t) == "RowVersion"


def test_table_without_rowversion_has_no_token():
    md = MetaData()
    t = Table(
        "Plain", md,
        Column("ID", INTEGER(), primary_key=True),
        Column("Name", NVARCHAR(50)),
        schema="dbo",
    )
    assert _find_concurrency_token(t) is None


# ── Structurally database-owned ──────────────────────────────────────────────

def test_identity_is_db_owned():
    col = make_column("ID", INTEGER(), Identity(), primary_key=True)
    assert _classify(col, set()) is ColumnKind.DB_OWNED


def test_computed_column_is_db_owned():
    col = make_column("FullName", NVARCHAR(101), Computed("[First] + ' ' + [Last]"))
    assert _classify(col, set()) is ColumnKind.DB_OWNED


def test_computed_persisted_column_is_db_owned():
    col = make_column("Total", DECIMAL(18, 2), Computed("[Qty] * [Price]", persisted=True))
    assert _classify(col, set()) is ColumnKind.DB_OWNED


def test_computed_detected_structurally_without_reflection():
    # A least-privilege reflection identity can't see the computed definition, so
    # SQLAlchemy leaves col.computed empty. The structural is_computed flag
    # (passed in `computed`, read from sys.columns) must still classify DB_OWNED —
    # this is the path that fixes computed columns in production.
    col = make_column("FullName", NVARCHAR(101))  # NOT marked Computed() — reflection blind
    computed = frozenset({("dbo", "T", "FullName")})
    assert _is_db_owned(col, set(), computed) is True
    assert _classify(col, set(), computed) is ColumnKind.DB_OWNED


def test_value_generating_default_stays_editable_when_text_is_hidden():
    # The irreducible gap: without VIEW DEFINITION the default's text is gated, so
    # a NEWID() default can't be proven value-generating and the column degrades
    # to EDITABLE rather than DB_OWNED. (It is still kept out of "required" via the
    # structural default flag — see test_structurally_defaulted_column_is_optional.)
    col = make_column("ExternalRef", UNIQUEIDENTIFIER(), nullable=False)  # text not reflected
    assert _server_default_generates_value(col) is False
    assert _classify(col, set()) is ColumnKind.EDITABLE


@pytest.mark.parametrize(
    "default_sql",
    [
        "sysutcdatetime()", "(sysutcdatetime())", "((sysutcdatetime()))",
        "sysdatetime()", "sysdatetimeoffset()",
        "getdate()", "getutcdate()", "current_timestamp",
        "newid()", "newsequentialid()",
    ],
)
def test_value_generating_default_is_db_owned(default_sql):
    col = make_column("Stamp", DATETIME2(), server_default=text(default_sql))
    assert _server_default_generates_value(col) is True
    assert _classify(col, set()) is ColumnKind.DB_OWNED


@pytest.mark.parametrize("default_sql", ["0", "((0))", "'pending'", "(N'x')", "-1"])
def test_plain_value_default_stays_editable(default_sql):
    # A constant default does NOT make a column database-owned — the client may
    # still override it.
    col = make_column("Status", INTEGER(), server_default=text(default_sql))
    assert _server_default_generates_value(col) is False
    assert _classify(col, set()) is ColumnKind.EDITABLE


def test_no_server_default_is_not_generating():
    col = make_column("Plain", INTEGER())
    assert _server_default_generates_value(col) is False


# ── GENERATED ALWAYS period columns (passed in from sys.columns) ─────────────

def test_generated_always_period_column_is_db_owned():
    col = make_column("ValidFrom", DATETIME2())
    generated_always = {("dbo", "T", "ValidFrom")}
    assert _is_db_owned(col, generated_always) is True
    assert _classify(col, generated_always) is ColumnKind.DB_OWNED


def test_generated_always_only_matches_named_triple():
    col = make_column("ValidFrom", DATETIME2())
    # Same column name, different table → must NOT match.
    assert _is_db_owned(col, {("dbo", "Other", "ValidFrom")}) is False


# ── Name-based (audit) classification ────────────────────────────────────────
# DB_AUDIT_COLUMNS is set to CreatedBy/CreatedDate/ModifiedBy/ModifiedDate in
# conftest, and matched case-insensitively.

@pytest.mark.parametrize("name", ["CreatedBy", "createdby", "CREATEDDATE", "ModifiedBy", "modifieddate"])
def test_audit_named_column_is_db_owned(name):
    col = make_column(name, NVARCHAR(128))
    assert _classify(col, set()) is ColumnKind.DB_OWNED


def test_non_audit_name_stays_editable():
    col = make_column("Notes", NVARCHAR(400))
    assert _classify(col, set()) is ColumnKind.EDITABLE


def test_audit_classification_honours_runtime_set(monkeypatch):
    # _classify reads the module-global DB_AUDIT_COLUMNS, so a deployment with
    # no audit columns leaves an otherwise-plain column editable.
    monkeypatch.setattr("app.reflection.DB_AUDIT_COLUMNS", set())
    col = make_column("CreatedBy", NVARCHAR(128))
    assert _classify(col, set()) is ColumnKind.EDITABLE


# ── Precedence: EXCLUDED type wins even if named like an audit column ─────────

def test_excluded_type_takes_precedence_over_audit_name():
    col = make_column("CreatedBy", VARBINARY())
    assert _classify(col, set()) is ColumnKind.EXCLUDED
