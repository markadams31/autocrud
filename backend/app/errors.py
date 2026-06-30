"""
errors.py — A single, consistent error contract for the whole API.

Why this module exists
----------------------
Two problems with raw framework/database errors reaching the frontend:

  1. They leak internals. A bare SQL Server message naming tables, constraints,
     and key values is useful to an attacker and meaningless to an end user.

  2. They're not actionable. The frontend can't switch on an English sentence;
     it needs a stable, machine-readable code to decide how to react.

Every error this API returns therefore has the same JSON shape:

    {
      "code":    "CONSTRAINT_VIOLATION",   # stable, machine-readable
      "message": "This change is not allowed by a data rule.",  # for humans
      "fields":  {"Email": "..."}          # optional, per-field detail
    }

`code` is what the frontend branches on; `message` is a safe, generic
sentence it can show directly; `fields` (when present) lets a form highlight
the specific input that caused the problem.

How it's used
-------------
Route and connection code raises ApiError — or a helper like not_found().
A single exception handler registered in main.py converts ApiError, plus the
database exceptions mapped here, into the JSON shape above. No code anywhere
builds an error response by hand, and no error path returns a different shape.

On database errors
------------------
The error CODE is chosen coarsely and class-based, on SQLAlchemy's exception
types (not by parsing error numbers, which is fragile):

  IntegrityError   -> CONSTRAINT_VIOLATION   duplicate, FK, NOT NULL, CHECK
  DataError        -> CONSTRAINT_VIOLATION   length / numeric / datetime overflow
  ProgrammingError -> PERMISSION_DENIED      under OBO a denied grant is the
                                              realistic cause; queries are
                                              built from reflected metadata and
                                              fully parameterised, so a genuine
                                              malformed-SQL ProgrammingError
                                              indicates a bug and would surface
                                              in testing
  OperationalError -> DATABASE_UNAVAILABLE   dropped connection, timeout, login
                                              failure — transient/infra
  anything else    -> INTERNAL_ERROR

For a CONSTRAINT_VIOLATION we then read a precise, human MESSAGE (and per-field
detail) out of the driver text — see friendly_constraint_violation. This is an
internal-only deployment and the schema is already fully visible to every
signed-in user through /meta, so naming the offending column, constraint, or the
value they just submitted is not a leak — it is exactly the actionable feedback
an internal user needs. Parsing is best-effort: an unrecognised message (driver
variance, a non-SQL-Server backend) falls back to the generic text, so it only
ever adds detail, never an error. Up-front Pydantic validation still catches the
common over-length/invalid-type case before the database is touched (see
VALIDATION_ERROR); this covers what slips past it.
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


# Default human-readable message per code. Deliberately generic — they never
# echo table names, constraint names, or submitted values. A route can override
# the message when it has safe, specific context to add.
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

    Carries a machine-readable code, an optional human message override
    (defaults to the generic message for the code), and optional per-field
    detail for form highlighting. The exception handler in main.py turns this
    into the standard JSON response with the right HTTP status.
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
# Convenience constructors — keep route code terse and consistent.
# ---------------------------------------------------------------------------

def not_found(message: Optional[str] = None) -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, message)


def bad_request(message: Optional[str] = None) -> ApiError:
    return ApiError(ErrorCode.BAD_REQUEST, message)


def permission_denied(message: Optional[str] = None) -> ApiError:
    return ApiError(ErrorCode.PERMISSION_DENIED, message)


# ---------------------------------------------------------------------------
# Reading a precise reason out of a SQL Server constraint-violation message
#
# SQL Server's messages are structured enough to recover the constraint name,
# the offending column, and (for a duplicate) the value the user submitted.
# Internal-only deployment + the schema is public via /meta, so surfacing those
# is precise feedback, not a leak. See the module docstring for the rationale.
# ---------------------------------------------------------------------------

# SQL Server quotes identifiers with either ' or " across these messages.
_ID = r"['\"]([^'\"]+)['\"]"

_DUP_KEY_RE = re.compile(  # 1=constraint, 2=duplicate value
    r"Violation of (?:UNIQUE|PRIMARY) KEY constraint " + _ID
    + r".*?duplicate key value is \((.*?)\)",
    re.IGNORECASE | re.DOTALL,
)
_CHECK_RE = re.compile(  # 1=constraint, 2=column (SQL Server usually omits the column here)
    r"conflicted with the CHECK constraint " + _ID + r"(?:.*?column " + _ID + r")?",
    re.IGNORECASE | re.DOTALL,
)
_FK_RE = re.compile(  # 1=constraint, 2=referenced table, 3=referenced column
    r"conflicted with the FOREIGN KEY constraint " + _ID + r".*?table " + _ID + r", column " + _ID,
    re.IGNORECASE | re.DOTALL,
)
_REFERENCE_RE = re.compile(  # 1=constraint, 2=referencing table
    r"conflicted with the REFERENCE constraint " + _ID + r".*?table " + _ID,
    re.IGNORECASE | re.DOTALL,
)
_NULL_RE = re.compile(r"Cannot insert the value NULL into column " + _ID, re.IGNORECASE)  # 1=column
_TRUNCATE_RE = re.compile(  # 1=column (optional; older SQL Server omits it)
    r"String or binary data would be truncated(?:.*?column " + _ID + r")?",
    re.IGNORECASE | re.DOTALL,
)


def _constraint_columns(sa_table: object) -> dict[str, list[str]]:
    """
    Map each named constraint/unique-index on a reflected table to its local
    column names. Used to turn a constraint name from an error message into the
    actual form field(s) — SQL Server's unique/FK messages name the *referenced*
    side, not the column the user edited. Best-effort and never raises.
    """
    out: dict[str, list[str]] = {}
    if sa_table is None:
        return out
    try:
        table_columns = {col.name for col in sa_table.columns}
        for constraint in getattr(sa_table, "constraints", ()) or ():
            name = getattr(constraint, "name", None)
            if not name:
                continue
            cols = [col.name for col in constraint.columns]
            # CheckConstraint reflects its expression text but not its columns,
            # so recover them from the bracketed identifiers in the expression
            # (e.g. "[PercentComplete]>=(0)" → PercentComplete).
            if not cols and getattr(constraint, "sqltext", None) is not None:
                bracketed = set(re.findall(r"\[([^\]]+)\]", str(constraint.sqltext)))
                cols = [c for c in table_columns if c in bracketed]
            out[str(name)] = cols
        for index in getattr(sa_table, "indexes", ()) or ():
            if getattr(index, "unique", False) and getattr(index, "name", None):
                out.setdefault(str(index.name), [col.name for col in index.columns])
    except Exception:
        pass
    return out


def friendly_constraint_violation(
    text: str,
    columns_by_constraint: Optional[dict[str, list[str]]] = None,
) -> Optional[tuple[str, Optional[dict[str, str]]]]:
    """
    Read a precise (message, fields) pair out of a SQL Server constraint-violation
    message. `columns_by_constraint` (from _constraint_columns) lets a unique/FK
    violation name the exact form field, since the raw message only names the
    referenced side. Returns None when nothing recognisable matches, so the caller
    falls back to the generic message.
    """
    cols_of = lambda name: (columns_by_constraint or {}).get(name) or []

    m = _DUP_KEY_RE.search(text)
    if m:
        constraint, value = m.group(1), m.group(2).strip()
        cols = cols_of(constraint)
        if len(cols) == 1:
            return (f"'{cols[0]}' must be unique, but '{value}' is already used.",
                    {cols[0]: f"'{value}' already exists."})
        if len(cols) > 1:
            return (f"This combination of {', '.join(cols)} already exists — it must be unique.", None)
        return (f"That value must be unique, but '{value}' already exists (constraint {constraint}).", None)

    m = _CHECK_RE.search(text)
    if m:
        constraint = m.group(1)
        # SQL Server's CHECK message rarely names the column, so fall back to the
        # column(s) recovered from the constraint's expression (see _constraint_columns).
        cols = [m.group(2)] if m.group(2) else cols_of(constraint)
        if len(cols) == 1:
            return (f"'{cols[0]}' is not allowed by validation rule {constraint}.",
                    {cols[0]: f"Not allowed by {constraint}."})
        if len(cols) > 1:
            return (f"These values are not allowed by validation rule {constraint} ({', '.join(cols)}).",
                    {c: f"Not allowed by {constraint}." for c in cols})
        return (f"A value is not allowed by validation rule {constraint}.", None)

    m = _FK_RE.search(text)
    if m:
        constraint, ref_table, ref_column = m.group(1), m.group(2), m.group(3)
        cols = cols_of(constraint)
        if len(cols) == 1:
            return (f"'{cols[0]}' must reference an existing record, but the selected value does not exist.",
                    {cols[0]: "Selected record does not exist."})
        return (f"A selected value references a record that does not exist "
                f"(constraint {constraint}, expects {ref_table}.{ref_column}).", None)

    m = _REFERENCE_RE.search(text)
    if m:
        constraint, ref_table = m.group(1), m.group(2)
        return (f"This record cannot be deleted because it is still referenced by "
                f"{ref_table} (constraint {constraint}).", None)

    m = _NULL_RE.search(text)
    if m:
        column = m.group(1)
        return (f"'{column}' is required and cannot be empty.", {column: "Required."})

    m = _TRUNCATE_RE.search(text)
    if m:
        column = m.group(1)
        if column:
            return (f"The value for '{column}' is too long for this field.", {column: "Too long."})
        return ("A value is too long for its column.", None)

    return None


# ---------------------------------------------------------------------------
# Database exception mapping
#
# The class of the exception decides the CODE; for CONSTRAINT_VIOLATION we then
# enrich the MESSAGE from the driver text (above). The one distinction worth
# preserving is PERMISSION_DENIED, because database-enforced authorization is
# the spine of the design and the frontend reacts differently to "you can't" vs
# "it broke".
# ---------------------------------------------------------------------------

def map_database_exception(exc: Exception, sa_table: object = None) -> ApiError:
    """
    Translate a SQLAlchemy/DBAPI exception into a clean ApiError. `sa_table` (the
    statement's table, when available) lets a unique/FK violation name the exact
    form field.

    Import of SQLAlchemy exception types is local to this function so that
    importing `errors` has no hard dependency on SQLAlchemy being present
    (keeps the module cheap to import and easy to test in isolation).
    """
    from sqlalchemy.exc import (
        DataError,
        IntegrityError,
        OperationalError,
        ProgrammingError,
    )

    if isinstance(exc, (IntegrityError, DataError)):
        # The database's data rules rejected the write — duplicate key, FK, NOT
        # NULL, CHECK (IntegrityError); or a value the column can't hold, e.g.
        # string truncation / numeric overflow that Pydantic doesn't bound
        # (DataError). Both are a clean 409. Read a precise reason where we can.
        detail = None
        try:
            orig = getattr(exc, "orig", None)
            text = str(orig) if orig is not None else str(exc)
            detail = friendly_constraint_violation(text, _constraint_columns(sa_table))
        except Exception:
            detail = None
        if detail is not None:
            message, fields = detail
            return ApiError(ErrorCode.CONSTRAINT_VIOLATION, message, fields=fields)
        return ApiError(ErrorCode.CONSTRAINT_VIOLATION)

    if isinstance(exc, ProgrammingError):
        # Under OBO the realistic cause is a denied grant. (Verify against a
        # low-privilege user: if permission errors surface as OperationalError
        # in your stack instead, move this check ahead of that branch.)
        return ApiError(ErrorCode.PERMISSION_DENIED)

    if isinstance(exc, OperationalError):
        # Connection dropped, timeout, login failure — transient/infra.
        return ApiError(ErrorCode.DATABASE_UNAVAILABLE)

    # Not a database exception we model — let it surface as internal.
    return ApiError(ErrorCode.INTERNAL_ERROR)