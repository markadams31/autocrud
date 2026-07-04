"""
GET /me — reports the EasyAuth identity from request headers. No database
access, so it works with the standard widget client (which has the app wired
up) and even when no snapshot/connection is involved.
"""


def test_me_anonymous(widget):
    resp = widget.client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] is None
    assert body["id"] is None
    assert body["authenticated"] is False


def test_me_with_easyauth_headers(widget):
    resp = widget.client.get(
        "/me",
        headers={
            "X-MS-CLIENT-PRINCIPAL-NAME": "ada@contoso.com",
            "X-MS-CLIENT-PRINCIPAL-ID": "abc-123",
            "X-MS-TOKEN-AAD-ACCESS-TOKEN": "a.token",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "name": "ada@contoso.com",
        "id": "abc-123",
        "authenticated": True,
    }


def test_me_name_only_is_authenticated(widget):
    # Display name present but no token header still counts as a known identity.
    resp = widget.client.get(
        "/me", headers={"X-MS-CLIENT-PRINCIPAL-NAME": "ada@contoso.com"}
    )
    assert resp.json()["authenticated"] is True
