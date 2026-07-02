"""
Comprehensive reflection against real SQL Server — every situation from
schema.sql verified through the production reflect_schemas() output.

Docker-gated (see integration/conftest.py). Run with:
    uv sync --extra test
    uv run pytest tests/integration     # needs Docker
"""

import datetime
from decimal import Decimal

import pytest

from app.reflection import ColumnKind

pytestmark = pytest.mark.integration


def _col(ti, name):
    c = ti.column(name)
    assert c is not None, f"{ti.name}.{name} was not reflected"
    return c


# ── Table-level inclusion / exclusion ────────────────────────────────────────

def test_both_schemas_reflected(reflected):
    assert reflected.schemas() == ["app2", "dbo"]


def test_table_without_pk_is_skipped(reflected):
    assert reflected.get("dbo", "NoPk") is None


def test_temporal_history_table_excluded(reflected):
    assert reflected.get("dbo", "VersionedHistory") is None


def test_temporal_main_table_included(reflected):
    v = reflected.get("dbo", "Versioned")
    assert v is not None
    assert _col(v, "Name").kind is ColumnKind.EDITABLE


def test_generated_always_period_columns_are_db_owned(reflected):
    v = reflected.get("dbo", "Versioned")
    assert _col(v, "ValidFrom").kind is ColumnKind.DB_OWNED
    assert _col(v, "ValidTo").kind is ColumnKind.DB_OWNED


# ── Primary keys ─────────────────────────────────────────────────────────────

def test_identity_primary_key(reflected):
    at = reflected.get("dbo", "AllTypes")
    assert at.primary_key == ["AllTypesID"]
    assert _col(at, "AllTypesID").kind is ColumnKind.DB_OWNED
    assert _col(at, "AllTypesID").is_primary_key is True


def test_composite_primary_key_order(reflected):
    assert reflected.get("dbo", "Composite").primary_key == ["OrgID", "TagID"]


def test_manual_single_primary_key(reflected):
    mk = reflected.get("dbo", "ManualKey")
    assert mk.primary_key == ["Code"]
    assert _col(mk, "Label").required_on_create is True


def test_manual_pk_is_client_suppliable(reflected):
    # A non-identity PK must remain in the create model so the client can supply
    # it. (Validates _is_auto_generated_pk doesn't over-exclude manual PKs.)
    mk = reflected.get("dbo", "ManualKey")
    assert "Code" in mk.create_model.model_fields
    assert mk.create_model.model_fields["Code"].is_required()


# ── Type mapping ─────────────────────────────────────────────────────────────

TYPE_EXPECT = {
    "ColBigInt": int, "ColInt": int, "ColSmallInt": int, "ColTinyInt": int,
    "ColBit": bool,
    "ColDecimal": Decimal, "ColNumeric": Decimal, "ColMoney": Decimal, "ColSmallMoney": Decimal,
    "ColFloat": float, "ColReal": float,
    "ColDate": datetime.date, "ColTime": datetime.time,
    "ColDateTime": datetime.datetime, "ColDateTime2": datetime.datetime,
    "ColSmallDateTime": datetime.datetime,
    "ColDateTimeOffset": str,
    "ColNVarchar": str, "ColNVarcharMax": str, "ColVarchar": str,
    "ColChar": str, "ColNChar": str, "ColUniqueId": str,
}


@pytest.mark.parametrize("name,expected", list(TYPE_EXPECT.items()))
def test_python_type_mapping(reflected, name, expected):
    assert _col(reflected.get("dbo", "AllTypes"), name).python_type is expected


# ── Excluded-for-write types ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name", ["ColVarbinary", "ColVarbinaryMax", "ColXml", "ColSqlVariant", "ColRowversion"]
)
def test_unsupported_types_excluded(reflected, name):
    assert _col(reflected.get("dbo", "AllTypes"), name).kind is ColumnKind.EXCLUDED


def test_hierarchyid_falls_back_to_editable_string(reflected):
    c = _col(reflected.get("dbo", "AllTypes"), "ColHierarchy")
    assert c.python_type is str
    assert c.kind is ColumnKind.EDITABLE


# ── Database-owned mechanisms ────────────────────────────────────────────────

def test_computed_columns_are_db_owned(reflected):
    at = reflected.get("dbo", "AllTypes")
    assert _col(at, "ColComputed").kind is ColumnKind.DB_OWNED
    assert _col(at, "ColComputedPersisted").kind is ColumnKind.DB_OWNED


def test_value_generating_defaults_are_db_owned(reflected):
    at = reflected.get("dbo", "AllTypes")
    assert _col(at, "ColGenDefault").kind is ColumnKind.DB_OWNED
    assert _col(at, "ColNewId").kind is ColumnKind.DB_OWNED


def test_plain_default_is_editable_and_optional(reflected):
    c = _col(reflected.get("dbo", "AllTypes"), "ColPlainDefault")
    assert c.kind is ColumnKind.EDITABLE
    assert c.required_on_create is False


def test_required_and_optional_columns(reflected):
    at = reflected.get("dbo", "AllTypes")
    assert _col(at, "ColRequired").required_on_create is True
    assert _col(at, "ColNullable").required_on_create is False


def test_audit_columns_db_owned(reflected):
    at = reflected.get("dbo", "AllTypes")
    for name in ("CreatedBy", "CreatedDate", "ModifiedBy", "ModifiedDate"):
        assert _col(at, name).is_audit is True
        assert _col(at, name).kind is ColumnKind.DB_OWNED


def test_create_model_excludes_all_db_owned(reflected):
    fields = set(reflected.get("dbo", "AllTypes").create_model.model_fields)
    for absent in (
        "AllTypesID", "ColComputed", "ColComputedPersisted", "ColGenDefault",
        "ColNewId", "ColVarbinary", "ColXml", "ColSqlVariant", "ColRowversion",
        "CreatedBy", "ModifiedDate",
    ):
        assert absent not in fields
    assert "ColRequired" in fields
    assert "ColNullable" in fields


# ── Foreign keys ─────────────────────────────────────────────────────────────

def test_two_foreign_keys_to_same_table(reflected):
    p = reflected.get("dbo", "Project")
    assert _col(p, "ManagerID").foreign_key == ("dbo", "Employee", "EmployeeID")
    assert _col(p, "SponsorID").foreign_key == ("dbo", "Employee", "EmployeeID")


def test_cross_table_foreign_key(reflected):
    p = reflected.get("dbo", "Project")
    assert _col(p, "CategoryID").foreign_key == ("dbo", "Category", "CategoryID")


def test_self_referential_foreign_key(reflected):
    t = reflected.get("dbo", "TaskNode")
    assert _col(t, "ParentID").foreign_key == ("dbo", "TaskNode", "TaskID")


def test_cross_schema_foreign_key(reflected):
    e = reflected.get("app2", "External")
    assert _col(e, "CategoryID").foreign_key == ("dbo", "Category", "CategoryID")


def test_persisted_computed_fk_combo(reflected):
    assert _col(reflected.get("dbo", "Project"), "DurationDays").kind is ColumnKind.DB_OWNED


# ── SQL Server 2025 native types (json / vector), registered in mssql_types ──

def test_native_json_is_editable_string(reflected):
    c = _col(reflected.get("dbo", "AllTypes"), "ColJson")
    assert c.kind is ColumnKind.EDITABLE
    assert c.python_type is str
    assert c.sql_type == "JSON"      # registered type, not the dialect's "NULL"
    assert c.fetchable is True


def test_native_vector_is_excluded(reflected):
    c = _col(reflected.get("dbo", "AllTypes"), "ColVector")
    assert c.kind is ColumnKind.EXCLUDED
    assert c.sql_type == "VECTOR"    # registered type, not "VARBINARY(20)"
    assert c.max_length is None      # the varbinary byte count is not surfaced


def test_json_in_create_model_vector_not(reflected):
    fields = set(reflected.get("dbo", "AllTypes").create_model.model_fields)
    assert "ColJson" in fields
    assert "ColVector" not in fields


# ── CLR / spatial / sql_variant classification + fetchability ────────────────

def test_spatial_types_excluded_and_unfetchable(reflected):
    s = reflected.get("dbo", "Spatial")
    for name, disp in [("Geo", "GEOGRAPHY"), ("Shape", "GEOMETRY")]:
        c = _col(s, name)
        assert c.kind is ColumnKind.EXCLUDED
        assert c.sql_type == disp
        assert c.fetchable is False


def test_hierarchyid_editable_but_unfetchable(reflected):
    c = _col(reflected.get("dbo", "Spatial"), "Node")
    assert c.kind is ColumnKind.EDITABLE       # a path string round-trips
    assert c.python_type is str
    assert c.sql_type == "HIERARCHYID"
    assert c.fetchable is False                 # ...but pyodbc can't SELECT it raw


def test_sql_variant_excluded_and_unfetchable(reflected):
    c = _col(reflected.get("dbo", "Spatial"), "Variant")
    assert c.kind is ColumnKind.EXCLUDED
    assert c.fetchable is False


def test_spatial_create_model_only_has_editable_columns(reflected):
    # Geo/Shape/Variant EXCLUDED, SpatialID identity → only Name/Node/Doc remain.
    fields = set(reflected.get("dbo", "Spatial").create_model.model_fields)
    assert fields == {"Name", "Node", "Doc"}


# ── Display-column heuristic ─────────────────────────────────────────────────

def test_display_columns(reflected):
    assert reflected.get("dbo", "Category").display_column == "CategoryName"   # name > code
    assert reflected.get("dbo", "Employee").display_column == "FullName"
    assert reflected.get("dbo", "ManualKey").display_column == "Label"
