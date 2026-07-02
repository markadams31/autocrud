"""
Comprehensive reflection against real SQL Server — every situation from
schema.sql verified through the production reflect_schemas() output.

Every test runs twice via the parametrized `snapshot` fixture (see conftest):
once reflected as sa, once as the least-privilege login (VIEW DEFINITION only,
no data access). Identical assertions holding under both identities IS the
module's privilege promise; test_reflection_golden adds the wholesale
field-by-field parity check.

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

def test_both_schemas_reflected(snapshot):
    assert snapshot.schemas() == ["app2", "dbo"]


def test_table_without_pk_is_skipped(snapshot):
    assert snapshot.get("dbo", "NoPk") is None


def test_temporal_history_table_excluded(snapshot):
    assert snapshot.get("dbo", "VersionedHistory") is None


def test_temporal_main_table_included(snapshot):
    v = snapshot.get("dbo", "Versioned")
    assert v is not None
    assert _col(v, "Name").kind is ColumnKind.EDITABLE


def test_generated_always_period_columns_are_db_owned(snapshot):
    v = snapshot.get("dbo", "Versioned")
    assert _col(v, "ValidFrom").kind is ColumnKind.DB_OWNED
    assert _col(v, "ValidTo").kind is ColumnKind.DB_OWNED


# ── Primary keys ─────────────────────────────────────────────────────────────

def test_identity_primary_key(snapshot):
    at = snapshot.get("dbo", "AllTypes")
    assert at.primary_key == ["AllTypesID"]
    assert _col(at, "AllTypesID").kind is ColumnKind.DB_OWNED
    assert _col(at, "AllTypesID").is_primary_key is True


def test_composite_primary_key_order(snapshot):
    assert snapshot.get("dbo", "Composite").primary_key == ["OrgID", "TagID"]


def test_manual_single_primary_key(snapshot):
    mk = snapshot.get("dbo", "ManualKey")
    assert mk.primary_key == ["Code"]
    assert _col(mk, "Label").required_on_create is True


def test_manual_pk_is_client_suppliable(snapshot):
    # A non-identity PK must remain in the create model so the client can supply
    # it. (Validates _is_auto_generated_pk doesn't over-exclude manual PKs.)
    mk = snapshot.get("dbo", "ManualKey")
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
def test_python_type_mapping(snapshot, name, expected):
    assert _col(snapshot.get("dbo", "AllTypes"), name).python_type is expected


# ── Excluded-for-write types ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name", ["ColVarbinary", "ColVarbinaryMax", "ColXml", "ColSqlVariant", "ColRowversion"]
)
def test_unsupported_types_excluded(snapshot, name):
    assert _col(snapshot.get("dbo", "AllTypes"), name).kind is ColumnKind.EXCLUDED


def test_hierarchyid_falls_back_to_editable_string(snapshot):
    c = _col(snapshot.get("dbo", "AllTypes"), "ColHierarchy")
    assert c.python_type is str
    assert c.kind is ColumnKind.EDITABLE


# ── Database-owned mechanisms ────────────────────────────────────────────────

def test_computed_columns_are_db_owned(snapshot):
    at = snapshot.get("dbo", "AllTypes")
    assert _col(at, "ColComputed").kind is ColumnKind.DB_OWNED
    assert _col(at, "ColComputedPersisted").kind is ColumnKind.DB_OWNED


def test_value_generating_defaults_are_db_owned(snapshot):
    at = snapshot.get("dbo", "AllTypes")
    assert _col(at, "ColGenDefault").kind is ColumnKind.DB_OWNED
    assert _col(at, "ColNewId").kind is ColumnKind.DB_OWNED


def test_plain_default_is_editable_and_optional(snapshot):
    c = _col(snapshot.get("dbo", "AllTypes"), "ColPlainDefault")
    assert c.kind is ColumnKind.EDITABLE
    assert c.required_on_create is False


def test_required_and_optional_columns(snapshot):
    at = snapshot.get("dbo", "AllTypes")
    assert _col(at, "ColRequired").required_on_create is True
    assert _col(at, "ColNullable").required_on_create is False


def test_audit_columns_db_owned(snapshot):
    at = snapshot.get("dbo", "AllTypes")
    for name in ("CreatedBy", "CreatedDate", "ModifiedBy", "ModifiedDate"):
        assert _col(at, name).is_audit is True
        assert _col(at, name).kind is ColumnKind.DB_OWNED


def test_create_model_excludes_all_db_owned(snapshot):
    fields = set(snapshot.get("dbo", "AllTypes").create_model.model_fields)
    for absent in (
        "AllTypesID", "ColComputed", "ColComputedPersisted", "ColGenDefault",
        "ColNewId", "ColVarbinary", "ColXml", "ColSqlVariant", "ColRowversion",
        "CreatedBy", "ModifiedDate",
    ):
        assert absent not in fields
    assert "ColRequired" in fields
    assert "ColNullable" in fields


# ── Foreign keys ─────────────────────────────────────────────────────────────

def test_two_foreign_keys_to_same_table(snapshot):
    p = snapshot.get("dbo", "Project")
    assert _col(p, "ManagerID").foreign_key == ("dbo", "Employee", "EmployeeID")
    assert _col(p, "SponsorID").foreign_key == ("dbo", "Employee", "EmployeeID")


def test_cross_table_foreign_key(snapshot):
    p = snapshot.get("dbo", "Project")
    assert _col(p, "CategoryID").foreign_key == ("dbo", "Category", "CategoryID")


def test_self_referential_foreign_key(snapshot):
    t = snapshot.get("dbo", "TaskNode")
    assert _col(t, "ParentID").foreign_key == ("dbo", "TaskNode", "TaskID")


def test_cross_schema_foreign_key(snapshot):
    e = snapshot.get("app2", "External")
    assert _col(e, "CategoryID").foreign_key == ("dbo", "Category", "CategoryID")


def test_persisted_computed_fk_combo(snapshot):
    assert _col(snapshot.get("dbo", "Project"), "DurationDays").kind is ColumnKind.DB_OWNED


# ── SQL Server 2025 native types (json / vector), registered in mssql_types ──

def test_native_json_is_editable_string(snapshot):
    c = _col(snapshot.get("dbo", "AllTypes"), "ColJson")
    assert c.kind is ColumnKind.EDITABLE
    assert c.python_type is str
    assert c.sql_type == "JSON"      # registered type, not the dialect's "NULL"
    assert c.fetchable is True


def test_native_vector_is_excluded(snapshot):
    c = _col(snapshot.get("dbo", "AllTypes"), "ColVector")
    assert c.kind is ColumnKind.EXCLUDED
    assert c.sql_type == "VECTOR"    # registered type, not "VARBINARY(20)"
    assert c.max_length is None      # the varbinary byte count is not surfaced


def test_json_in_create_model_vector_not(snapshot):
    fields = set(snapshot.get("dbo", "AllTypes").create_model.model_fields)
    assert "ColJson" in fields
    assert "ColVector" not in fields


# ── CLR / spatial / sql_variant classification + fetchability ────────────────

def test_spatial_types_excluded_and_unfetchable(snapshot):
    s = snapshot.get("dbo", "Spatial")
    for name, disp in [("Geo", "GEOGRAPHY"), ("Shape", "GEOMETRY")]:
        c = _col(s, name)
        assert c.kind is ColumnKind.EXCLUDED
        assert c.sql_type == disp
        assert c.fetchable is False


def test_hierarchyid_editable_but_unfetchable(snapshot):
    c = _col(snapshot.get("dbo", "Spatial"), "Node")
    assert c.kind is ColumnKind.EDITABLE       # a path string round-trips
    assert c.python_type is str
    assert c.sql_type == "HIERARCHYID"
    assert c.fetchable is False                 # ...but pyodbc can't SELECT it raw


def test_sql_variant_excluded_and_unfetchable(snapshot):
    c = _col(snapshot.get("dbo", "Spatial"), "Variant")
    assert c.kind is ColumnKind.EXCLUDED
    assert c.fetchable is False


def test_spatial_create_model_only_has_editable_columns(snapshot):
    # Geo/Shape/Variant EXCLUDED, SpatialID identity → only Name/Node/Doc remain.
    fields = set(snapshot.get("dbo", "Spatial").create_model.model_fields)
    assert fields == {"Name", "Node", "Doc"}


# ── Display-column heuristic ─────────────────────────────────────────────────

def test_display_columns(snapshot):
    assert snapshot.get("dbo", "Category").display_column == "CategoryName"   # name > code
    assert snapshot.get("dbo", "Employee").display_column == "FullName"
    assert snapshot.get("dbo", "ManualKey").display_column == "Label"


# ── Schema outside the configured set ────────────────────────────────────────
# dbo.EdgeRef references outside.Target; 'outside' is not in DB_SCHEMAS.
# SQLAlchemy's resolve_fks default would recursively reflect the referenced
# table into the MetaData, and it would enter the snapshot misclassified (the
# sys.* flag/FK/CHECK queries filter to configured schemas). Reflection must
# keep the FK metadata while excluding the foreign table itself.

def test_outside_schema_never_enters_snapshot(snapshot):
    assert snapshot.get("outside", "Target") is None
    assert "outside" not in snapshot.schemas()


def test_fk_into_unconfigured_schema_still_reported(snapshot):
    # The referencing column keeps its FK metadata even though the target table
    # is not reflected — the frontend's options endpoint degrades gracefully to
    # an empty dropdown for a target outside the snapshot.
    c = _col(snapshot.get("dbo", "EdgeRef"), "TargetID")
    assert c.foreign_key == ("outside", "Target", "TargetID")


# ── Legacy text/ntext — the reflected "length" is the LOB pointer, not a limit ──

@pytest.mark.parametrize("name", ["ColText", "ColNText"])
def test_text_ntext_editable_with_no_max_length(snapshot, name):
    # sys.columns.max_length for a text/ntext column is the 16-byte LOB pointer
    # (8 for ntext after the nchar halving). Surfacing it as max_length would
    # make the generated models reject valid input past 16/8 characters.
    c = _col(snapshot.get("dbo", "Legacy"), name)
    assert c.kind is ColumnKind.EDITABLE
    assert c.python_type is str
    assert c.max_length is None


def test_text_ntext_create_model_accepts_long_values(snapshot):
    model = snapshot.get("dbo", "Legacy").create_model
    row = model(Label="x", ColText="t" * 5000, ColNText="n" * 5000)
    assert len(row.ColText) == 5000 and len(row.ColNText) == 5000


# ── Alias UDT — reflected via base-type fallback; visibility needs VIEW DEFINITION ──

def test_alias_udt_reflects_as_base_type(snapshot):
    # dbo.PhoneNumber (FROM varchar(20)). The type's sys.types row is its own
    # securable: without VIEW DEFINITION the column vanishes from reflection
    # entirely, so this passing under the vdonly identity pins that the grant
    # keeps alias-UDT columns visible.
    c = _col(snapshot.get("dbo", "Legacy"), "ColUdt")
    assert c.kind is ColumnKind.EDITABLE
    assert c.python_type is str
    assert c.max_length == 20


# ── Sequence-fed default — a plain (overridable) default, not DB-owned ────────

def test_sequence_default_is_editable_and_optional(snapshot):
    c = _col(snapshot.get("dbo", "Legacy"), "ColSeq")
    assert c.kind is ColumnKind.EDITABLE
    assert c.required_on_create is False


# ── Manual single-column INTEGER primary key ─────────────────────────────────
# Reflected mssql columns carry autoincrement=True/False (never "auto"), so a
# manual int PK stays in the create model and is required. A hand-built Table
# of the same shape reports autoincrement="auto" and would be excluded — this
# pin exists precisely because unit-test tables diverge from live reflection.

def test_manual_int_pk_is_client_suppliable_and_required(snapshot):
    mk = snapshot.get("dbo", "ManualIntKey")
    assert mk.primary_key == ["IntCode"]
    assert "IntCode" in mk.create_model.model_fields
    assert mk.create_model.model_fields["IntCode"].is_required()
