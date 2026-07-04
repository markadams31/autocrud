"""
Read path for CLR / spatial / sql_variant columns + SQL Server 2025 json.

The driver returns raw CLR-internal bytes for hierarchyid/geometry/geography —
useless through a JSON API — and sql_variant has no fixed JSON shape. Reflection
flags those columns ColumnInfo.read_as_text and the read path CASTs them to
NVARCHAR (routes/crud._read_columns). These tests prove a row round-trips through
the real driver as human-usable text (WKT / path / value).

Docker-gated (see integration/conftest.py).
"""

import json

import pytest

pytestmark = pytest.mark.integration


def test_get_row_with_clr_columns_returns_text(api):
    # dbo.Spatial row #1 is seeded by schema.sql with geography/geometry/
    # hierarchyid/sql_variant values a plain SELECT could not fetch back.
    resp = api.get("/api/dbo/Spatial/1")
    assert resp.status_code == 200, resp.text
    row = resp.json()
    assert row["Name"] == "origin"
    assert row["Geo"] == "POINT (-122 47)"           # geography → WKT
    assert row["Shape"] == "LINESTRING (0 0, 1 1)"   # geometry → WKT
    assert row["Node"] == "/1/2/"                     # hierarchyid → path string
    assert row["Variant"] == "42"                     # sql_variant → text
    assert json.loads(row["Doc"]) == {"k": 1}         # native json (2025)


def test_query_table_with_clr_columns_does_not_500(api):
    # The list endpoint SELECTs every column too, so it must survive the CLR ones.
    resp = api.post("/api/dbo/Spatial/query", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] >= 1


def test_create_round_trips_editable_json_and_hierarchyid(api):
    # Geo/Shape/Variant are EXCLUDED (read-only); Node (hierarchyid) and Doc
    # (json) are editable and must survive create + the post-insert re-fetch,
    # which itself SELECTs the CLR columns on the freshly inserted row.
    resp = api.post("/api/dbo/Spatial", json={"Name": "n2", "Node": "/3/", "Doc": '{"a": 2}'})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["Node"] == "/3/"
    assert json.loads(body["Doc"]) == {"a": 2}
    # Geo/Shape/Variant were not supplied and stay null — but still read back cleanly.
    assert body["Geo"] is None and body["Shape"] is None and body["Variant"] is None
