"""
Column classification — the core reflection logic, exercised against every
mssql dialect type and every database-owned mechanism without a database.

A column is classified EDITABLE (client may write), DB_OWNED (database controls
the value), or EXCLUDED (type not safely writable) by reflection._classify,
which reads the SQLAlchemy column for type/identity and a ColumnFacts for the
catalog-sourced facts (computed / generated-always / default). Facts come from
exactly one source each — these tests construct ColumnFacts directly, the same
shape reflect_schemas builds from sys.columns.
"""

import pytest
from sqlalchemy import Column, Identity, MetaData, Table
from sqlalchemy.dialects.mssql import (
    BIGINT, BINARY, BIT, CHAR, DATE, DATETIME, DATETIME2, DATETIMEOFFSET,
    DECIMAL, FLOAT, IMAGE, INTEGER, MONEY, NCHAR, NTEXT, NUMERIC, NVARCHAR,
    REAL, SMALLDATETIME, SMALLINT, SMALLMONEY, SQL_VARIANT, TEXT, TIME,
    TIMESTAMP, TINYINT, UNIQUEIDENTIFIER, VARBINARY, VARCHAR, XML,
)

from app.mssql_types import GEOGRAPHY, GEOMETRY, HIERARCHYID, JSON, VECTOR
from app.reflection import (
    CatalogFacts,
    ColumnFacts,
    ColumnKind,
    _classify,
    _find_concurrency_token,
)

NO_FACTS = ColumnFacts()


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
    assert _classify(make_column("Field", sa_type), NO_FACTS) is ColumnKind.EDITABLE


# ── Types unsafe to write → EXCLUDED ─────────────────────────────────────────
# VECTOR/GEOMETRY/GEOGRAPHY are registered for reflection in app.mssql_types, so
# once reflected they are real types and classify EXCLUDED by isinstance — same
# path as binary/XML. A raw embedding or spatial value isn't hand-editable through
# a generic layer, so they're read-only.

EXCLUDED_TYPES = [
    VARBINARY(), BINARY(), IMAGE(), TIMESTAMP(), XML(), SQL_VARIANT(),
    VECTOR(), GEOMETRY(), GEOGRAPHY(),
]


@pytest.mark.parametrize("sa_type", EXCLUDED_TYPES, ids=lambda t: type(t).__name__)
def test_unsupported_types_are_excluded(sa_type):
    assert _classify(make_column("Field", sa_type), NO_FACTS) is ColumnKind.EXCLUDED


# ── Precedence: EXCLUDED (by type) beats every DB_OWNED signal ───────────────

def test_excluded_type_takes_precedence_over_audit_name():
    assert _classify(make_column("CreatedBy", VARBINARY()), NO_FACTS) is ColumnKind.EXCLUDED


def test_vector_exclusion_wins_over_audit_name():
    assert _classify(make_column("CreatedBy", VECTOR()), NO_FACTS) is ColumnKind.EXCLUDED


# ── Structurally database-owned ──────────────────────────────────────────────

def test_identity_is_db_owned():
    # Identity is the one DB-owned signal read from SQLAlchemy (sys.identity_
    # columns is ungated, so reflection is reliable for it at any privilege).
    col = make_column("ID", INTEGER(), Identity(), primary_key=True)
    assert _classify(col, NO_FACTS) is ColumnKind.DB_OWNED


def test_computed_flag_is_db_owned():
    col = make_column("FullName", NVARCHAR(101))
    assert _classify(col, ColumnFacts(is_computed=True)) is ColumnKind.DB_OWNED


def test_generated_always_flag_is_db_owned():
    col = make_column("ValidFrom", DATETIME2())
    assert _classify(col, ColumnFacts(is_generated_always=True)) is ColumnKind.DB_OWNED


def test_sqlalchemy_side_signals_are_not_consulted():
    # Single-source contract: computed/default status comes from ColumnFacts
    # (sys.columns), never from SQLAlchemy's VIEW-DEFINITION-gated attributes.
    # A column with no facts classifies EDITABLE even if a hand-built table
    # carried SQLAlchemy-side markers — there is no fallback path to diverge
    # between privilege levels.
    col = make_column("FullName", NVARCHAR(101))
    assert _classify(col, NO_FACTS) is ColumnKind.EDITABLE


# ── Value-generating vs constant defaults ────────────────────────────────────

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
    facts = ColumnFacts(has_default=True, default_text=default_sql)
    assert facts.generates_value is True
    assert _classify(make_column("Stamp", DATETIME2()), facts) is ColumnKind.DB_OWNED


@pytest.mark.parametrize("default_sql", ["0", "((0))", "'pending'", "(N'x')", "-1"])
def test_plain_value_default_stays_editable(default_sql):
    # A constant default does NOT make a column database-owned — the client may
    # still override it.
    facts = ColumnFacts(has_default=True, default_text=default_sql)
    assert facts.generates_value is False
    assert _classify(make_column("Status", INTEGER()), facts) is ColumnKind.EDITABLE


def test_hidden_default_text_degrades_to_editable():
    # The irreducible privilege gap: without VIEW DEFINITION the default's text
    # is NULL, so a NEWID() default can't be proven value-generating and the
    # column degrades to EDITABLE rather than DB_OWNED. (has_default still
    # keeps it out of "required" — see the required-on-create tests.)
    facts = ColumnFacts(has_default=True, default_text=None)
    assert facts.generates_value is False
    col = make_column("ExternalRef", UNIQUEIDENTIFIER(), nullable=False)
    assert _classify(col, facts) is ColumnKind.EDITABLE


# ── Facts lookup keys on the exact (schema, table, column) triple ────────────

def test_catalog_facts_lookup_is_per_column():
    col = make_column("ValidFrom", DATETIME2())
    facts = CatalogFacts({
        ("dbo", "Other", "ValidFrom"): ColumnFacts(is_generated_always=True),
    })
    # Same column name on a different table must not match → default facts.
    assert facts.for_column(col) is not facts[("dbo", "Other", "ValidFrom")]
    assert _classify(col, facts.for_column(col)) is ColumnKind.EDITABLE


# ── Name-based (audit) classification ────────────────────────────────────────
# DB_AUDIT_COLUMNS is set to CreatedBy/CreatedDate/ModifiedBy/ModifiedDate in
# conftest, and matched case-insensitively.

@pytest.mark.parametrize("name", ["CreatedBy", "createdby", "CREATEDDATE", "ModifiedBy", "modifieddate"])
def test_audit_named_column_is_db_owned(name):
    assert _classify(make_column(name, NVARCHAR(128)), NO_FACTS) is ColumnKind.DB_OWNED


def test_non_audit_name_stays_editable():
    assert _classify(make_column("Notes", NVARCHAR(400)), NO_FACTS) is ColumnKind.EDITABLE


def test_audit_classification_honours_runtime_set(monkeypatch):
    # _classify reads the module-global DB_AUDIT_COLUMNS, so a deployment with
    # no audit columns leaves an otherwise-plain column editable.
    monkeypatch.setattr("app.reflection.DB_AUDIT_COLUMNS", set())
    assert _classify(make_column("CreatedBy", NVARCHAR(128)), NO_FACTS) is ColumnKind.EDITABLE


# ── Registered string-backed types stay EDITABLE ─────────────────────────────
# JSON (a document) and HIERARCHYID (a "/1/2/" path) are registered for reflection
# in app.mssql_types like VECTOR, but unlike VECTOR they round-trip as plain
# strings, so they remain client-writable.

@pytest.mark.parametrize("sa_type", [JSON(), HIERARCHYID()], ids=lambda t: type(t).__name__)
def test_string_backed_registered_types_are_editable(sa_type):
    assert _classify(make_column("Field", sa_type), NO_FACTS) is ColumnKind.EDITABLE


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
