"""
Type mapping, required-on-create, auto-generated PK detection, the display
column heuristic, and the generated Pydantic create/update models.
"""

import datetime
from decimal import Decimal

import pytest
from sqlalchemy import Column, Computed, Identity, MetaData, Table, text
from sqlalchemy.dialects.mssql import (
    BIGINT, BIT, CHAR, DATE, DATETIME, DATETIME2, DATETIMEOFFSET, DECIMAL,
    FLOAT, INTEGER, MONEY, NCHAR, NTEXT, NVARCHAR, REAL, SMALLDATETIME,
    SMALLINT, SMALLMONEY, SQL_VARIANT, TEXT, TIME, TINYINT, UNIQUEIDENTIFIER,
    VARCHAR,
)

from app.mssql_types import GEOGRAPHY, HIERARCHYID, JSON, VECTOR
from app.reflection import (
    ColumnKind,
    _build_column_info,
    _build_create_model,
    _build_update_model,
    _find_display_column,
    _is_auto_generated_pk,
    _is_required_on_create,
    _python_type,
)


def make_column(name, *args, **kw):
    md = MetaData()
    t = Table("T", md, Column(name, *args, **kw), schema="dbo")
    return t.c[name]


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
]


@pytest.mark.parametrize("sa_type,expected", TYPE_CASES, ids=lambda v: getattr(v, "__name__", type(v).__name__))
def test_python_type_mapping(sa_type, expected):
    assert _python_type(make_column("C", sa_type)) is expected


def test_sql_variant_has_no_python_type():
    # Unrepresentable — surfaces as None so it can be omitted entirely.
    assert _python_type(make_column("V", SQL_VARIANT())) is None


# ── fetchable + display for the app.mssql_types registered types ─────────────
# These reflect as real named types (see test_mssql_types), so _build_column_info
# reads them by isinstance — no catalog read. fetchable is False for the CLR types
# pyodbc can't materialise (see reflection._UNFETCHABLE_TYPES).

def test_build_column_info_flags_unfetchable_clr_types():
    for sa_type, kind in ((GEOGRAPHY(), ColumnKind.EXCLUDED), (HIERARCHYID(), ColumnKind.EDITABLE)):
        info = _build_column_info(make_column("C", sa_type), set(), {})
        assert info.fetchable is False
        assert info.kind is kind
        assert info.sql_type == str(sa_type)   # e.g. "GEOGRAPHY" / "HIERARCHYID"


def test_build_column_info_json_is_fetchable_editable():
    info = _build_column_info(make_column("Doc", JSON()), set(), {})
    assert info.fetchable is True
    assert info.kind is ColumnKind.EDITABLE
    assert info.sql_type == "JSON"


def test_build_column_info_vector_excluded_but_fetchable():
    # A vector reads back fine (as a JSON-array string), so it's fetchable; it's
    # only kept out of writes.
    info = _build_column_info(make_column("V", VECTOR()), set(), {})
    assert info.fetchable is True
    assert info.kind is ColumnKind.EXCLUDED
    assert info.sql_type == "VECTOR"


# ── required-on-create ───────────────────────────────────────────────────────

def test_not_null_no_default_is_required():
    col = make_column("Name", NVARCHAR(50), nullable=False)
    assert _is_required_on_create(col, ColumnKind.EDITABLE, 1) is True


def test_nullable_is_optional():
    col = make_column("Nickname", NVARCHAR(50), nullable=True)
    assert _is_required_on_create(col, ColumnKind.EDITABLE, 1) is False


def test_not_null_with_server_default_is_optional():
    col = make_column("Status", INTEGER(), nullable=False, server_default=text("0"))
    assert _is_required_on_create(col, ColumnKind.EDITABLE, 1) is False


def test_db_owned_is_never_required():
    col = make_column("CreatedBy", NVARCHAR(128), nullable=False)
    assert _is_required_on_create(col, ColumnKind.DB_OWNED, 1) is False


def test_auto_generated_pk_is_not_required():
    col = make_column("ID", INTEGER(), Identity(), primary_key=True, nullable=False)
    assert _is_required_on_create(col, ColumnKind.EDITABLE, 1) is False


def test_structurally_defaulted_column_is_optional():
    # Under a least-privilege reflection identity col.server_default is empty
    # (the default's text is VIEW-DEFINITION-gated), so without the structural
    # flag a NOT NULL defaulted column would be wrongly required. The `defaulted`
    # set (sys.columns default_object_id) must make it optional regardless.
    col = make_column("IsActive", BIT(), nullable=False)  # default text not reflected
    assert _is_required_on_create(col, ColumnKind.EDITABLE, 1) is True
    defaulted = frozenset({("dbo", "T", "IsActive")})
    assert _is_required_on_create(col, ColumnKind.EDITABLE, 1, defaulted) is False


# ── auto-generated PK detection ──────────────────────────────────────────────

def test_identity_pk_is_auto_generated():
    col = make_column("ID", INTEGER(), Identity(), primary_key=True)
    assert _is_auto_generated_pk(col, 1) is True


def test_generating_default_pk_is_auto_generated():
    col = make_column("ID", UNIQUEIDENTIFIER(), primary_key=True, server_default=text("newid()"))
    assert _is_auto_generated_pk(col, 1) is True


def test_manual_pk_is_not_auto_generated():
    col = make_column("Code", NVARCHAR(10), primary_key=True, autoincrement=False)
    assert _is_auto_generated_pk(col, 1) is False


def test_composite_pk_member_is_not_auto_generated():
    # The pk_count > 1 guard prevents excluding a composite-PK member.
    col = make_column("OrgID", INTEGER(), primary_key=True)
    assert _is_auto_generated_pk(col, 2) is False


def test_single_pk_with_structural_default_is_auto_generated():
    # A lone PK that merely has a default constraint (default_object_id, surfaced
    # in `defaulted`) is database-suppliable even when its text is hidden under
    # least privilege — so it's auto-generated and omitted from the Create model.
    col = make_column("RowGuid", UNIQUEIDENTIFIER(), primary_key=True)  # NEWID() text not reflected
    defaulted = frozenset({("dbo", "T", "RowGuid")})
    assert _is_auto_generated_pk(col, 1, defaulted) is True
    # The pk_count guard still protects composite-PK members.
    assert _is_auto_generated_pk(col, 2, defaulted) is False


# ── display column heuristic ─────────────────────────────────────────────────

def _columns(table):
    return [_build_column_info(c, set(), {}) for c in table.columns]


def _pk_names(table):
    return {c.name for c in table.primary_key.columns}


def test_name_hint_beats_code_hint():
    md = MetaData()
    t = Table(
        "Dept", md,
        Column("DeptID", INTEGER(), Identity(), primary_key=True),
        Column("DepartmentCode", NVARCHAR(10), nullable=False),
        Column("DepartmentName", NVARCHAR(100), nullable=False),
        schema="dbo",
    )
    assert _find_display_column(_columns(t), _pk_names(t)) == "DepartmentName"


def test_code_used_when_no_higher_hint():
    md = MetaData()
    t = Table(
        "Ref", md,
        Column("RefID", INTEGER(), Identity(), primary_key=True),
        Column("RefCode", NVARCHAR(10), nullable=False),
        schema="dbo",
    )
    assert _find_display_column(_columns(t), _pk_names(t)) == "RefCode"


def test_first_text_column_when_no_hint():
    md = MetaData()
    t = Table(
        "Thing", md,
        Column("ThingID", INTEGER(), Identity(), primary_key=True),
        Column("Quantity", INTEGER(), nullable=True),
        Column("Label", NVARCHAR(50), nullable=True),
        schema="dbo",
    )
    assert _find_display_column(_columns(t), _pk_names(t)) == "Label"


def test_no_display_column_when_only_numeric():
    md = MetaData()
    t = Table(
        "Nums", md,
        Column("NumID", INTEGER(), Identity(), primary_key=True),
        Column("Amount", DECIMAL(18, 2), nullable=True),
        schema="dbo",
    )
    assert _find_display_column(_columns(t), _pk_names(t)) is None


def test_pk_and_audit_columns_are_not_display_candidates():
    # PKs are excluded; audit columns are DB_OWNED (not editable) so excluded too.
    md = MetaData()
    t = Table(
        "Audited", md,
        Column("Name", NVARCHAR(50), primary_key=True, autoincrement=False),  # PK named "name"
        Column("CreatedBy", NVARCHAR(128), nullable=True),                    # audit, DB_OWNED
        schema="dbo",
    )
    # PK "Name" excluded, "CreatedBy" not editable → no candidate left.
    assert _find_display_column(_columns(t), _pk_names(t)) is None


# ── Pydantic model factory ───────────────────────────────────────────────────

def _employee_table():
    md = MetaData()
    return Table(
        "Employee", md,
        Column("EmployeeID", INTEGER(), Identity(), primary_key=True),
        Column("Name", NVARCHAR(50), nullable=False),                         # required
        Column("Nickname", NVARCHAR(50), nullable=True),                      # optional
        Column("Status", INTEGER(), nullable=False, server_default=text("0")),  # optional (default)
        Column("FullName", NVARCHAR(101), Computed("[Name]")),               # DB_OWNED
        Column("CreatedBy", NVARCHAR(128), nullable=True),                    # audit
        schema="dbo",
    )


def test_create_model_excludes_db_owned_and_identity():
    table = _employee_table()
    cols = _columns(table)
    model = _build_create_model("dbo", table, cols)
    assert set(model.model_fields) == {"Name", "Nickname", "Status"}


def test_create_model_required_vs_optional():
    table = _employee_table()
    model = _build_create_model("dbo", table, _columns(table))
    assert model.model_fields["Name"].is_required() is True
    assert model.model_fields["Nickname"].is_required() is False
    assert model.model_fields["Status"].is_required() is False


def test_create_model_enforces_max_length():
    table = _employee_table()
    model = _build_create_model("dbo", table, _columns(table))
    with pytest.raises(Exception):  # pydantic ValidationError
        model(Name="x" * 51)


def test_update_model_all_fields_optional_and_no_pk():
    table = _employee_table()
    model = _build_update_model("dbo", table, _columns(table))
    assert set(model.model_fields) == {"Name", "Nickname", "Status"}
    assert all(not f.is_required() for f in model.model_fields.values())
