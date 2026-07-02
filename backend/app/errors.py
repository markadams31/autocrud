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
The SQLAlchemy exception class is a coarse first cut — it conflates a denied
grant with a text operator against an xml column (both ProgrammingError), and a
bad filter value with a real overflow (both DataError). So the CODE is chosen
from the SQL Server error number the ODBC driver reports, falling back to class:

  permission denied (229/230/…)       -> PERMISSION_DENIED   a denied grant (403)
  bad value/operator (8114/8116/245…) -> BAD_REQUEST         a client mistake: a
                                                             bad number/date in a
                                                             filter, LIKE on a
                                                             binary/xml column,
                                                             an uncoercible key
  IntegrityError / other DataError    -> CONSTRAINT_VIOLATION duplicate, FK, NOT
                                                             NULL, CHECK, overflow,
                                                             truncation (409)
  OperationalError                    -> DATABASE_UNAVAILABLE dropped connection,
                                                             timeout, login (503)
  anything else                       -> INTERNAL_ERROR

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
        table_columns = {col.name for col in getattr(sa_table, "columns", ())}
        for constraint in getattr(sa_table, "constraints", ()) or ():
            name = getattr(constraint, "name", None)
            if not name:
                continue
            out[str(name)] = [col.name for col in constraint.columns]
        for index in getattr(sa_table, "indexes", ()) or ():
            if getattr(index, "unique", False) and getattr(index, "name", None):
                out.setdefault(str(index.name), [col.name for col in index.columns])
        # CHECK constraints aren't reflected onto the Table (mssql dialect limitation),
        # so their columns come from the catalog data reflection parked on table.info:
        # the parent column for a column-level check, else the bracketed identifiers in
        # the definition text (e.g. "[EndDate]>=[StartDate]" → EndDate, StartDate).
        for name, (col, definition) in _reflected_checks(sa_table).items():
            if col:
                out.setdefault(str(name), [col])
            else:
                bracketed = set(re.findall(r"\[([^\]]+)\]", str(definition)))
                out.setdefault(str(name), [c for c in table_columns if c in bracketed])
    except Exception:
        pass
    return out


def _parens_balanced(s: str) -> bool:
    """True if every '(' in `s` is matched by a later ')'. Used to only unwrap an
    outer paren pair that actually wraps the whole expression."""
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _humanize_check_expression(sqltext: str) -> str:
    """
    Turn SQL Server's stored CHECK text into something an end user can read: strip
    the [bracket] quoting around identifiers, unwrap the parentheses SQL Server puts
    around literals and the whole expression, and normalise operator spacing.

        "([PercentComplete]>=(0) AND [PercentComplete]<=(100))"
            -> "PercentComplete >= 0 AND PercentComplete <= 100"

    Purely cosmetic and best-effort — an expression it can't simplify is still
    returned, just less tidy. Never raises.
    """
    expr = (sqltext or "").strip()
    # Unwrap redundant outer paren pairs that wrap the entire expression.
    while expr.startswith("(") and expr.endswith(")") and _parens_balanced(expr[1:-1]):
        expr = expr[1:-1].strip()
    # Drop the [] quoting SQL Server puts around identifiers.
    expr = re.sub(r"\[([^\]]+)\]", r"\1", expr)
    # Unwrap parenthesised literals: (0) -> 0, (-1) -> -1, (1.5) -> 1.5, ('x') -> 'x'.
    expr = re.sub(r"\((-?\d+(?:\.\d+)?|N?'[^']*')\)", r"\1", expr)
    # Normalise spacing around the common comparison operators (multi-char first so
    # ">=" isn't split into ">" "=").
    expr = re.sub(r"\s*(<=|>=|<>|!=|=|<|>)\s*", r" \1 ", expr)
    # Collapse any whitespace the steps above introduced.
    return re.sub(r"\s+", " ", expr).strip()


def _reflected_checks(sa_table: object) -> dict:
    """
    The CHECK constraints reflection stashed on the Table's `info` dict —
    { constraint_name: (column|None, definition) }. Empty for a table not built
    by reflection (or before it ran). The mssql dialect doesn't reflect CHECK
    constraints, so reflection reads them from sys.check_constraints and parks
    them here — see reflection._check_constraints / _build_table_info.
    """
    try:
        return getattr(sa_table, "info", {}).get("check_constraints", {}) or {}
    except Exception:
        return {}


def _check_constraint_expressions(sa_table: object) -> dict[str, str]:
    """
    Map each named CHECK constraint on a reflected table to a human-readable form
    of its rule, so a violation can tell the user *what* rule failed, not just its
    name. Sourced from the catalog-reflected definitions on the Table's `info`
    (NOT from table.constraints — the mssql dialect never reflects CHECK
    constraints there; see _reflected_checks).

    Best-effort and never raises. Where the definition wasn't readable at reflection
    (VIEW DEFINITION not held), the constraint isn't present, so the caller degrades
    to naming the rule rather than quoting it.
    """
    out: dict[str, str] = {}
    try:
        for name, (_col, definition) in _reflected_checks(sa_table).items():
            human = _humanize_check_expression(str(definition))
            if human:
                out[str(name)] = human
    except Exception:
        pass
    return out


def friendly_constraint_violation(
    text: str,
    columns_by_constraint: Optional[dict[str, list[str]]] = None,
    checks_by_constraint: Optional[dict[str, str]] = None,
) -> Optional[tuple[str, Optional[dict[str, str]]]]:
    """
    Read a precise (message, fields) pair out of a SQL Server constraint-violation
    message. `columns_by_constraint` (from _constraint_columns) lets a unique/FK
    violation name the exact form field, since the raw message only names the
    referenced side. `checks_by_constraint` (from _check_constraint_expressions)
    lets a CHECK violation quote the actual rule instead of only its name. Returns
    None when nothing recognisable matches, so the caller falls back to the generic
    message.
    """
    def cols_of(name):
        return (columns_by_constraint or {}).get(name) or []

    def rule_of(name):
        return (checks_by_constraint or {}).get(name)

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
        rule = rule_of(constraint)
        # When we have the reflected expression, quote the rule itself — far more
        # actionable than a constraint name the user has never seen. Otherwise name
        # the rule (the pre-existing behaviour) so it still degrades cleanly.
        if rule:
            if len(cols) == 1:
                return (f"'{cols[0]}' must satisfy: {rule}.",
                        {cols[0]: f"Must satisfy: {rule}."})
            if len(cols) > 1:
                return (f"These values must satisfy the rule ({', '.join(cols)}): {rule}.",
                        {c: f"Must satisfy: {rule}." for c in cols})
            return (f"A value must satisfy the rule: {rule}.", None)
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
# The SQLAlchemy exception CLASS is a coarse first cut, but it conflates cases
# that need different HTTP codes: a denied grant and a text operator against an
# xml column both arrive as ProgrammingError; a bad filter value and a real
# overflow both arrive as DataError. So we read the SQL Server *error number*
# (which the ODBC driver appends to the message) to separate:
#
#   - a genuine authorization failure        -> PERMISSION_DENIED (403)
#   - a value/operator the DB can't run (a bad number/date in a filter, LIKE on
#     a binary/xml column, an uncoercible key) -> BAD_REQUEST (400): a *client*
#     mistake, not a permission failure or a data-rule conflict
#   - a real data-rule rejection (FK/unique/NULL/CHECK/overflow/truncation)
#     -> CONSTRAINT_VIOLATION (409), enriched with a precise message
#   - a dropped connection/timeout           -> DATABASE_UNAVAILABLE (503)
#   - anything else                          -> INTERNAL_ERROR (500)
# ---------------------------------------------------------------------------

# The ODBC driver appends the SQL Server native error number just before the
# function name, e.g. "...of like function. (8116) (SQLExecDirectW)".
_SQL_ERRNO_RE = re.compile(r"\((\d{2,6})\)\s*\(SQL\w+\)")

# Authorization failures (a denied grant under the signed-in user's identity).
# They surface as ProgrammingError/42000 — the same class as a malformed query —
# so detect them by number, with the message text as a backstop.
_PERMISSION_ERRORS = frozenset({229, 230, 262, 297, 300, 916, 6004, 10330})
_PERMISSION_RE = re.compile(
    r"permission (?:was )?denied"
    r"|denied on (?:the )?(?:object|column|database|schema)"
    r"|do(?:es)? not have permission"
    r"|not able to access the database"
    r"|cannot open database",
    re.IGNORECASE,
)

# A value or operator the database can't process: conversion failures, type
# clashes, a text operator on a non-text column. Client mistakes -> 400.
#   206  operand type clash            402  types incompatible in operator
#   241  date/time conversion failed   245  varchar→int/other conversion failed
#   8114 error converting data type    8116 arg data type invalid for function
_BAD_QUERY_ERRORS = frozenset({206, 241, 245, 402, 8114, 8116})


def _sql_error_number(text: str) -> Optional[int]:
    """The SQL Server native error number from an ODBC driver message, or None."""
    m = _SQL_ERRNO_RE.search(text)
    return int(m.group(1)) if m else None


def map_database_exception(exc: Exception, sa_table: object = None) -> ApiError:
    """
    Translate a SQLAlchemy/DBAPI exception into a clean ApiError. `sa_table` (the
    statement's table, when available) lets a unique/FK violation name the exact
    form field.

    The code is chosen from the SQL Server error number where the exception class
    is ambiguous (see the section comment above), falling back to the class.

    Import of SQLAlchemy exception types is local to this function so that
    importing `errors` has no hard dependency on SQLAlchemy being present
    (keeps the module cheap to import and easy to test in isolation).
    """
    from sqlalchemy.exc import (
        DataError,
        IntegrityError,
        OperationalError,
    )

    orig = getattr(exc, "orig", None)
    text = str(orig) if orig is not None else str(exc)
    number = _sql_error_number(text)

    # A genuine authorization failure is a 403 whatever class it wears.
    if number in _PERMISSION_ERRORS or _PERMISSION_RE.search(text):
        return ApiError(ErrorCode.PERMISSION_DENIED)

    # A value/operator the database can't run is a client mistake — a 400, not a
    # permission failure (403) or a data-rule conflict (409).
    if number in _BAD_QUERY_ERRORS:
        return ApiError(
            ErrorCode.BAD_REQUEST,
            "The request contains a value or operator that can't be processed.",
        )

    if isinstance(exc, (IntegrityError, DataError)):
        # The database's data rules rejected the write — duplicate key, FK, NOT
        # NULL, CHECK (IntegrityError); or a value the column can't hold, e.g.
        # string truncation / numeric overflow that Pydantic doesn't bound
        # (DataError). A clean 409. Read a precise reason where we can.
        try:
            detail = friendly_constraint_violation(
                text,
                _constraint_columns(sa_table),
                _check_constraint_expressions(sa_table),
            )
        except Exception:
            detail = None
        if detail is not None:
            message, fields = detail
            return ApiError(ErrorCode.CONSTRAINT_VIOLATION, message, fields=fields)
        return ApiError(ErrorCode.CONSTRAINT_VIOLATION)

    if isinstance(exc, OperationalError):
        # Connection dropped, timeout, login failure — transient/infra.
        return ApiError(ErrorCode.DATABASE_UNAVAILABLE)

    # An unattributed ProgrammingError means our generated SQL is malformed — a
    # bug, not a client or permission problem — so it surfaces as internal.
    return ApiError(ErrorCode.INTERNAL_ERROR)
