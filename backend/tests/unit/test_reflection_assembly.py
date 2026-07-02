"""
Column/table assembly: foreign-key wiring, primary-key ordering, the
implicit-RETURNING opt-out (trigger safety), and the SchemaSnapshot container.
"""

from sqlalchemy import Column, Identity, MetaData, Table
from sqlalchemy.dialects.mssql import INTEGER, NVARCHAR

from app.reflection import (
    CatalogFacts,
    ColumnFacts,
    ForeignKeyRef,
    SchemaSnapshot,
    _build_column_info,
    _build_table_info,
)


def test_foreign_key_metadata_from_facts():
    md = MetaData()
    t = Table(
        "Project", md,
        Column("ProjectID", INTEGER(), Identity(), primary_key=True),
        Column("ManagerID", INTEGER(), nullable=False),
        schema="ppm",
    )
    facts = CatalogFacts({
        ("ppm", "Project", "ManagerID"): ColumnFacts(
            foreign_key=ForeignKeyRef("dbo", "Employee", "EmployeeID")
        ),
    })
    info = _build_column_info(t.c.ManagerID, facts.for_column(t.c.ManagerID), 1)
    # ForeignKeyRef is a NamedTuple: attribute access and tuple equality both work.
    assert info.foreign_key == ("dbo", "Employee", "EmployeeID")
    assert (info.foreign_key.schema, info.foreign_key.column) == ("dbo", "EmployeeID")


def test_non_fk_column_has_no_foreign_key():
    md = MetaData()
    t = Table("T", md, Column("X", INTEGER(), primary_key=True), schema="dbo")
    assert _build_column_info(t.c.X, ColumnFacts(), 1).foreign_key is None


def test_table_info_pk_order_and_models():
    md = MetaData()
    t = Table(
        "TagMap", md,
        Column("OrgID", INTEGER(), primary_key=True),
        Column("TagID", INTEGER(), primary_key=True),
        Column("Note", NVARCHAR(100), nullable=True),
        schema="dbo",
    )
    info = _build_table_info(t, CatalogFacts())
    assert info.schema == "dbo"
    assert info.name == "TagMap"
    assert info.primary_key == ("OrgID", "TagID")   # constraint-definition order
    assert info.create_model is not None
    assert info.update_model is not None
    assert info.column("Note") is not None
    assert info.column("Missing") is None


def test_table_info_disables_implicit_returning():
    # Trigger-carrying tables forbid OUTPUT clauses; reflection must opt out so
    # inserts fall back to SELECT SCOPE_IDENTITY().
    md = MetaData()
    t = Table("T", md, Column("ID", INTEGER(), Identity(), primary_key=True), schema="dbo")
    _build_table_info(t, CatalogFacts())
    assert t.implicit_returning is False


def test_schema_override_keys_the_snapshot_not_the_sql():
    # Test harnesses run hand-built, schemaless tables against sqlite while the
    # snapshot keys them under a schema name; the override affects only the key.
    md = MetaData()
    t = Table("W", md, Column("ID", INTEGER(), primary_key=True))
    info = _build_table_info(t, CatalogFacts(), schema="dbo")
    assert info.key == ("dbo", "W")
    assert t.schema is None


def test_snapshot_accessors_and_timestamp():
    md = MetaData()
    a = _build_table_info(Table("A", md, Column("ID", INTEGER(), primary_key=True), schema="dbo"), CatalogFacts())
    b = _build_table_info(Table("B", md, Column("ID", INTEGER(), primary_key=True), schema="ppm"), CatalogFacts())
    c = _build_table_info(Table("C", md, Column("ID", INTEGER(), primary_key=True), schema="ppm"), CatalogFacts())

    snap = SchemaSnapshot(tables={a.key: a, b.key: b, c.key: c})

    assert snap.schemas() == ["dbo", "ppm"]            # sorted, de-duped
    assert [t.name for t in snap.tables_in("ppm")] == ["B", "C"]  # sorted by name
    assert snap.get("dbo", "A") is a
    assert snap.get("dbo", "missing") is None
    assert snap.reflected_at.tzinfo is not None        # aware UTC timestamp
