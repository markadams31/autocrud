"""
End-to-end CRUD against real SQL Server through the FastAPI routes — proving
the things only a real database exercises: SELECT SCOPE_IDENTITY() inserts on
trigger-carrying tables (implicit RETURNING disabled), computed columns, value-
generating defaults, and audit triggers stamping the real caller.

Uses dbo.Gadget (plain types) for row round-trips and dbo.Project for the
foreign-key paths. dbo.AllTypes is reflection-only. The CLR/sql_variant columns
that read back as CAST text (ColumnInfo.read_as_text) get their own read
round-trip in test_crud_clr_types.py.

Docker-gated (see integration/conftest.py).
"""

import pytest

pytestmark = pytest.mark.integration


def test_insert_returns_generated_values(api):
    resp = api.post("/api/dbo/Gadget", json={"Name": "hello", "Quantity": 21})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["GadgetID"] is not None      # identity via SCOPE_IDENTITY
    assert body["Doubled"] == 42             # computed (Quantity * 2)
    assert body["Status"] == 0               # plain DEFAULT
    assert body["Token"] is not None         # DEFAULT NEWID()
    assert body["CreatedBy"] == "sa"         # audit trigger, real caller
    assert body["CreatedDate"] is not None


def test_insert_scrubs_db_owned_columns(api):
    resp = api.post(
        "/api/dbo/Gadget",
        json={"Name": "x", "Quantity": 5, "GadgetID": 777, "CreatedBy": "attacker", "Doubled": 999},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["GadgetID"] != 777           # identity assigned by the DB
    assert body["CreatedBy"] == "sa"         # audit column never client-set
    assert body["Doubled"] == 10             # computed (5 * 2), not 999


def test_missing_required_is_422(api):
    resp = api.post("/api/dbo/Gadget", json={"Quantity": 1})  # Name omitted
    assert resp.status_code == 422
    assert "Name" in resp.json()["fields"]


def test_update_recomputes_and_stamps_then_deletes(api):
    pk = api.post("/api/dbo/Gadget", json={"Name": "orig", "Quantity": 1}).json()["GadgetID"]

    upd = api.patch(f"/api/dbo/Gadget/{pk}", json={"Quantity": 50})
    assert upd.status_code == 200
    body = upd.json()
    assert body["Quantity"] == 50
    assert body["Doubled"] == 100            # computed column recalculated
    assert body["ModifiedBy"] == "sa"        # audit trigger on UPDATE

    assert api.delete(f"/api/dbo/Gadget/{pk}").status_code == 200
    assert api.get(f"/api/dbo/Gadget/{pk}").status_code == 404


def test_query_search(api):
    api.post("/api/dbo/Gadget", json={"Name": "needle-abc123"})
    resp = api.post("/api/dbo/Gadget/query", json={"search": "needle-abc123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(r["Name"] == "needle-abc123" for r in body["data"])


# ── Foreign-key paths (dbo.Project) ──────────────────────────────────────────

def test_foreign_keys_and_computed_persisted(api):
    # Category #1 and Employees #1/#2 are seeded by schema.sql.
    resp = api.post(
        "/api/dbo/Project",
        json={
            "ProjectName": "Apollo",
            "CategoryID": 1,
            "ManagerID": 1,
            "SponsorID": 2,
            "StartDate": "2025-01-01",
            "EndDate": "2025-12-31",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ProjectID"] is not None
    assert body["DurationDays"] == 364       # computed persisted (DATEDIFF)
    assert body["CreatedBy"] == "sa"


def test_invalid_foreign_key_is_constraint_violation(api):
    resp = api.post(
        "/api/dbo/Project",
        json={
            "ProjectName": "Bad FK",
            "CategoryID": 9999,              # no such category
            "ManagerID": 1,
            "SponsorID": 2,
            "StartDate": "2025-01-01",
            "EndDate": "2025-02-01",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONSTRAINT_VIOLATION"


def test_check_violation_passes_sql_servers_message_through(api):
    # Database errors reach the client verbatim (internal tool; see errors.py):
    # SQL Server's own CHECK-violation message names the constraint, table and
    # column — precise feedback with no parsing layer to drift out of date.
    # Only a real database enforces the CHECK, so this can only run here.
    resp = api.post("/api/dbo/Checked", json={"Name": "over", "Score": 999})
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "CONSTRAINT_VIOLATION"
    assert "CK_Checked_Score" in body["message"]     # SQL Server names the rule
    assert "CHECK" in body["message"]


def test_options_returns_value_label_pairs_for_fk(api):
    # Project.CategoryID references dbo.Category, whose display column is
    # CategoryName, so options pair each id with its human label. (The happy
    # path uses SQL Server's TOP syntax, so it can only run here, not on sqlite.)
    resp = api.get("/meta/dbo/Project/options/CategoryID")
    assert resp.status_code == 200, resp.text
    opts = resp.json()
    assert {"value": 1, "label": "Engineering"} in opts          # seeded category
    # Shape: ints paired with strings. (Ordering isn't asserted — it follows SQL
    # Server's collation, which other tests' rows make non-trivial to predict.)
    assert all(isinstance(o["value"], int) and isinstance(o["label"], str) for o in opts)
