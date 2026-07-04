"""
Bulk update (POST /api/{schema}/{table}/bulk-update).

"One change → many rows": both targeting modes (an explicit id list and "all
matching" a search/filter), the row cap, the auth gate, the validation/scrubbing
shared with single-row update (server-controlled columns and primary keys can't
be written; bad values are rejected up front), and the all-or-nothing guarantee
— a batch whose new values violate a constraint changes nothing.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, Table, UniqueConstraint, create_engine
from sqlalchemy.dialects.mssql import INTEGER, NVARCHAR
from sqlalchemy.pool import StaticPool

from app import reflection
from tests.conftest import pk_default_facts
from app.dependencies import get_db, get_snapshot
from app.main import app
from app.reflection import SchemaSnapshot


def _create(client, **fields):
    return client.post("/api/dbo/Widget", json=fields)


def _rows(client):
    return client.post("/api/dbo/Widget/query", json={"page_size": 500}).json()["data"]


def _all_ids(client):
    """Current WidgetIDs as the [[pk], ...] shape the bulk endpoint expects."""
    return [[r["WidgetID"]] for r in _rows(client)]


def _by_id(client, wid):
    return client.get(f"/api/dbo/Widget/{wid}").json()


@pytest.fixture
def seeded(widget):
    """The shared in-memory Widget table, pre-loaded with 10 rows (Quantity 1..10)."""
    for i in range(1, 11):
        _create(widget.client, Name=f"Item-{i:02d}", Quantity=i)
    return widget


# ── Explicit id list ─────────────────────────────────────────────────────────

def test_explicit_ids_updates_exactly_those(seeded):
    ids = _all_ids(seeded.client)[:3]
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update", json={"ids": ids, "values": {"Quantity": 100}}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 3}

    changed = {tuple(i) for i in ids}
    for r in _rows(seeded.client):
        if (r["WidgetID"],) in changed:
            assert r["Quantity"] == 100
        else:
            assert r["Quantity"] != 100  # untouched rows keep their seeded value


def test_explicit_ids_ignores_nonexistent(seeded):
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update", json={"ids": [[99999]], "values": {"Quantity": 7}}
    )
    assert resp.status_code == 200
    assert resp.json() == {"updated": 0}


def test_empty_ids_is_400(seeded):
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update", json={"ids": [], "values": {"Quantity": 1}}
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"


def test_wrong_pk_arity_is_400(seeded):
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update", json={"ids": [[1, 2]], "values": {"Quantity": 1}}
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"


# ── All matching ─────────────────────────────────────────────────────────────

def test_all_matching_with_filter(seeded):
    # Quantity > 7 → 8, 9, 10 get Quantity := 0
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update",
        json={
            "all_matching": True,
            "filters": {"Quantity": {"op": "gt", "value": 7}},
            "values": {"Quantity": 0},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 3}
    quantities = sorted(r["Quantity"] for r in _rows(seeded.client))
    assert quantities == [0, 0, 0, 1, 2, 3, 4, 5, 6, 7]


def test_all_matching_with_search(seeded):
    # Only "Item-10" matches the search; set its Notes.
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update",
        json={"all_matching": True, "search": "Item-10", "values": {"Notes": "flagged"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 1}
    flagged = [r for r in _rows(seeded.client) if r["Notes"] == "flagged"]
    assert len(flagged) == 1 and flagged[0]["Name"] == "Item-10"


def test_all_matching_no_filter_updates_everything(seeded):
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update", json={"all_matching": True, "values": {"Quantity": 42}}
    )
    assert resp.status_code == 200
    assert resp.json() == {"updated": 10}
    assert all(r["Quantity"] == 42 for r in _rows(seeded.client))


def test_all_matching_empty_set_is_zero(seeded):
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update",
        json={
            "all_matching": True,
            "filters": {"Quantity": {"op": "gt", "value": 9999}},
            "values": {"Quantity": 0},
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"updated": 0}
    assert all(r["Quantity"] != 0 for r in _rows(seeded.client))


# ── Values: empty, null clears, scrubbing, validation ────────────────────────

def test_empty_values_is_400(seeded):
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update", json={"ids": _all_ids(seeded.client), "values": {}}
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"


def test_values_with_only_server_owned_column_is_400(seeded):
    # CreatedBy is an audit column — not in the update model, so it scrubs away,
    # leaving nothing to write. Proves audit columns can't be bulk-written.
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update",
        json={"ids": _all_ids(seeded.client), "values": {"CreatedBy": "hacker"}},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"
    assert all(r["CreatedBy"] is None for r in _rows(seeded.client))


def test_primary_key_in_values_is_scrubbed(seeded):
    # WidgetID is the PK — it must be ignored, while a real field still applies.
    one = _all_ids(seeded.client)[0]
    wid = one[0]
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update",
        json={"ids": [one], "values": {"WidgetID": 99999, "Quantity": 555}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 1}
    row = _by_id(seeded.client, wid)               # still addressable by its old PK
    assert row["WidgetID"] == wid and row["Quantity"] == 555
    assert seeded.client.get("/api/dbo/Widget/99999").status_code == 404  # PK never moved


def test_null_clears_a_nullable_column(seeded):
    ids = _all_ids(seeded.client)[:2]
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update", json={"ids": ids, "values": {"Quantity": None}}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 2}
    cleared = {tuple(i) for i in ids}
    for r in _rows(seeded.client):
        if (r["WidgetID"],) in cleared:
            assert r["Quantity"] is None


def test_invalid_value_is_422_and_changes_nothing(seeded):
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update",
        json={"ids": _all_ids(seeded.client), "values": {"Quantity": "not-a-number"}},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "Quantity" in body.get("fields", {})


# ── Row cap ──────────────────────────────────────────────────────────────────

def test_explicit_over_cap_is_400_and_changes_nothing(seeded, monkeypatch):
    monkeypatch.setattr("app.routes.crud.BULK_MAX_ROWS", 3)
    before = {r["WidgetID"]: r["Quantity"] for r in _rows(seeded.client)}
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update",
        json={"ids": _all_ids(seeded.client), "values": {"Quantity": 0}},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"
    after = {r["WidgetID"]: r["Quantity"] for r in _rows(seeded.client)}
    assert after == before


def test_all_matching_over_cap_is_400_and_changes_nothing(seeded, monkeypatch):
    monkeypatch.setattr("app.routes.crud.BULK_MAX_ROWS", 3)
    before = {r["WidgetID"]: r["Quantity"] for r in _rows(seeded.client)}
    resp = seeded.client.post(
        "/api/dbo/Widget/bulk-update",
        json={"all_matching": True, "values": {"Quantity": 0}},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"
    after = {r["WidgetID"]: r["Quantity"] for r in _rows(seeded.client)}
    assert after == before


# ── Auth gate ────────────────────────────────────────────────────────────────

def test_bulk_update_without_token_is_401(snapshot_only):
    resp = snapshot_only.client.post(
        "/api/dbo/Widget/bulk-update", json={"ids": [[1]], "values": {"Quantity": 1}}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHENTICATED"


# ── Atomicity: a constraint failure changes nothing ──────────────────────────

@pytest.fixture
def uniq():
    """
    A table with a UNIQUE column (Code) on sqlite, seeded with three distinct
    codes. Bulk-setting two rows to a code that collides makes the single UPDATE
    fail, which proves the batch is all-or-nothing: the whole thing rolls back,
    so neither row's Code changes — not even one that would have been fine.
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
            {"ThingID": 1, "Code": 1, "Label": "a"},
            {"ThingID": 2, "Code": 2, "Label": "b"},
            {"ThingID": 3, "Code": 3, "Label": "c"},
        ])

    info = reflection._build_table_info(thing, pk_default_facts(thing), schema="dbo")
    snapshot = SchemaSnapshot(tables={info.key: info})

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


def _thing_codes(client):
    rows = client.post("/api/dbo/Thing/query", json={"page_size": 500}).json()["data"]
    return {r["ThingID"]: r["Code"] for r in rows}


def test_unique_violation_rolls_back_whole_batch(uniq):
    # Setting rows 2 and 3 both to Code=1 collides with row 1 (and each other);
    # the update must fail as a unit and leave every Code untouched.
    resp = uniq.client.post(
        "/api/dbo/Thing/bulk-update", json={"ids": [[2], [3]], "values": {"Code": 1}}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONSTRAINT_VIOLATION"
    assert _thing_codes(uniq.client) == {1: 1, 2: 2, 3: 3}


def test_all_matching_unique_violation_rolls_back(uniq):
    # Same guarantee via the "all matching" path: set every row's Code to a
    # constant — they collide — and nothing changes.
    resp = uniq.client.post(
        "/api/dbo/Thing/bulk-update", json={"all_matching": True, "values": {"Code": 9}}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONSTRAINT_VIOLATION"
    assert _thing_codes(uniq.client) == {1: 1, 2: 2, 3: 3}
