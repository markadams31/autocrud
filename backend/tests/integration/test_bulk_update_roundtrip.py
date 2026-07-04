"""
Bulk update against real SQL Server — the things only a real database proves:
a set-based UPDATE applied to many rows, composite-key matching compiled to SQL
Server's OR-of-ANDs, server-managed columns (the audit trigger) reacting to the
write, and genuine transactional rollback when a foreign key makes the new value
illegal (the whole batch is undone).

Docker-gated (see integration/conftest.py).
"""

import pytest

pytestmark = pytest.mark.integration


def test_explicit_ids_update(api):
    ids = []
    for i in range(3):
        gid = api.post("/api/dbo/Gadget", json={"Name": f"bu-{i}", "Quantity": 1}).json()["GadgetID"]
        ids.append([gid])

    resp = api.post("/api/dbo/Gadget/bulk-update", json={"ids": ids, "values": {"Quantity": 99}})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 3}

    for (gid,) in ids:
        row = api.get(f"/api/dbo/Gadget/{gid}").json()
        assert row["Quantity"] == 99
        assert row["Doubled"] == 198          # computed column recomputed
        assert row["ModifiedDate"] is not None  # AFTER UPDATE trigger fired


def test_all_matching_by_search(api):
    for i in range(3):
        api.post("/api/dbo/Gadget", json={"Name": f"bu-search-{i}", "Quantity": 5})

    resp = api.post(
        "/api/dbo/Gadget/bulk-update",
        json={"all_matching": True, "search": "bu-search-", "values": {"Quantity": 0}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 3

    rows = api.post("/api/dbo/Gadget/query", json={"search": "bu-search-"}).json()["data"]
    assert rows and all(r["Quantity"] == 0 for r in rows)


def test_composite_primary_key_update(api):
    # Composite PK (OrgID, TagID) — exercises the OR-of-ANDs WHERE on real mssql.
    for org, tag in [(9101, 1), (9101, 2), (9102, 1)]:
        r = api.post("/api/dbo/Composite", json={"OrgID": org, "TagID": tag, "Note": "before"})
        assert r.status_code == 201, r.text

    resp = api.post(
        "/api/dbo/Composite/bulk-update",
        json={"ids": [[9101, 1], [9102, 1]], "values": {"Note": "after"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 2}

    assert api.get("/api/dbo/Composite/9101,1").json()["Note"] == "after"
    assert api.get("/api/dbo/Composite/9102,1").json()["Note"] == "after"
    assert api.get("/api/dbo/Composite/9101,2").json()["Note"] == "before"  # untouched


def test_cap_enforced_and_nothing_updated(api, monkeypatch):
    monkeypatch.setattr("app.routes.crud.BULK_MAX_ROWS", 2)
    ids = []
    for i in range(3):
        gid = api.post("/api/dbo/Gadget", json={"Name": f"bu-cap-{i}", "Quantity": 7}).json()["GadgetID"]
        ids.append([gid])

    resp = api.post("/api/dbo/Gadget/bulk-update", json={"ids": ids, "values": {"Quantity": 0}})
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"
    for (gid,) in ids:  # the over-cap request changed nothing
        assert api.get(f"/api/dbo/Gadget/{gid}").json()["Quantity"] == 7


def test_atomic_rollback_on_foreign_key(api):
    # Two projects on a valid category; bulk-setting their CategoryID to a
    # non-existent one violates the FK, so the whole batch must roll back and
    # both keep their original category.
    cat = api.post(
        "/api/dbo/Category", json={"CategoryCode": "BUREF", "CategoryName": "bu-ref"}
    ).json()["CategoryID"]
    ids = []
    for i in range(2):
        pid = api.post(
            "/api/dbo/Project",
            json={
                "ProjectName": f"bu-proj-{i}",
                "CategoryID": cat,
                "ManagerID": 1,
                "SponsorID": 2,
                "StartDate": "2025-01-01",
                "EndDate": "2025-02-01",
            },
        ).json()["ProjectID"]
        ids.append([pid])

    resp = api.post(
        "/api/dbo/Project/bulk-update",
        json={"ids": ids, "values": {"CategoryID": 9_999_999}},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONSTRAINT_VIOLATION"

    for (pid,) in ids:  # neither moved off the real category
        assert api.get(f"/api/dbo/Project/{pid}").json()["CategoryID"] == cat
