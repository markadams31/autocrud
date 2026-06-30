"""
Bulk delete (POST /api/{schema}/{table}/bulk-delete).

Covers the two modes — an explicit id list and "all matching" a search/filter —
plus the row cap, the auth gate, and the all-or-nothing guarantee: a batch that
violates a constraint deletes nothing, never a partial subset.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, ForeignKey, MetaData, Table, create_engine, event
from sqlalchemy.dialects.mssql import INTEGER, NVARCHAR
from sqlalchemy.pool import StaticPool

from app import reflection
from app.dependencies import get_db, get_snapshot
from app.main import app
from app.reflection import ReflectedSchema


def _create(client, **fields):
    return client.post("/api/dbo/Widget", json=fields)


def _total(client):
    return client.post("/api/dbo/Widget/query", json={"page_size": 500}).json()["total"]


def _all_ids(client):
    """Current WidgetIDs as the [[pk], ...] shape the bulk endpoint expects."""
    rows = client.post("/api/dbo/Widget/query", json={"page_size": 500}).json()["data"]
    return [[r["WidgetID"]] for r in rows]


@pytest.fixture
def seeded(widget):
    """The shared in-memory Widget table, pre-loaded with 10 rows (Quantity 1..10)."""
    for i in range(1, 11):
        _create(widget.client, Name=f"Item-{i:02d}", Quantity=i)
    return widget


# ── Explicit id list ─────────────────────────────────────────────────────────

def test_explicit_ids_deletes_exactly_those(seeded):
    ids = _all_ids(seeded.client)[:3]
    resp = seeded.client.post("/api/dbo/Widget/bulk-delete", json={"ids": ids})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 3}
    assert _total(seeded.client) == 7


def test_explicit_ids_ignores_nonexistent(seeded):
    resp = seeded.client.post("/api/dbo/Widget/bulk-delete", json={"ids": [[99999]]})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 0}
    assert _total(seeded.client) == 10


def test_empty_ids_is_400(seeded):
    resp = seeded.client.post("/api/dbo/Widget/bulk-delete", json={"ids": []})
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"
    assert _total(seeded.client) == 10


def test_wrong_pk_arity_is_400(seeded):
    # Widget has a single-column PK; two values per row is malformed.
    resp = seeded.client.post("/api/dbo/Widget/bulk-delete", json={"ids": [[1, 2]]})
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"
    assert _total(seeded.client) == 10


# ── All matching ─────────────────────────────────────────────────────────────

def test_all_matching_with_filter(seeded):
    # Quantity > 7 → 8, 9, 10
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-delete",
        json={"all_matching": True, "filters": {"Quantity": {"op": "gt", "value": 7}}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 3}
    remaining = seeded.client.post("/api/dbo/Widget/query", json={"page_size": 500}).json()
    assert remaining["total"] == 7
    assert all(r["Quantity"] <= 7 for r in remaining["data"])


def test_all_matching_with_search(seeded):
    # Only "Item-10" contains the substring "Item-10" (Item-01..09 don't).
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-delete", json={"all_matching": True, "search": "Item-10"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 1}
    assert _total(seeded.client) == 9


def test_all_matching_no_filter_deletes_everything(seeded):
    resp = seeded.client.post("/api/dbo/Widget/bulk-delete", json={"all_matching": True})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 10}
    assert _total(seeded.client) == 0


def test_all_matching_empty_set_is_zero(seeded):
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-delete",
        json={"all_matching": True, "filters": {"Quantity": {"op": "gt", "value": 9999}}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 0}
    assert _total(seeded.client) == 10


# ── Row cap ──────────────────────────────────────────────────────────────────

def test_explicit_over_cap_is_400_and_deletes_nothing(seeded, monkeypatch):
    monkeypatch.setattr("app.routes.crud.BULK_MAX_ROWS", 3)
    resp = seeded.client.post("/api/dbo/Widget/bulk-delete", json={"ids": _all_ids(seeded.client)})
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"
    assert _total(seeded.client) == 10


def test_all_matching_over_cap_is_400_and_deletes_nothing(seeded, monkeypatch):
    monkeypatch.setattr("app.routes.crud.BULK_MAX_ROWS", 3)
    resp = seeded.client.post("/api/dbo/Widget/bulk-delete", json={"all_matching": True})
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"
    assert _total(seeded.client) == 10


# ── Auth gate ────────────────────────────────────────────────────────────────

def test_bulk_delete_without_token_is_401(snapshot_only):
    resp = snapshot_only.client.post("/api/dbo/Widget/bulk-delete", json={"ids": [[1]]})
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHENTICATED"


# ── Atomicity: a constraint failure deletes nothing ──────────────────────────

@pytest.fixture
def related():
    """
    Parent (Department) + child (Employee, FK → Department) on sqlite with FK
    enforcement on. Deleting a referenced department raises, which lets us prove
    a bulk delete is all-or-nothing: the whole batch rolls back, not just the
    offending row. Only Department is put in the snapshot (the table under test).
    """
    md = MetaData()
    dept = Table(
        "Department", md,
        Column("DepartmentID", INTEGER(), primary_key=True, autoincrement=True),
        Column("DepartmentName", NVARCHAR(100), nullable=False),
    )
    Table(
        "Employee", md,
        Column("EmployeeID", INTEGER(), primary_key=True, autoincrement=True),
        Column("DepartmentID", INTEGER(), ForeignKey("Department.DepartmentID"), nullable=False),
    )

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):  # sqlite enforces FKs only when asked
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(dept.insert(), [
            {"DepartmentID": 1, "DepartmentName": "Engineering"},
            {"DepartmentID": 2, "DepartmentName": "Finance"},
            {"DepartmentID": 3, "DepartmentName": "Operations"},
        ])
        # One employee references department 1, pinning it against deletion.
        conn.execute(
            md.tables["Employee"].insert(), [{"EmployeeID": 1, "DepartmentID": 1}]
        )

    dept_info = reflection._build_table_info("dbo", dept, set(), {})
    snapshot = ReflectedSchema(tables={dept_info.key: dept_info})

    def _override_get_db():
        conn = engine.connect()
        try:
            with conn.begin():
                yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_snapshot] = lambda: snapshot
    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)
    try:
        yield SimpleNamespace(client=client, engine=engine)
    finally:
        app.dependency_overrides.clear()


def _dept_total(client):
    return client.post("/api/dbo/Department/query", json={"page_size": 500}).json()["total"]


def test_constraint_violation_rolls_back_whole_batch(related):
    # Department 1 is referenced; deleting [1, 2, 3] together must fail as a unit
    # and leave all three in place — not delete the unreferenced 2 and 3.
    resp = related.client.post(
        "/api/dbo/Department/bulk-delete", json={"ids": [[1], [2], [3]]}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONSTRAINT_VIOLATION"
    assert _dept_total(related.client) == 3


def test_all_matching_constraint_violation_rolls_back(related):
    # Same guarantee via the "all matching" path (DELETE ... with no filter).
    resp = related.client.post(
        "/api/dbo/Department/bulk-delete", json={"all_matching": True}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONSTRAINT_VIOLATION"
    assert _dept_total(related.client) == 3
