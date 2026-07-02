"""
The error contract: code→status mapping, the JSON body shape, and the
database-exception mapping — a machine code chosen from the DB-API exception
class plus two text patterns, with the database's own message passed through
verbatim (internal tool; see errors.py).
"""

import pytest
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, ProgrammingError

from app.errors import (
    ApiError,
    ErrorCode,
    _DEFAULT_MESSAGES,
    _STATUS_CODES,
    map_database_exception,
)


def test_every_code_has_status_and_message():
    for code in ErrorCode:
        assert code in _STATUS_CODES, f"{code} missing a status"
        assert code in _DEFAULT_MESSAGES, f"{code} missing a default message"


@pytest.mark.parametrize(
    "code,status",
    [
        (ErrorCode.NOT_FOUND, 404),
        (ErrorCode.VALIDATION_ERROR, 422),
        (ErrorCode.CONSTRAINT_VIOLATION, 409),
        (ErrorCode.BAD_REQUEST, 400),
        (ErrorCode.UNAUTHENTICATED, 401),
        (ErrorCode.PERMISSION_DENIED, 403),
        (ErrorCode.DATABASE_UNAVAILABLE, 503),
        (ErrorCode.INTERNAL_ERROR, 500),
    ],
)
def test_status_codes(code, status):
    assert ApiError(code).status_code == status


def test_to_dict_uses_default_message_and_omits_fields():
    body = ApiError(ErrorCode.NOT_FOUND).to_dict()
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == _DEFAULT_MESSAGES[ErrorCode.NOT_FOUND]
    assert "fields" not in body


def test_to_dict_includes_fields_and_override_message():
    body = ApiError(ErrorCode.VALIDATION_ERROR, "Custom", fields={"Email": "bad"}).to_dict()
    assert body == {"code": "VALIDATION_ERROR", "message": "Custom", "fields": {"Email": "bad"}}


def test_code_serialises_to_its_string_value():
    # ErrorCode inherits str, so it is part of the JSON contract verbatim.
    assert ApiError(ErrorCode.PERMISSION_DENIED).to_dict()["code"] == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# Database exception mapping.
#
# Message fragments as mssql-python surfaces them (verified against SQL Server
# 2025): "Driver Error: <odbc class>; DDBC Error: [Microsoft][SQL Server]<text>",
# with NO native error number anywhere — the text patterns carry the signal.
# The older pyodbc shapes (trailing "(<errno>) (SQLxxx)") contain the same SQL
# Server sentences, so they map identically; a few are kept below to prove the
# mapping is driver-format-agnostic.
# ---------------------------------------------------------------------------

_MP = "Driver Error: Syntax error or access violation; DDBC Error: [Microsoft][SQL Server]"
_MP_PERM = f"{_MP}The SELECT permission was denied on the object 'Category', database 'db', schema 'app'."
_MP_XML_LIKE = f"{_MP}Argument data type xml is invalid for argument 1 of like function."
_MP_CONVERT = ("Driver Error: Invalid character value for cast specification; "
               "DDBC Error: [Microsoft][SQL Server]Conversion failed when converting "
               "the varchar value 'not-a-number' to data type int.")
_MP_CHECK = ("Driver Error: Integrity constraint violation; DDBC Error: [Microsoft]"
             "[SQL Server]The INSERT statement conflicted with the CHECK constraint "
             '"CK_Shapes_Range". The conflict occurred in database "db", table '
             "\"app.Shapes\", column 'ColChecked'.")

_PYODBC = "[Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
_PYODBC_PERM = f"[42000] {_PYODBC}The SELECT permission was denied on the object 'Employee'. (229) (SQLExecDirectW)"
_PYODBC_CONVERT = f"[42000] {_PYODBC}Error converting data type varchar to real. (8114) (SQLExecDirectW)"


@pytest.mark.parametrize(
    "exc,expected",
    [
        (IntegrityError("stmt", {}, Exception("dup")), ErrorCode.CONSTRAINT_VIOLATION),
        # Out-of-range / overflow / truncation — a value the column can't hold,
        # so a clean 409.
        (DataError("stmt", {}, Exception("out of range")), ErrorCode.CONSTRAINT_VIOLATION),
        # A denied grant is a 403 whatever SQLAlchemy class it surfaces as.
        (ProgrammingError("stmt", {}, Exception(_MP_PERM)), ErrorCode.PERMISSION_DENIED),
        (ProgrammingError("stmt", {}, Exception(_PYODBC_PERM)), ErrorCode.PERMISSION_DENIED),
        # A value/operator the server can't process is a client mistake (400) —
        # not a permission failure (403) nor a data-rule conflict (409).
        (ProgrammingError("stmt", {}, Exception(_MP_XML_LIKE)), ErrorCode.BAD_REQUEST),
        (DataError("stmt", {}, Exception(_MP_CONVERT)), ErrorCode.BAD_REQUEST),
        (ProgrammingError("stmt", {}, Exception(_PYODBC_CONVERT)), ErrorCode.BAD_REQUEST),
        (OperationalError("stmt", {}, Exception("timeout")), ErrorCode.DATABASE_UNAVAILABLE),
        # An unattributed ProgrammingError means our generated SQL is malformed —
        # a bug, so a 500 rather than a misleading 403 (but see below: the DB
        # text is still surfaced).
        (ProgrammingError("stmt", {}, Exception("syntax error near X")), ErrorCode.INTERNAL_ERROR),
        (ValueError("anything else"), ErrorCode.INTERNAL_ERROR),
    ],
)
def test_map_database_exception_codes(exc, expected):
    assert map_database_exception(exc).code == expected


def test_constraint_violation_passes_the_database_message_through():
    # Internal tool: the user sees exactly what SQL Server said — constraint,
    # table and column names included. No parsing, no paraphrase.
    err = map_database_exception(IntegrityError("stmt", {}, Exception(_MP_CHECK)))
    assert err.code is ErrorCode.CONSTRAINT_VIOLATION
    assert 'CHECK constraint "CK_Shapes_Range"' in err.message
    assert "'ColChecked'" in err.message


def test_bad_request_passes_the_database_message_through():
    err = map_database_exception(DataError("stmt", {}, Exception(_MP_CONVERT)))
    assert err.code is ErrorCode.BAD_REQUEST
    assert "'not-a-number'" in err.message


def test_permission_denied_keeps_the_generic_message():
    # The frontend renders its own no-access guidance for 403; the raw text
    # adds nothing actionable for the user here.
    err = map_database_exception(ProgrammingError("stmt", {}, Exception(_MP_PERM)))
    assert err.message == _DEFAULT_MESSAGES[ErrorCode.PERMISSION_DENIED]


def test_unavailable_keeps_the_generic_message():
    # A driver connect trace is noise to an end user; the caller logs the text.
    err = map_database_exception(OperationalError("stmt", {}, Exception("login timeout: tcp ...")))
    assert err.message == _DEFAULT_MESSAGES[ErrorCode.DATABASE_UNAVAILABLE]


def test_unmapped_database_error_surfaces_the_message_but_stays_500():
    # An unrecognised DB error stays a 500 (it's most likely our bug), but its
    # text is passed through rather than hidden behind the generic message — so
    # an unmapped fault is legible instead of an opaque "Something went wrong".
    unmapped = f"{_MP}An unexpected server condition matching no known pattern occurred."
    err = map_database_exception(ProgrammingError("stmt", {}, Exception(unmapped)))
    assert err.code is ErrorCode.INTERNAL_ERROR
    assert err.status_code == 500
    assert "no known pattern" in err.message
    assert err.message != _DEFAULT_MESSAGES[ErrorCode.INTERNAL_ERROR]


def test_non_database_internal_error_keeps_the_generic_message():
    # The generic Exception handler in main.py raises ApiError(INTERNAL_ERROR)
    # directly (not via the mapper) for a non-DB bug — no DB text to show, so the
    # user still gets the friendly generic message. This guards that distinction.
    assert ApiError(ErrorCode.INTERNAL_ERROR).message == _DEFAULT_MESSAGES[ErrorCode.INTERNAL_ERROR]


def test_message_comes_from_orig_when_present():
    # SQLAlchemy wraps the DBAPI error as .orig; the mapper must prefer it over
    # the (statement-bearing) wrapper string.
    exc = IntegrityError("INSERT INTO t ...", {}, Exception("the real database message"))
    assert map_database_exception(exc).message == "the real database message"
