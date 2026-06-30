"""
CRUD endpoints end-to-end against a live (in-memory sqlite) table, so route
logic runs for real: payload scrubbing, validation, search/filter/sort/paging,
partial updates, and the standard error contract.
"""

import logging

import pytest


def _create(client, **fields):
    return client.post("/api/dbo/Widget", json=fields)


def test_create_returns_row_and_assigns_pk(widget):
    resp = _create(widget.client, Name="Gizmo", Quantity=5)
    assert resp.status_code == 201
    body = resp.json()
    assert body["Name"] == "Gizmo"
    assert body["Quantity"] == 5
    assert body["WidgetID"] is not None


def test_create_scrubs_db_owned_columns(widget):
    # Client tries to set the identity PK and an audit column — both must be
    # ignored, leaving the database in control.
    resp = _create(widget.client, Name="Sneaky", WidgetID=999, CreatedBy="attacker")
    assert resp.status_code == 201
    body = resp.json()
    assert body["WidgetID"] != 999          # identity assigned by the DB
    assert body["CreatedBy"] is None        # audit column never written by client


def test_create_ignores_unknown_fields(widget):
    resp = _create(widget.client, Name="Clean", bogus="dropped")
    assert resp.status_code == 201
    assert "bogus" not in resp.json()


def test_create_missing_required_field_is_422(widget):
    resp = _create(widget.client, Quantity=1)   # Name omitted
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "Name" in body["fields"]


def test_get_row(widget):
    pk = _create(widget.client, Name="Fetchable").json()["WidgetID"]
    resp = widget.client.get(f"/api/dbo/Widget/{pk}")
    assert resp.status_code == 200
    assert resp.json()["Name"] == "Fetchable"


def test_get_unknown_row_404(widget):
    resp = widget.client.get("/api/dbo/Widget/99999")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_patch_updates_only_supplied_fields(widget):
    pk = _create(widget.client, Name="Original", Quantity=1).json()["WidgetID"]
    resp = widget.client.patch(f"/api/dbo/Widget/{pk}", json={"Quantity": 42})
    assert resp.status_code == 200
    body = resp.json()
    assert body["Quantity"] == 42
    assert body["Name"] == "Original"        # untouched


def test_put_behaves_as_partial_update(widget):
    pk = _create(widget.client, Name="Putt", Quantity=1).json()["WidgetID"]
    resp = widget.client.put(f"/api/dbo/Widget/{pk}", json={"Quantity": 7})
    assert resp.status_code == 200
    assert resp.json()["Quantity"] == 7


def test_update_unknown_row_404(widget):
    resp = widget.client.patch("/api/dbo/Widget/99999", json={"Quantity": 1})
    assert resp.status_code == 404


def test_delete_then_gone(widget):
    pk = _create(widget.client, Name="Doomed").json()["WidgetID"]
    resp = widget.client.delete(f"/api/dbo/Widget/{pk}")
    assert resp.status_code == 200
    # Single delete reports a row count (int 1), the same shape as bulk-delete's
    # {"deleted": <count>} — not the pk echoed back as a string.
    assert resp.json() == {"deleted": 1}
    assert widget.client.get(f"/api/dbo/Widget/{pk}").status_code == 404


def test_delete_unknown_row_404(widget):
    assert widget.client.delete("/api/dbo/Widget/99999").status_code == 404


def test_delete_logs_the_user(widget, caplog):
    # A delete removes the row, so the application log is the only record of who
    # did it — the DELETE line must carry user=, like INSERT/UPDATE.
    pk = _create(widget.client, Name="Doomed").json()["WidgetID"]
    with caplog.at_level(logging.INFO, logger="app.routes.crud"):
        resp = widget.client.delete(
            f"/api/dbo/Widget/{pk}",
            headers={"X-MS-CLIENT-PRINCIPAL-NAME": "ada@example.com"},
        )
    assert resp.status_code == 200
    delete_lines = [
        r.getMessage() for r in caplog.records
        if r.name == "app.routes.crud" and r.getMessage().startswith("DELETE")
    ]
    assert delete_lines and "user=ada@example.com" in delete_lines[0]


# ── Query: search / filter / sort / paginate ─────────────────────────────────

@pytest.fixture
def populated(widget):
    for i in range(1, 26):
        _create(widget.client, Name=f"Item-{i:02d}", Quantity=i)
    return widget


def test_query_returns_paginated_envelope(populated):
    resp = populated.client.post("/api/dbo/Widget/query", json={"page": 1, "page_size": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 25
    assert body["page"] == 1
    assert body["page_size"] == 10
    assert body["pages"] == 3
    assert len(body["data"]) == 10


def test_query_search_matches_text_columns(populated):
    resp = populated.client.post("/api/dbo/Widget/query", json={"search": "Item-07"})
    body = resp.json()
    assert body["total"] == 1
    assert body["data"][0]["Name"] == "Item-07"


def test_query_filter_exact_match(populated):
    resp = populated.client.post("/api/dbo/Widget/query", json={"filters": {"Quantity": 5}})
    body = resp.json()
    assert body["total"] == 1
    assert body["data"][0]["Quantity"] == 5


def test_query_filter_in_list(populated):
    resp = populated.client.post(
        "/api/dbo/Widget/query", json={"filters": {"Quantity": [1, 2, 3]}}
    )
    assert resp.json()["total"] == 3


# ── Query: operator filters (beyond equality) ────────────────────────────────

def _op(client, column, op, value=None):
    spec = {"op": op} if value is None else {"op": op, "value": value}
    return client.post("/api/dbo/Widget/query", json={"filters": {column: spec}}).json()


def test_query_filter_gt(populated):
    body = _op(populated.client, "Quantity", "gt", 20)
    assert body["total"] == 5                          # 21..25
    assert all(r["Quantity"] > 20 for r in body["data"])


def test_query_filter_lte(populated):
    assert _op(populated.client, "Quantity", "lte", 3)["total"] == 3   # 1,2,3


def test_query_filter_between(populated):
    body = _op(populated.client, "Quantity", "between", [10, 12])
    assert sorted(r["Quantity"] for r in body["data"]) == [10, 11, 12]


def test_query_filter_contains(populated):
    # 'Item-1' matches Item-10..Item-19 (Item-01 does not contain it) → 10 rows
    assert _op(populated.client, "Name", "contains", "Item-1")["total"] == 10


def test_query_filter_contains_escapes_wildcards(populated):
    # A literal '%' must not act as a wildcard — no Widget name contains it.
    assert _op(populated.client, "Name", "contains", "%")["total"] == 0


def test_query_search_escapes_wildcards(populated):
    # The global `search` box runs through its own LIKE path (not the filter
    # operators above), so it gets its own escaping check. Names are "Item-01".."Item-25":
    # neither '%' nor '_' appears literally, so an *unescaped* wildcard would
    # match all 25 rows. Escaped, both match nothing.
    def search(term):
        return populated.client.post("/api/dbo/Widget/query", json={"search": term}).json()

    assert search("%")["total"] == 0   # '%' wildcard would otherwise match every row
    assert search("_")["total"] == 0   # '_' single-char wildcard likewise
    assert search("Item-0")["total"] == 9   # sanity: a real substring still matches (Item-01..09)


def test_query_filter_null_tests(widget):
    _create(widget.client, Name="HasQty", Quantity=7)
    _create(widget.client, Name="NoQty")  # Quantity omitted → NULL
    assert _op(widget.client, "Quantity", "isnull")["total"] == 1
    assert _op(widget.client, "Quantity", "notnull")["total"] == 1


def test_query_filter_incomplete_is_ignored(populated):
    # A value-requiring operator with an empty/partial value constrains nothing.
    assert _op(populated.client, "Quantity", "gt", "")["total"] == 25
    assert _op(populated.client, "Quantity", "between", [None, 5])["total"] == 25


def test_query_sort_descending(populated):
    resp = populated.client.post(
        "/api/dbo/Widget/query",
        json={"sort": {"column": "Quantity", "direction": "desc"}, "page_size": 3},
    )
    quantities = [r["Quantity"] for r in resp.json()["data"]]
    assert quantities == [25, 24, 23]


# ── Query: pagination boundaries ─────────────────────────────────────────────

def test_query_page_size_is_capped_at_500(populated):
    # A caller asking for an enormous page is clamped to the server cap, not
    # allowed to pull unbounded rows.
    body = populated.client.post("/api/dbo/Widget/query", json={"page_size": 100000}).json()
    assert body["page_size"] == 500          # echoed value is the clamp
    assert len(body["data"]) == 25           # only 25 rows exist


def test_query_page_beyond_last_is_empty_but_well_formed(populated):
    body = populated.client.post(
        "/api/dbo/Widget/query", json={"page": 99, "page_size": 10}
    ).json()
    assert body["total"] == 25
    assert body["pages"] == 3
    assert body["data"] == []                # past the end → empty page, not an error


def test_query_page_zero_or_negative_clamps_to_first(populated):
    for p in (0, -5):
        body = populated.client.post(
            "/api/dbo/Widget/query", json={"page": p, "page_size": 10}
        ).json()
        assert body["page"] == 1
        assert len(body["data"]) == 10


def test_query_page_size_zero_clamps_to_one(populated):
    body = populated.client.post("/api/dbo/Widget/query", json={"page_size": 0}).json()
    assert body["page_size"] == 1
    assert len(body["data"]) == 1


# ── Update: server-controlled columns can't be changed via PATCH/PUT ─────────

def test_update_ignores_primary_key_and_db_owned_columns(widget):
    # Only the editable Quantity should change. Attempts to repoint the identity
    # PK or write the audit column must be scrubbed, leaving the DB in control.
    pk = _create(widget.client, Name="Original", Quantity=1).json()["WidgetID"]
    resp = widget.client.patch(
        f"/api/dbo/Widget/{pk}",
        json={"WidgetID": 9999, "CreatedBy": "attacker", "Quantity": 2},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["WidgetID"] == pk            # PK is addressed by the URL, never rewritten
    assert body["CreatedBy"] is None         # audit column stays DB-owned
    assert body["Quantity"] == 2             # the one editable change landed
    # The row is still at its original PK — proving WidgetID truly wasn't moved.
    assert widget.client.get(f"/api/dbo/Widget/{pk}").status_code == 200
    assert widget.client.get("/api/dbo/Widget/9999").status_code == 404
