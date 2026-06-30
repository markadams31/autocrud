"""
Column/table assembly: foreign-key wiring, primary-key ordering, the
implicit-RETURNING opt-out (trigger safety), and the ReflectedSchema container.
"""

from sqlalchemy import Column, Identity, MetaData, Table
from sqlalchemy.dialects.mssql import INTEGER, NVARCHAR

from app.reflection import (
    ReflectedSchema,
    _build_column_info,
    _build_table_info,
)


def test_foreign_key_metadata_from_map():
    md = MetaData()
    t = Table(
        "Project", md,
        Column("ProjectID", INTEGER(), Identity(), primary_key=True),
        Column("ManagerID", INTEGER(), nullable=False),
        schema="ppm",
    )
    fk_map = {("ppm", "Project", "ManagerID"): ("dbo", "Employee", "EmployeeID")}
    info = _build_column_info(t.c.ManagerID, set(), fk_map)
    assert info.foreign_key == ("dbo", "Employee", "EmployeeID")


def test_non_fk_column_has_no_foreign_key():
    md = MetaData()
    t = Table("T", md, Column("X", INTEGER(), primary_key=True), schema="dbo")
    assert _build_column_info(t.c.X, set(), {}).foreign_key is None


def test_table_info_pk_order_and_models():
    md = MetaData()
    t = Table(
        "TagMap", md,
        Column("OrgID", INTEGER(), primary_key=True),
        Column("TagID", INTEGER(), primary_key=True),
        Column("Note", NVARCHAR(100), nullable=True),
        schema="dbo",
    )
    info = _build_table_info("dbo", t, set(), {})
    assert info.schema == "dbo"
    assert info.name == "TagMap"
    assert info.primary_key == ["OrgID", "TagID"]   # constraint-definition order
    assert info.create_model is not None
    assert info.update_model is not None
    assert info.column("Note") is not None
    assert info.column("Missing") is None


def test_table_info_disables_implicit_returning():
    # Trigger-carrying tables forbid OUTPUT clauses; reflection must opt out so
    # inserts fall back to SELECT SCOPE_IDENTITY().
    md = MetaData()
    t = Table("T", md, Column("ID", INTEGER(), Identity(), primary_key=True), schema="dbo")
    _build_table_info("dbo", t, set(), {})
    assert t.implicit_returning is False


def test_reflected_schema_accessors():
    md = MetaData()
    a = _build_table_info("dbo", Table("A", md, Column("ID", INTEGER(), primary_key=True), schema="dbo"), set(), {})
    b = _build_table_info("ppm", Table("B", md, Column("ID", INTEGER(), primary_key=True), schema="ppm"), set(), {})
    c = _build_table_info("ppm", Table("C", md, Column("ID", INTEGER(), primary_key=True), schema="ppm"), set(), {})

    snap = ReflectedSchema(tables={a.key: a, b.key: b, c.key: c})

    assert snap.schemas() == ["dbo", "ppm"]            # sorted, de-duped
    assert [t.name for t in snap.tables_in("ppm")] == ["B", "C"]  # sorted by name
    assert snap.get("dbo", "A") is a
    assert snap.get("dbo", "missing") is None
