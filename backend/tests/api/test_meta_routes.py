"""
Metadata endpoints via the FastAPI TestClient, with the snapshot injected.

/meta and /meta/{schema}/{table} are served from the in-memory snapshot.
/meta/{schema} runs a SQL Server permission query — exercised here with a fake
connection so the filtering logic is tested without a real database.
"""

from types import SimpleNamespace

from app.dependencies import get_db


def test_list_schemas_returns_database_and_schemas(widget):
    resp = widget.client.get("/meta")
    assert resp.status_code == 200
    assert resp.json() == {"database": "testdb", "schemas": ["dbo"]}


def test_describe_table(widget):
    resp = widget.client.get("/meta/dbo/Widget")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema"] == "dbo"
    assert body["name"] == "Widget"
    assert body["primary_key"] == ["WidgetID"]
    assert body["display_column"] == "Name"

    cols = {c["name"]: c for c in body["columns"]}
    assert cols["WidgetID"]["is_primary_key"] is True
    assert cols["Name"]["required"] is True
    # Precise SQL type is exposed for the header hover-card. The exact string
    # varies by backend, so just assert it's present and non-empty.
    assert isinstance(cols["WidgetID"]["sql_type"], str) and cols["WidgetID"]["sql_type"]
    # Audit columns are classified DB-owned by name (works on any backend).
    assert cols["CreatedBy"]["is_audit"] is True
    assert cols["CreatedBy"]["editable"] is False


def test_describe_unknown_table_404(widget):
    resp = widget.client.get("/meta/dbo/DoesNotExist")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_list_tables_filters_by_permission(widget):
    # Replace the DB dependency with a fake connection returning crafted
    # HAS_PERMS_BY_NAME rows: the user can read+delete Widget but not update it.
    rows = [
        SimpleNamespace(
            tname="Widget", can_select=1, can_insert=1, can_update=0, can_delete=1
        )
    ]

    class _Result:
        def fetchall(self):
            return rows

    class _Conn:
        def execute(self, *a, **k):
            return _Result()

    widget.client.app.dependency_overrides[get_db] = lambda: _Conn()

    resp = widget.client.get("/meta/dbo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema"] == "dbo"
    assert len(body["tables"]) == 1
    table = body["tables"][0]
    assert table["name"] == "Widget"
    assert table["display_column"] == "Name"
    assert table["primary_key"] == ["WidgetID"]
    assert table["permissions"] == {"insert": True, "update": False, "delete": True}


def test_list_tables_hides_unreadable_tables(widget):
    # No SELECT → the table must not appear at all.
    rows = [
        SimpleNamespace(
            tname="Widget", can_select=0, can_insert=0, can_update=0, can_delete=0
        )
    ]

    class _Result:
        def fetchall(self):
            return rows

    class _Conn:
        def execute(self, *a, **k):
            return _Result()

    widget.client.app.dependency_overrides[get_db] = lambda: _Conn()

    resp = widget.client.get("/meta/dbo")
    assert resp.status_code == 200
    assert resp.json()["tables"] == []


# ── FK options endpoint ──────────────────────────────────────────────────────
# The happy path uses SQL Server's `TOP` syntax, so the value/label result is
# covered by the integration tier (sqlite can't run it). Here we cover the
# request-validation branches and the graceful-empty fallback.

def test_options_unknown_column_is_400(widget):
    resp = widget.client.get("/meta/dbo/Widget/options/Nope")
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"


def test_options_non_fk_column_is_400(widget):
    # Name is a plain text column, not a foreign key.
    resp = widget.client.get("/meta/dbo/Widget/options/Name")
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"


def test_options_returns_empty_when_target_not_in_snapshot(widget):
    # Point a column at a table the snapshot doesn't know — the endpoint degrades
    # to an empty list (schema may have changed) rather than erroring.
    col = widget.table_info.column("Quantity")
    object.__setattr__(col, "foreign_key", ("dbo", "Ghost", "GhostID"))
    try:
        resp = widget.client.get("/meta/dbo/Widget/options/Quantity")
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        object.__setattr__(col, "foreign_key", None)
