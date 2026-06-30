"""
Optimistic concurrency end-to-end against real SQL Server, where ROWVERSION
auto-increments on every UPDATE. This proves what only a real database can: the
token a read returns genuinely moves on after each write, so a stale token is
rejected with 409 CONFLICT — the protection the api-tier tests simulate with a
fixed token here happens for real.

Uses dbo.Concurrent (identity PK + a ROWVERSION column). Docker-gated (see
integration/conftest.py).
"""

import pytest

pytestmark = pytest.mark.integration


def _create(api, name: str) -> dict:
    resp = api.post("/api/dbo/Concurrent", json={"Name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_describe_exposes_rowversion_token(api):
    body = api.get("/meta/dbo/Concurrent").json()
    assert body["concurrency_token"] == "RowVersion"


def test_read_returns_hex_token_that_changes_after_update(api):
    row = _create(api, "v1")
    pk = row["ConcurrentID"]
    t1 = row["RowVersion"]
    assert isinstance(t1, str) and t1                     # hex-encoded, present
    bytes.fromhex(t1)                                     # valid hex (round-trippable)

    updated = api.patch(
        f"/api/dbo/Concurrent/{pk}", json={"Name": "v2"}, headers={"If-Match": t1}
    )
    assert updated.status_code == 200, updated.text
    t2 = updated.json()["RowVersion"]
    assert t2 != t1                                       # rowversion moved on


def test_stale_update_is_conflict(api):
    row = _create(api, "a")
    pk = row["ConcurrentID"]
    t1 = row["RowVersion"]

    # First update with the current token succeeds and bumps the version.
    ok = api.patch(f"/api/dbo/Concurrent/{pk}", json={"Name": "b"}, headers={"If-Match": t1})
    assert ok.status_code == 200, ok.text

    # Reusing the now-stale token must be refused — and change nothing.
    stale = api.patch(f"/api/dbo/Concurrent/{pk}", json={"Name": "c"}, headers={"If-Match": t1})
    assert stale.status_code == 409
    assert stale.json()["code"] == "CONFLICT"
    assert api.get(f"/api/dbo/Concurrent/{pk}").json()["Name"] == "b"


def test_update_without_if_match_still_writes(api):
    # A rowversion table still accepts a write that sends no If-Match (opt-in).
    row = _create(api, "x")
    pk = row["ConcurrentID"]
    resp = api.patch(f"/api/dbo/Concurrent/{pk}", json={"Name": "y"})
    assert resp.status_code == 200, resp.text
    assert api.get(f"/api/dbo/Concurrent/{pk}").json()["Name"] == "y"


def test_stale_delete_conflicts_then_fresh_delete_succeeds(api):
    row = _create(api, "d")
    pk = row["ConcurrentID"]
    t1 = row["RowVersion"]

    # Bump the version so t1 is stale, capturing the fresh token.
    bumped = api.patch(f"/api/dbo/Concurrent/{pk}", json={"Name": "d2"}, headers={"If-Match": t1})
    t2 = bumped.json()["RowVersion"]

    # Stale delete is refused; the row survives.
    assert api.delete(f"/api/dbo/Concurrent/{pk}", headers={"If-Match": t1}).status_code == 409
    assert api.get(f"/api/dbo/Concurrent/{pk}").status_code == 200

    # Current token deletes it.
    assert api.delete(f"/api/dbo/Concurrent/{pk}", headers={"If-Match": t2}).status_code == 200
    assert api.get(f"/api/dbo/Concurrent/{pk}").status_code == 404
