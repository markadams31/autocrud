"""
Bulk delete against real SQL Server — the things only a real database proves:
composite-key matching compiled to SQL Server's OR-of-ANDs, set-based "all
matching" deletes, and genuine transactional rollback when a foreign key pins a
row (the whole batch is undone, not just the offending row).

Docker-gated (see integration/conftest.py).
"""

import pytest

pytestmark = pytest.mark.integration


def test_explicit_ids_delete(api):
    ids = []
    for i in range(3):
        gid = api.post("/api/dbo/Gadget", json={"Name": f"bd-{i}"}).json()["GadgetID"]
        ids.append([gid])

    resp = api.post("/api/dbo/Gadget/bulk-delete", json={"ids": ids})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 3}
    for (gid,) in ids:
        assert api.get(f"/api/dbo/Gadget/{gid}").status_code == 404


def test_all_matching_by_search(api):
    for i in range(3):
        api.post("/api/dbo/Gadget", json={"Name": f"bulk-mark-{i}", "Quantity": 1})

    resp = api.post(
        "/api/dbo/Gadget/bulk-delete", json={"all_matching": True, "search": "bulk-mark-"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 3
    assert api.post("/api/dbo/Gadget/query", json={"search": "bulk-mark-"}).json()["total"] == 0


def test_composite_primary_key_delete(api):
    # Composite PK (OrgID, TagID) — exercises the OR-of-ANDs WHERE on real mssql.
    for org, tag in [(9001, 1), (9001, 2), (9002, 1)]:
        r = api.post("/api/dbo/Composite", json={"OrgID": org, "TagID": tag, "Note": "x"})
        assert r.status_code == 201, r.text

    resp = api.post("/api/dbo/Composite/bulk-delete", json={"ids": [[9001, 1], [9002, 1]]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 2

    assert api.get("/api/dbo/Composite/9001,1").status_code == 404
    assert api.get("/api/dbo/Composite/9002,1").status_code == 404
    assert api.get("/api/dbo/Composite/9001,2").status_code == 200  # untouched


def test_cap_enforced_and_nothing_deleted(api, monkeypatch):
    monkeypatch.setattr("app.routes.crud.BULK_MAX_ROWS", 2)
    ids = []
    for i in range(3):
        gid = api.post("/api/dbo/Gadget", json={"Name": f"cap-{i}"}).json()["GadgetID"]
        ids.append([gid])

    resp = api.post("/api/dbo/Gadget/bulk-delete", json={"ids": ids})
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"
    for (gid,) in ids:  # the over-cap request deleted nothing
        assert api.get(f"/api/dbo/Gadget/{gid}").status_code == 200


def test_atomic_rollback_on_foreign_key(api):
    # Two fresh categories; a project references the first, pinning it against
    # deletion. Deleting both together must fail as a unit and leave both.
    a = api.post(
        "/api/dbo/Category", json={"CategoryCode": "BDREF", "CategoryName": "bd-ref"}
    ).json()["CategoryID"]
    b = api.post(
        "/api/dbo/Category", json={"CategoryCode": "BDFREE", "CategoryName": "bd-free"}
    ).json()["CategoryID"]
    pinned = api.post(
        "/api/dbo/Project",
        json={
            "ProjectName": "pins-a",
            "CategoryID": a,
            "ManagerID": 1,
            "SponsorID": 2,
            "StartDate": "2025-01-01",
            "EndDate": "2025-02-01",
        },
    )
    assert pinned.status_code == 201, pinned.text

    resp = api.post("/api/dbo/Category/bulk-delete", json={"ids": [[a], [b]]})
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONSTRAINT_VIOLATION"

    # Neither was deleted — the unreferenced one too, proving all-or-nothing.
    assert api.get(f"/api/dbo/Category/{a}").status_code == 200
    assert api.get(f"/api/dbo/Category/{b}").status_code == 200
