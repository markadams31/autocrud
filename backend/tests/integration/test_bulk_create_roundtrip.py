"""
Bulk create against real SQL Server — the things only a real database proves:
many rows imported in one transaction with server-managed columns populating
(computed, value-generating defaults, the audit trigger), manual and composite
primary keys, and genuine transactional rollback when a row's insert violates a
primary key or foreign key (the whole import is undone, and the failing row is
named).

Docker-gated (see integration/conftest.py).
"""

import pytest

pytestmark = pytest.mark.integration


def test_imports_gadgets_with_server_columns(api):
    body = {"rows": [{"Name": f"bc-imp-{i}", "Quantity": i} for i in range(3)]}
    resp = api.post("/api/dbo/Gadget/bulk-create", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"created": 3}

    rows = api.post("/api/dbo/Gadget/query", json={"search": "bc-imp-"}).json()["data"]
    assert len(rows) == 3
    for r in rows:
        assert r["Doubled"] == r["Quantity"] * 2   # computed column
        assert r["Status"] == 0                     # plain default
        assert r["Token"] is not None               # NEWID() default
        assert r["CreatedDate"] is not None         # AFTER INSERT audit trigger


def test_manual_and_composite_primary_keys(api):
    manual = api.post(
        "/api/dbo/ManualKey/bulk-create",
        json={"rows": [{"Code": "BCK1", "Label": "one"}, {"Code": "BCK2", "Label": "two"}]},
    )
    assert manual.status_code == 200, manual.text
    assert manual.json() == {"created": 2}
    assert api.get("/api/dbo/ManualKey/BCK1").json()["Label"] == "one"

    comp = api.post(
        "/api/dbo/Composite/bulk-create",
        json={"rows": [
            {"OrgID": 9201, "TagID": 1, "Note": "x"},
            {"OrgID": 9201, "TagID": 2, "Note": "y"},
        ]},
    )
    assert comp.status_code == 200, comp.text
    assert comp.json() == {"created": 2}
    assert api.get("/api/dbo/Composite/9201,2").json()["Note"] == "y"


def test_atomic_rollback_on_duplicate_primary_key(api):
    # Seed one composite row, then import a batch whose second row duplicates it.
    seed = api.post("/api/dbo/Composite", json={"OrgID": 9301, "TagID": 1, "Note": "seed"})
    assert seed.status_code == 201, seed.text

    resp = api.post(
        "/api/dbo/Composite/bulk-create",
        json={"rows": [
            {"OrgID": 9301, "TagID": 5, "Note": "new"},   # fine on its own
            {"OrgID": 9301, "TagID": 1, "Note": "dup"},   # duplicates the seed
        ]},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONSTRAINT_VIOLATION"
    assert resp.json()["row"] == 1

    # The whole import rolled back — the otherwise-valid first row didn't land.
    assert api.get("/api/dbo/Composite/9301,5").status_code == 404


def test_out_of_range_value_is_a_clean_conflict(api):
    # 9_999_999_999 overflows the 32-bit INT Quantity column. Pydantic only
    # bounds string length, so it reaches the database, which raises a DataError.
    # That must map to a clean 409 (not a 500), attributed to the offending row,
    # with nothing committed.
    resp = api.post(
        "/api/dbo/Gadget/bulk-create",
        json={"rows": [
            {"Name": "bc-range-ok", "Quantity": 1},
            {"Name": "bc-range-bad", "Quantity": 9_999_999_999},
        ]},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "CONSTRAINT_VIOLATION"
    assert resp.json()["row"] == 1
    assert api.post("/api/dbo/Gadget/query", json={"search": "bc-range-"}).json()["total"] == 0


def test_atomic_rollback_on_foreign_key(api):
    cat = api.post(
        "/api/dbo/Category", json={"CategoryCode": "BCFK", "CategoryName": "bc-fk"}
    ).json()["CategoryID"]
    base = {
        "ManagerID": 1,
        "SponsorID": 2,
        "StartDate": "2025-01-01",
        "EndDate": "2025-02-01",
    }
    resp = api.post(
        "/api/dbo/Project/bulk-create",
        json={"rows": [
            {"ProjectName": "bc-proj-ok", "CategoryID": cat, **base},
            {"ProjectName": "bc-proj-bad", "CategoryID": 9_999_999, **base},  # no such category
        ]},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONSTRAINT_VIOLATION"
    assert resp.json()["row"] == 1

    # Neither project was committed.
    assert api.post("/api/dbo/Project/query", json={"search": "bc-proj-"}).json()["total"] == 0
