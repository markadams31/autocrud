"""
Bulk create (POST /api/{schema}/{table}/bulk-create) — importing many rows.

Covers the happy path, the empty/over-cap guards, the same validation and
db-owned-column scrubbing a single insert gets (applied per row, with errors
attributed to the offending line), and the all-or-nothing guarantee: a batch
whose insert hits a constraint creates nothing and names the row that failed.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, Table, UniqueConstraint, create_engine
from sqlalchemy.dialects.mssql import INTEGER, NVARCHAR
from sqlalchemy.pool import StaticPool

from app import reflection
from app.dependencies import get_db, get_snapshot
from app.errors import ErrorCode, _DEFAULT_MESSAGES
from app.main import app
from app.reflection import ReflectedSchema


def _rows(client):
    return client.post("/api/dbo/Widget/query", json={"page_size": 500}).json()["data"]


def _total(client):
    return client.post("/api/dbo/Widget/query", json={"page_size": 500}).json()["total"]


# ── Happy path ───────────────────────────────────────────────────────────────

def test_imports_all_rows(widget):
    body = {"rows": [
        {"Name": "Alpha", "Quantity": 1},
        {"Name": "Beta", "Quantity": 2},
        {"Name": "Gamma", "Quantity": 3},
    ]}
    resp = widget.client.post("/api/dbo/Widget/bulk-create", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"created": 3}

    rows = _rows(widget.client)
    assert {r["Name"] for r in rows} == {"Alpha", "Beta", "Gamma"}
    assert {r["Name"]: r["Quantity"] for r in rows} == {"Alpha": 1, "Beta": 2, "Gamma": 3}


def test_unknown_column_is_ignored(widget):
    resp = widget.client.post(
        "/api/dbo/Widget/bulk-create",
        json={"rows": [{"Name": "WithExtra", "Bogus": "nope"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"created": 1}
    assert _rows(widget.client)[0]["Name"] == "WithExtra"


def test_db_owned_column_is_scrubbed(widget):
    # CreatedBy is an audit column — the client can't set it on import.
    resp = widget.client.post(
        "/api/dbo/Widget/bulk-create",
        json={"rows": [{"Name": "Audited", "CreatedBy": "hacker"}]},
    )
    assert resp.status_code == 200, resp.text
    assert _rows(widget.client)[0]["CreatedBy"] is None


def test_omitted_optional_column_is_null(widget):
    resp = widget.client.post(
        "/api/dbo/Widget/bulk-create", json={"rows": [{"Name": "NoQty"}]}
    )
    assert resp.status_code == 200, resp.text
    assert _rows(widget.client)[0]["Quantity"] is None


# ── Empty / over cap ─────────────────────────────────────────────────────────

def test_empty_rows_is_400(widget):
    resp = widget.client.post("/api/dbo/Widget/bulk-create", json={"rows": []})
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"


def test_over_cap_is_400_and_creates_nothing(widget, monkeypatch):
    monkeypatch.setattr("app.routes.crud.BULK_MAX_ROWS", 2)
    resp = widget.client.post(
        "/api/dbo/Widget/bulk-create",
        json={"rows": [{"Name": f"X{i}"} for i in range(3)]},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"
    assert _total(widget.client) == 0


# ── Per-row validation (phase 1) ─────────────────────────────────────────────

def test_missing_required_field_reports_the_row(widget):
    resp = widget.client.post(
        "/api/dbo/Widget/bulk-create",
        json={"rows": [{"Name": "ok"}, {"Quantity": 5}]},  # row 1 has no Name
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "1" in body["rows"] and "Name" in body["rows"]["1"]
    assert "0" not in body["rows"]               # the valid row isn't flagged
    assert _total(widget.client) == 0            # validation fails before any insert


def test_multiple_bad_rows_all_reported(widget):
    resp = widget.client.post(
        "/api/dbo/Widget/bulk-create",
        json={"rows": [
            {"Quantity": 1},                      # missing Name
            {"Name": "ok"},                       # fine
            {"Name": "bad", "Quantity": "NaN"},   # bad int
        ]},
    )
    assert resp.status_code == 422
    rows = resp.json()["rows"]
    assert set(rows) == {"0", "2"}
    assert "Name" in rows["0"] and "Quantity" in rows["2"]


# ── Auth gate ────────────────────────────────────────────────────────────────

def test_bulk_create_without_token_is_401(snapshot_only):
    resp = snapshot_only.client.post(
        "/api/dbo/Widget/bulk-create", json={"rows": [{"Name": "x"}]}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHENTICATED"


# ── Atomicity: a constraint failure imports nothing (phase 2) ─────────────────

@pytest.fixture
def uniq():
    """
    A table with a UNIQUE column (Code) on sqlite, seeded with codes 1, 2, 3.
    Importing a batch where one row duplicates an existing code makes that row's
    insert fail; the whole import must roll back, so the count stays at 3 and the
    error names the offending row.
    """
    md = MetaData()
    thing = Table(
        "Thing", md,
        Column("ThingID", INTEGER(), primary_key=True, autoincrement=True),
        Column("Code", INTEGER(), nullable=False),
        Column("Label", NVARCHAR(50), nullable=True),
        UniqueConstraint("Code", name="UQ_Thing_Code"),
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(thing.insert(), [
            {"ThingID": 1, "Code": 1},
            {"ThingID": 2, "Code": 2},
            {"ThingID": 3, "Code": 3},
        ])

    info = reflection._build_table_info("dbo", thing, set(), {})
    snapshot = ReflectedSchema(tables={info.key: info})

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


def _thing_total(client):
    return client.post("/api/dbo/Thing/query", json={"page_size": 500}).json()["total"]


def test_constraint_violation_rolls_back_whole_import(uniq):
    # Row 0 (Code=4) is fine; row 1 (Code=2) duplicates an existing code. The
    # batch must fail as a unit — the good row is not committed either.
    resp = uniq.client.post(
        "/api/dbo/Thing/bulk-create",
        json={"rows": [{"Code": 4}, {"Code": 2}]},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "CONSTRAINT_VIOLATION"
    assert body["row"] == 1
    assert _thing_total(uniq.client) == 3        # nothing imported

    # Invariant: the user-facing message is the generic default (the route may
    # prefix safe context like "Row N:") and leaks no SQL/schema detail — the
    # underlying driver error is "UNIQUE constraint failed: Thing.Code", none of
    # which may reach the client.
    msg = body["message"]
    assert _DEFAULT_MESSAGES[ErrorCode.CONSTRAINT_VIOLATION] in msg
    for leak in ("Thing", "Code", "UNIQUE", "constraint", "UQ_Thing_Code"):
        assert leak.lower() not in msg.lower()
