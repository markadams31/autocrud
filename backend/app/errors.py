"""
errors.py — A single, consistent error contract for the whole API.

Every error this API returns has the same JSON shape:

    {
      "code":    "CONSTRAINT_VIOLATION",   # stable, machine-readable
      "message": "...",                    # shown to the user
      "fields":  {"Email": "..."}          # optional, per-field detail
    }

`code` is what the frontend branches on — the session-refresh flow on
UNAUTHENTICATED, the no-access state on PERMISSION_DENIED, the reload-and-
retry flow on CONFLICT, per-field form highlighting on VALIDATION_ERROR — so
the code values and their HTTP statuses are API contract. Route and
connection code raises ApiError; a single exception handler in main.py turns
it into this shape. No code anywhere builds an error response by hand.

Database errors pass their message through raw
----------------------------------------------
This is an internal tool and the schema is already fully visible to every
signed-in user through /meta, so map_database_exception deliberately does NOT
sanitise or prettify: the `message` is the database's own error text,
verbatim. SQL Server's messages name the constraint, table, and column
involved — exactly the feedback an internal user needs — and passing them
through means no parsing layer that can lag behind driver or server message
formats. Only the CODE is derived, and from two robust signals:

    DB-API exception class     IntegrityError/DataError → CONSTRAINT_VIOLATION
                               (409), OperationalError → DATABASE_UNAVAILABLE
                               (503)
    two text patterns          a denied permission → PERMISSION_DENIED (403);
                               a value/operator the server can't process
                               (conversion failures, operators against
                               xml/binary) → BAD_REQUEST (400). Both wear
                               ProgrammingError — the same class as a
                               genuinely malformed query — so the text is
                               the only signal that separates them. The
                               driver (mssql-python) exposes no native SQL
                               error number anywhere, so numbers are not an
                               option.

Anything unrecognised is INTERNAL_ERROR (500): an unattributed
ProgrammingError means our generated SQL is malformed — a bug, not a client
or permission problem.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    """
    Stable, machine-readable error identifiers. The frontend switches on
    these, so the string values are part of the API contract — rename with
    care. Inherits from str so it serializes directly to its value in JSON.
    """
    # Client-correctable (4xx)
    NOT_FOUND = "NOT_FOUND"                        # No row/table/column at this address
    VALIDATION_ERROR = "VALIDATION_ERROR"          # Payload failed Pydantic validation
    CONSTRAINT_VIOLATION = "CONSTRAINT_VIOLATION"  # A database data rule rejected the write
    CONFLICT = "CONFLICT"                          # Optimistic-concurrency: row changed since it was read
    BAD_REQUEST = "BAD_REQUEST"                    # Malformed request (e.g. bad PK format)

    # Access (4xx)
    UNAUTHENTICATED = "UNAUTHENTICATED"            # No signed-in user (missing auth token)
    PERMISSION_DENIED = "PERMISSION_DENIED"        # SQL Server denied this user the operation
    RATE_LIMITED = "RATE_LIMITED"                  # Too many requests to a throttled endpoint

    # Server / infrastructure (5xx)
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"  # Connection/timeout/login, transient
    INTERNAL_ERROR = "INTERNAL_ERROR"              # Anything we didn't anticipate


# Default message per code, used when no more specific text is available.
_DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.NOT_FOUND: "The requested item could not be found.",
    ErrorCode.VALIDATION_ERROR: "Some of the values provided are not valid.",
    ErrorCode.CONSTRAINT_VIOLATION: "This change is not allowed by a data rule.",
    ErrorCode.CONFLICT: (
        "This record was changed by someone else since you loaded it. "
        "Reload to get the latest version, then reapply your change."
    ),
    ErrorCode.BAD_REQUEST: "The request was malformed.",
    ErrorCode.UNAUTHENTICATED: "You are not signed in.",
    ErrorCode.PERMISSION_DENIED: "You do not have permission to perform this action.",
    ErrorCode.RATE_LIMITED: "This action was performed too recently. Please try again shortly.",
    ErrorCode.DATABASE_UNAVAILABLE: "The database is temporarily unavailable. Please try again.",
    ErrorCode.INTERNAL_ERROR: "Something went wrong. Please try again.",
}


# HTTP status per code. Kept here so the exception handler in main.py stays a
# thin translation layer with no status logic of its own.
_STATUS_CODES: dict[ErrorCode, int] = {
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.CONSTRAINT_VIOLATION: 409,
    ErrorCode.CONFLICT: 409,
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.DATABASE_UNAVAILABLE: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}


class ApiError(Exception):
    """
    The one exception type route and connection code raises deliberately.

    Carries a machine-readable code, an optional message override (defaults
    to the generic message for the code), and optional per-field detail for
    form highlighting. The exception handler in main.py turns this into the
    standard JSON response with the right HTTP status.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: Optional[str] = None,
        fields: Optional[dict[str, str]] = None,
        row: Optional[int] = None,
        rows: Optional[dict[str, dict[str, str]]] = None,
    ):
        self.code = code
        self.message = message or _DEFAULT_MESSAGES[code]
        self.fields = fields
        # Bulk-operation attribution (e.g. CSV import): `row` points at the
        # single 0-based line a database constraint rejected; `rows` carries
        # per-line field errors ({"<index>": {"<col>": "<msg>"}}) from up-front
        # validation, so the client can highlight every bad cell at once.
        self.row = row
        self.rows = rows
        super().__init__(self.message)

    @property
    def status_code(self) -> int:
        return _STATUS_CODES[self.code]

    def to_dict(self) -> dict:
        """The JSON body sent to the client. Optional keys are omitted when unset."""
        body: dict = {"code": self.code.value, "message": self.message}
        if self.fields:
            body["fields"] = self.fields
        if self.row is not None:
            body["row"] = self.row
        if self.rows:
            body["rows"] = self.rows
        return body


# ---------------------------------------------------------------------------
# Database exception mapping
# ---------------------------------------------------------------------------

# A denied grant under the signed-in user's identity. Wears ProgrammingError —
# the same class as a malformed query — so the message text is the signal.
_PERMISSION_RE = re.compile(
    r"permission (?:was )?denied"
    r"|denied on (?:the )?(?:object|column|database|schema)"
    r"|do(?:es)? not have permission"
    r"|not able to access the database"
    r"|cannot open database",
    re.IGNORECASE,
)

# A value or operator the server can't process — a client mistake, not a bug:
# conversion failures (a bad number/date in a filter or key), type clashes,
# an operator against xml/binary. One alternative per SQL Server message.
_BAD_QUERY_RE = re.compile(
    r"operand type clash"                       # 206
    r"|are incompatible in the .* operator"     # 402
    r"|conversion failed when converting"       # 241 / 245
    r"|error converting data type"              # 8114
    r"|data type .* is invalid for",            # 8116
    re.IGNORECASE,
)


def map_database_exception(exc: Exception) -> ApiError:
    """
    Translate a SQLAlchemy/DBAPI exception into an ApiError: a machine code
    for the frontend, the raw database message for the user (see the module
    docstring for why it passes through unsanitised).

    Import of SQLAlchemy exception types is local so importing `errors` has
    no hard dependency on SQLAlchemy (keeps the module cheap to import and
    easy to test in isolation).
    """
    from sqlalchemy.exc import DataError, IntegrityError, OperationalError

    orig = getattr(exc, "orig", None)
    text = str(orig) if orig is not None else str(exc)

    # A genuine authorization failure is a 403 whatever class it wears. The
    # default message is kept here: "permission denied on <object>" is already
    # the whole story, and the frontend shows its no-access guidance for it.
    if _PERMISSION_RE.search(text):
        return ApiError(ErrorCode.PERMISSION_DENIED)

    if _BAD_QUERY_RE.search(text):
        return ApiError(ErrorCode.BAD_REQUEST, text)

    if isinstance(exc, (IntegrityError, DataError)):
        # A data rule rejected the write: duplicate key, FK, NOT NULL, CHECK
        # (IntegrityError); or a value the column can't hold — truncation,
        # numeric overflow (DataError). SQL Server's own message names the
        # constraint/table/column, so it goes to the user as-is.
        return ApiError(ErrorCode.CONSTRAINT_VIOLATION, text)

    if isinstance(exc, OperationalError):
        # Connection dropped, timeout, login failure — transient/infra. The
        # generic message is friendlier than a driver connect trace and the
        # full text is logged by the caller.
        return ApiError(ErrorCode.DATABASE_UNAVAILABLE)

    # An unattributed ProgrammingError means our generated SQL is malformed —
    # a bug, not a client or permission problem — so it surfaces as internal.
    return ApiError(ErrorCode.INTERNAL_ERROR)
