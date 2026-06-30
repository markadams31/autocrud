"""
Optimistic concurrency (rowversion / If-Match) on update and delete, against the
in-memory sqlite 'dbo.Doc' fixture, which carries a rowversion column.

The fixture seeds one row with a known 8-byte token (sqlite doesn't auto-maintain
a rowversion). Tests read the token back as hex from the API and send it as
If-Match, exercising the exact route logic — the If-Match WHERE clause and the
409-vs-404 distinction. Real auto-incrementing rowversion behaviour (the token
moving on after each write) is covered by the integration tier.
"""


def _get(client, pk):
    return client.get(f"/api/dbo/Doc/{pk}")


# ── Metadata exposure ────────────────────────────────────────────────────────

def test_describe_exposes_concurrency_token(versioned):
    body = versioned.client.get("/meta/dbo/Doc").json()
    assert body["concurrency_token"] == "RowVersion"


def test_describe_has_no_token_when_absent(widget):
    # The Widget fixture has no rowversion column.
    body = widget.client.get("/meta/dbo/Widget").json()
    assert body["concurrency_token"] is None


def test_row_carries_token_as_hex(versioned):
    body = _get(versioned.client, versioned.seeded_pk).json()
    assert body["RowVersion"] == versioned.seeded_token


# ── Update ───────────────────────────────────────────────────────────────────

def test_update_with_matching_token_succeeds(versioned):
    pk = versioned.seeded_pk
    token = _get(versioned.client, pk).json()["RowVersion"]
    resp = versioned.client.patch(
        f"/api/dbo/Doc/{pk}", json={"Title": "Edited"}, headers={"If-Match": token}
    )
    assert resp.status_code == 200
    assert resp.json()["Title"] == "Edited"


def test_update_with_stale_token_conflicts(versioned):
    pk = versioned.seeded_pk
    resp = versioned.client.patch(
        f"/api/dbo/Doc/{pk}",
        json={"Title": "Edited"},
        headers={"If-Match": "00000000000000ff"},  # not the seeded token
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONFLICT"
    # The row is untouched — the write never landed.
    assert _get(versioned.client, pk).json()["Title"] == "Original"


def test_update_missing_row_is_404_even_with_if_match(versioned):
    resp = versioned.client.patch(
        "/api/dbo/Doc/99999",
        json={"Title": "x"},
        headers={"If-Match": "0000000000000001"},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_update_without_if_match_falls_back_to_last_writer_wins(versioned):
    # A table with a rowversion still accepts a write that sends no If-Match —
    # protection is opt-in per request, so older clients keep working.
    pk = versioned.seeded_pk
    resp = versioned.client.patch(f"/api/dbo/Doc/{pk}", json={"Title": "NoToken"})
    assert resp.status_code == 200
    assert resp.json()["Title"] == "NoToken"


def test_malformed_if_match_is_400(versioned):
    pk = versioned.seeded_pk
    resp = versioned.client.patch(
        f"/api/dbo/Doc/{pk}", json={"Title": "x"}, headers={"If-Match": "nothex"}
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "BAD_REQUEST"


def test_update_accepts_0x_prefixed_token(versioned):
    # SSMS-style 0x-prefixed hex is tolerated.
    pk = versioned.seeded_pk
    token = _get(versioned.client, pk).json()["RowVersion"]
    resp = versioned.client.patch(
        f"/api/dbo/Doc/{pk}", json={"Title": "Prefixed"}, headers={"If-Match": "0x" + token}
    )
    assert resp.status_code == 200


# ── Delete ───────────────────────────────────────────────────────────────────

def test_delete_with_matching_token_succeeds(versioned):
    pk = versioned.seeded_pk
    token = _get(versioned.client, pk).json()["RowVersion"]
    resp = versioned.client.delete(f"/api/dbo/Doc/{pk}", headers={"If-Match": token})
    assert resp.status_code == 200
    assert _get(versioned.client, pk).status_code == 404


def test_delete_with_stale_token_conflicts(versioned):
    pk = versioned.seeded_pk
    resp = versioned.client.delete(
        f"/api/dbo/Doc/{pk}", headers={"If-Match": "00000000000000ff"}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONFLICT"
    # Still present — the stale delete was refused.
    assert _get(versioned.client, pk).status_code == 200


def test_delete_without_if_match_falls_back(versioned):
    pk = versioned.seeded_pk
    resp = versioned.client.delete(f"/api/dbo/Doc/{pk}")
    assert resp.status_code == 200
    assert _get(versioned.client, pk).status_code == 404
