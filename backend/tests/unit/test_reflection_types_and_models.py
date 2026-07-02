"""
Type mapping, per-column policy (searchable/filterable/read_as_text),
required-on-create, auto-generated PK detection, the display-column heuristic,
and the generated Pydantic create/update models.

Catalog facts are constructed directly (the same ColumnFacts shape
reflect_schemas builds from sys.columns) — hand-built SQLAlchemy markers like
server_default/Computed are deliberately NOT consulted by the new
classification, so tests state facts explicitly.
"""

import datetime
from decimal import Decimal

import pytest
from sqlalchemy import Column, Identity, MetaData, Table
from sqlalchemy.dialects.mssql import (
    BIGINT, BIT, CHAR, DATE, DATETIME, DATETIME2, DATETIMEOFFSET, DECIMAL,
    FLOAT, INTEGER, MONEY, NCHAR, NTEXT, NVARCHAR, REAL, SMALLDATETIME,
    SMALLINT, SMALLMONEY, SQL_VARIANT, TEXT, TIME, TIMESTAMP, TINYINT,
    UNIQUEIDENTIFIER, VARBINARY, VARCHAR, XML,
)

from app.mssql_types import GEOGRAPHY, HIERARCHYID, JSON, VECTOR
from app.reflection import (
    CatalogFacts,
    ColumnFacts,
    ColumnKind,
    ForeignKeyRef,
    _build_column_info,
    _build_models,
    _find_display_column,
    _is_auto_generated_pk,
    _is_required_on_create,
    _python_type,
)

NO_FACTS = ColumnFacts()


def make_column(name, *args, **kw):
    md = MetaData()
    t = Table("T", md, Column(name, *args, **kw), schema="dbo")
    return t.c[name]


def info_for(name, *args, facts=NO_FACTS, pk_count=1, **kw):
    return _build_column_info(make_column(name, *args, **kw), facts, pk_count)


# ── SQL type → Python type ───────────────────────────────────────────────────

TYPE_CASES = [
    (BIGINT(), int), (INTEGER(), int), (SMALLINT(), int), (TINYINT(), int),
    (BIT(), bool),
    (DECIMAL(18, 2), Decimal), (MONEY(), Decimal), (SMALLMONEY(), Decimal),
    (REAL(), float), (FLOAT(), float),
    (DATETIME2(), datetime.datetime), (DATETIME(), datetime.datetime),
    (SMALLDATETIME(), datetime.datetime),
    (DATE(), datetime.date), (TIME(), datetime.time),
    (DATETIMEOFFSET(), str),          # kept as text, not a tz-aware type
    (NVARCHAR(50), str), (NCHAR(5), str), (NTEXT(), str),
    (VARCHAR(50), str), (CHAR(5), str), (TEXT(), str),
    (UNIQUEIDENTIFIER(), str),
    # No None case: opaque/unmapped types fall back to str, so every column
    # has a usable python_type and model-building can never hit an illegal
    # "editable but untyped" state.
    (SQL_VARIANT(), str), (XML(), str), (VARBINARY(), str),
    (JSON(), str), (VECTOR(), str), (HIERARCHYID(), str), (GEOGRAPHY(), str),
]


@pytest.mark.parametrize("sa_type,expected", TYPE_CASES, ids=lambda v: getattr(v, "__name__", type(v).__name__))
def test_python_type_mapping(sa_type, expected):
    assert _python_type(make_column("C", sa_type)) is expected


# ── Per-column policy: searchable / filterable / read_as_text ────────────────

@pytest.mark.parametrize("sa_type", [NVARCHAR(50), NCHAR(5), NTEXT(), VARCHAR(50), CHAR(5), TEXT()],
                         ids=lambda t: type(t).__name__)
def test_string_types_are_searchable(sa_type):
    assert info_for("C", sa_type).searchable is True


@pytest.mark.parametrize(
    "sa_type",
    [XML(), JSON(), VARBINARY(), TIMESTAMP(), UNIQUEIDENTIFIER(), INTEGER(), HIERARCHYID(), SQL_VARIANT()],
    ids=lambda t: type(t).__name__,
)
def test_non_string_types_are_not_searchable(sa_type):
    # LIKE against xml/json raises server-side; binary isn't text; GUIDs,
    # numbers and CLR paths aren't free-text search targets.
    assert info_for("C", sa_type).searchable is False


@pytest.mark.parametrize("sa_type", [INTEGER(), DECIMAL(18, 2), DATE(), NVARCHAR(50), BIT(), UNIQUEIDENTIFIER()],
                         ids=lambda t: type(t).__name__)
def test_comparable_types_are_filterable(sa_type):
    assert info_for("C", sa_type).filterable is True


@pytest.mark.parametrize(
    "sa_type",
    [VARBINARY(), TIMESTAMP(), XML(), SQL_VARIANT(), VECTOR(), GEOGRAPHY(), HIERARCHYID(), JSON()],
    ids=lambda t: type(t).__name__,
)
def test_opaque_types_are_not_filterable(sa_type):
    # Value comparisons against these either bind bytes (useless through
    # JSON) or are rejected by the server — refused cleanly at reflection.
    assert info_for("C", sa_type).filterable is False


@pytest.mark.parametrize("sa_type", [HIERARCHYID(), GEOGRAPHY(), SQL_VARIANT()],
                         ids=lambda t: type(t).__name__)
def test_clr_and_variant_types_read_as_text(sa_type):
    # The driver returns raw CLR bytes for these (sql_variant has no fixed
    # shape) — the read path CASTs them to NVARCHAR.
    assert info_for("C", sa_type).read_as_text is True


@pytest.mark.parametrize("sa_type", [JSON(), VECTOR(), NVARCHAR(50), VARBINARY()],
                         ids=lambda t: type(t).__name__)
def test_natively_readable_types_do_not_cast(sa_type):
    # json/vector come back as strings, binary as bytes (hex-encoded later) —
    # no CAST needed.
    assert info_for("C", sa_type).read_as_text is False


# ── max_length: real limits pass through, LOB pointer sizes do not ───────────

def test_text_ntext_lob_pointer_is_not_a_max_length():
    # Reflection reports TEXT(16)/NTEXT(8) — sys.columns.max_length is the
    # 16-byte LOB pointer (halved for ntext), not a character limit. Surfacing
    # it would put Field(max_length=16/8) on the models and reject valid input.
    for sa_type in (TEXT(16), NTEXT(8)):
        info = info_for("C", sa_type)
        assert info.max_length is None, f"{type(sa_type).__name__} leaked a LOB pointer as max_length"


def test_varchar_length_still_surfaces_as_max_length():
    assert info_for("C", NVARCHAR(50)).max_length == 50


# ── ColumnInfo carries the FK and comment from reflection ────────────────────

def test_foreign_key_ref_comes_from_facts():
    facts = ColumnFacts(foreign_key=ForeignKeyRef("dbo", "Employee", "EmployeeID"))
    info = info_for("ManagerID", INTEGER(), facts=facts)
    assert info.foreign_key == ("dbo", "Employee", "EmployeeID")
    assert info.foreign_key.table == "Employee"


def test_comment_comes_from_the_column():
    info = info_for("Code", NVARCHAR(10), comment="Business code")
    assert info.comment == "Business code"


# ── required-on-create ───────────────────────────────────────────────────────

def test_not_null_no_default_is_required():
    col = make_column("Name", NVARCHAR(50), nullable=False)
    assert _is_required_on_create(col, ColumnKind.EDITABLE, NO_FACTS, 1) is True


def test_nullable_is_optional():
    col = make_column("Nickname", NVARCHAR(50), nullable=True)
    assert _is_required_on_create(col, ColumnKind.EDITABLE, NO_FACTS, 1) is False


def test_not_null_with_default_is_optional():
    # has_default comes from sys.columns default_object_id — ungated, so a NOT
    # NULL defaulted column is optional at every privilege level, even when
    # the default's text is hidden.
    col = make_column("IsActive", BIT(), nullable=False)
    assert _is_required_on_create(col, ColumnKind.EDITABLE, ColumnFacts(has_default=True), 1) is False


def test_db_owned_is_never_required():
    col = make_column("CreatedBy", NVARCHAR(128), nullable=False)
    assert _is_required_on_create(col, ColumnKind.DB_OWNED, NO_FACTS, 1) is False


def test_auto_generated_pk_is_not_required():
    col = make_column("ID", INTEGER(), Identity(), primary_key=True, nullable=False)
    assert _is_required_on_create(col, ColumnKind.EDITABLE, NO_FACTS, 1) is False


# ── auto-generated PK detection ──────────────────────────────────────────────

def test_identity_pk_is_auto_generated():
    col = make_column("ID", INTEGER(), Identity(), primary_key=True)
    assert _is_auto_generated_pk(col, NO_FACTS, 1) is True


def test_manual_pk_is_not_auto_generated():
    # Reflected columns carry autoincrement=True/False (never "auto") and a
    # manual PK has neither identity nor a default — the client supplies it.
    # This holds for int PKs too (pinned against a real database in the
    # integration matrix: dbo.ManualIntKey).
    for col in (
        make_column("Code", NVARCHAR(10), primary_key=True, autoincrement=False),
        make_column("IntCode", INTEGER(), primary_key=True, autoincrement=False),
    ):
        assert _is_auto_generated_pk(col, NO_FACTS, 1) is False


def test_single_pk_with_default_is_auto_generated():
    # A lone PK with any default constraint is database-suppliable even when
    # the default's text is privilege-hidden — omitted from the Create model.
    col = make_column("RowGuid", UNIQUEIDENTIFIER(), primary_key=True)
    assert _is_auto_generated_pk(col, ColumnFacts(has_default=True), 1) is True


def test_composite_pk_member_is_not_auto_generated():
    # The pk_count guard prevents excluding a composite-PK member, defaulted
    # or not.
    col = make_column("OrgID", INTEGER(), primary_key=True)
    assert _is_auto_generated_pk(col, NO_FACTS, 2) is False
    assert _is_auto_generated_pk(col, ColumnFacts(has_default=True), 2) is False


# ── display column heuristic ─────────────────────────────────────────────────

def _columns(table, facts: CatalogFacts | None = None):
    facts = facts or CatalogFacts()
    pk_count = len(table.primary_key.columns)
    return [_build_column_info(c, facts.for_column(c), pk_count) for c in table.columns]


def test_name_hint_beats_code_hint():
    md = MetaData()
    t = Table(
        "Dept", md,
        Column("DeptID", INTEGER(), Identity(), primary_key=True),
        Column("DepartmentCode", NVARCHAR(10), nullable=False),
        Column("DepartmentName", NVARCHAR(100), nullable=False),
        schema="dbo",
    )
    assert _find_display_column(_columns(t)) == "DepartmentName"


def test_code_used_when_no_higher_hint():
    md = MetaData()
    t = Table(
        "Ref", md,
        Column("RefID", INTEGER(), Identity(), primary_key=True),
        Column("RefCode", NVARCHAR(10), nullable=False),
        schema="dbo",
    )
    assert _find_display_column(_columns(t)) == "RefCode"


def test_first_text_column_when_no_hint():
    md = MetaData()
    t = Table(
        "Thing", md,
        Column("ThingID", INTEGER(), Identity(), primary_key=True),
        Column("Quantity", INTEGER(), nullable=True),
        Column("Payload", NVARCHAR(50), nullable=True),
        schema="dbo",
    )
    assert _find_display_column(_columns(t)) == "Payload"


def test_no_display_column_when_only_numeric():
    md = MetaData()
    t = Table(
        "Nums", md,
        Column("NumID", INTEGER(), Identity(), primary_key=True),
        Column("Amount", DECIMAL(18, 2), nullable=True),
        schema="dbo",
    )
    assert _find_display_column(_columns(t)) is None


def test_pk_and_audit_columns_are_not_display_candidates():
    # PKs are excluded; audit columns are DB_OWNED (not editable) so excluded too.
    md = MetaData()
    t = Table(
        "Audited", md,
        Column("Name", NVARCHAR(50), primary_key=True, autoincrement=False),  # PK named "name"
        Column("CreatedBy", NVARCHAR(128), nullable=True),                    # audit, DB_OWNED
        schema="dbo",
    )
    assert _find_display_column(_columns(t)) is None


# ── Pydantic model factory ───────────────────────────────────────────────────

def _employee():
    md = MetaData()
    table = Table(
        "Employee", md,
        Column("EmployeeID", INTEGER(), Identity(), primary_key=True),
        Column("Name", NVARCHAR(50), nullable=False),      # required
        Column("Nickname", NVARCHAR(50), nullable=True),   # optional
        Column("Status", INTEGER(), nullable=False),       # optional via default (facts)
        Column("FullName", NVARCHAR(101)),                 # computed (facts) → DB_OWNED
        Column("CreatedBy", NVARCHAR(128), nullable=True),  # audit → DB_OWNED
        schema="dbo",
    )
    facts = CatalogFacts({
        ("dbo", "Employee", "Status"): ColumnFacts(has_default=True, default_text="((0))"),
        ("dbo", "Employee", "FullName"): ColumnFacts(is_computed=True),
    })
    return table, facts


def _models(table, facts):
    return _build_models("dbo", table, _columns(table, facts), facts)


def test_create_model_excludes_db_owned_and_identity():
    table, facts = _employee()
    created, _ = _models(table, facts)
    assert set(created.model_fields) == {"Name", "Nickname", "Status"}


def test_create_model_required_vs_optional():
    table, facts = _employee()
    created, _ = _models(table, facts)
    assert created.model_fields["Name"].is_required() is True
    assert created.model_fields["Nickname"].is_required() is False
    assert created.model_fields["Status"].is_required() is False


def test_create_model_enforces_max_length():
    table, facts = _employee()
    created, _ = _models(table, facts)
    with pytest.raises(Exception):  # pydantic ValidationError
        created(Name="x" * 51)


def test_update_model_all_fields_optional_and_no_pk():
    table, facts = _employee()
    _, updated = _models(table, facts)
    assert set(updated.model_fields) == {"Name", "Nickname", "Status"}
    assert all(not f.is_required() for f in updated.model_fields.values())


def test_manual_pk_stays_in_create_model_and_is_required():
    md = MetaData()
    table = Table(
        "ManualKey", md,
        Column("Code", NVARCHAR(10), primary_key=True, autoincrement=False),
        Column("Label", NVARCHAR(100), nullable=False),
        schema="dbo",
    )
    facts = CatalogFacts()
    created, updated = _models(table, facts)
    assert created.model_fields["Code"].is_required() is True
    assert "Code" not in updated.model_fields  # PKs are addressed via the URL
