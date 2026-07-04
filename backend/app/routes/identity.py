"""
routes/identity.py — "Who am I".

Surfaces the EasyAuth-authenticated identity so the frontend can show who is
signed in. It reads only the identity headers EasyAuth injects on every
request — no database access — so it stays cheap and keeps working even when
the database is unreachable.

Authentication itself is enforced on the data routes via the OBO access token
(see connection.py); this endpoint does not gate anything. It simply reports
what EasyAuth has already established about the caller. Locally, the dev auth
proxy injects the same headers, so the signed-in user shows up in development
exactly as it does in production.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.auth_headers import (
    CLIENT_PRINCIPAL_ID,
    CLIENT_PRINCIPAL_NAME,
    USER_ACCESS_TOKEN,
)

router = APIRouter(tags=["identity"])


@router.get("/me")
def whoami(request: Request) -> dict:
    """
    Return the signed-in user's display name and id, plus an `authenticated`
    flag. All fields come straight from EasyAuth headers; `name`/`id` are null
    when no identity is present (e.g. running locally without the auth proxy),
    which the frontend treats as "not signed in" and renders nothing for.
    """
    name = request.headers.get(CLIENT_PRINCIPAL_NAME)
    return {
        "name": name,
        "id": request.headers.get(CLIENT_PRINCIPAL_ID),
        "authenticated": bool(request.headers.get(USER_ACCESS_TOKEN) or name),
    }
