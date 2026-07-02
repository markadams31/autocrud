"""
The error contract: code→status mapping, the JSON body shape, the class-based
database-exception code mapping, and the precise constraint-violation messages
read out of the SQL Server driver text.
"""

import pytest
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, ProgrammingError

from app.errors import (
    ApiError,
    ErrorCode,
    _DEFAULT_MESSAGES,
    _STATUS_CODES,
    _check_constraint_expressions,
    _humanize_check_expression,
    bad_request,
    friendly_constraint_violation,
    map_database_exception,
    not_found,
    permission_denied,
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


# Realistic ODBC driver messages — the driver appends "(<errno>) (SQLxxx)", which
# the mapper reads to pick the code. Fragments observed in production telemetry.
_DRV = "[Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
_PERM = f"[42000] {_DRV}The SELECT permission was denied on the object 'Employee'. (229) (SQLExecDirectW)"
_XML_LIKE = f"[42000] {_DRV}Argument data type xml is invalid for argument 1 of like function. (8116) (SQLExecDirectW)"
_CONVERT = f"[42000] {_DRV}Error converting data type varchar to real. (8114) (SQLExecDirectW)"
_PK_CONVERT = f"[22018] {_DRV}Conversion failed when converting the varchar value 'export' to data type int. (245) (SQLExecDirectW)"

# The same failures as mssql-python surfaces them (verified against SQL Server
# 2025): a "Driver Error: <odbc class>; DDBC Error: [Microsoft][SQL Server]<text>"
# message with NO native error number anywhere — so the mapper's text patterns,
# not the number, carry the signal for this driver.
_MP = "Driver Error: Syntax error or access violation; DDBC Error: [Microsoft][SQL Server]"
_MP_PERM = f"{_MP}The SELECT permission was denied on the object 'Category', database 'db', schema 'app'."
_MP_XML_LIKE = f"{_MP}Argument data type xml is invalid for argument 1 of like function."
_MP_CONVERT = ("Driver Error: Invalid character value for cast specification; "
               "DDBC Error: [Microsoft][SQL Server]Conversion failed when converting "
               "the varchar value 'not-a-number' to data type int.")


@pytest.mark.parametrize(
    "exc,expected",
    [
        (IntegrityError("stmt", {}, Exception("dup")), ErrorCode.CONSTRAINT_VIOLATION),
        # Out-of-range / overflow / truncation — a value the column can't hold, so
        # a clean 409.
        (DataError("stmt", {}, Exception("out of range")), ErrorCode.CONSTRAINT_VIOLATION),
        # A denied grant is a 403 whatever SQLAlchemy class it surfaces as.
        (ProgrammingError("stmt", {}, Exception(_PERM)), ErrorCode.PERMISSION_DENIED),
        # A text operator on an xml column, or an uncoercible filter/key value, is
        # a client mistake (400) — not a permission failure (was wrongly 403) nor
        # a data-rule conflict (was wrongly 409).
        (ProgrammingError("stmt", {}, Exception(_XML_LIKE)), ErrorCode.BAD_REQUEST),
        (ProgrammingError("stmt", {}, Exception(_CONVERT)), ErrorCode.BAD_REQUEST),
        (DataError("stmt", {}, Exception(_PK_CONVERT)), ErrorCode.BAD_REQUEST),
        # mssql-python shapes of the same failures — no native error number, so
        # the text patterns must reach the same codes.
        (ProgrammingError("stmt", {}, Exception(_MP_PERM)), ErrorCode.PERMISSION_DENIED),
        (ProgrammingError("stmt", {}, Exception(_MP_XML_LIKE)), ErrorCode.BAD_REQUEST),
        (DataError("stmt", {}, Exception(_MP_CONVERT)), ErrorCode.BAD_REQUEST),
        (OperationalError("stmt", {}, Exception("timeout")), ErrorCode.DATABASE_UNAVAILABLE),
        # An unattributed ProgrammingError means our generated SQL is malformed — a
        # bug, so a 500 rather than a misleading 403.
        (ProgrammingError("stmt", {}, Exception("syntax error near X")), ErrorCode.INTERNAL_ERROR),
        (ValueError("anything else"), ErrorCode.INTERNAL_ERROR),
    ],
)
def test_map_database_exception(exc, expected):
    assert map_database_exception(exc).code == expected


# ── Precise constraint-violation messages ────────────────────────────────────
# Internal-only deployment + schema is public via /meta, so naming the column /
# constraint / submitted value is actionable feedback, not a leak. Realistic
# SQL Server / pyodbc message fragments for each constraint kind:
_UNIQUE = ("Violation of UNIQUE KEY constraint 'UQ_Employee_Email'. Cannot insert duplicate "
           "key in object 'dbo.Employee'. The duplicate key value is (a@b.com). (2627)")
# Real SQL Server CHECK messages name the constraint but NOT the column.
_CHECK = ('The UPDATE statement conflicted with the CHECK constraint "CK_Project_Percent". '
          'The conflict occurred in database "db", table "ppm.Project". (547)')
_FK = ('The INSERT statement conflicted with the FOREIGN KEY constraint "FK_Project_Manager". '
       'The conflict occurred in database "db", table "dbo.Employee", column \'EmployeeID\'. (547)')
_REFERENCE = ('The DELETE statement conflicted with the REFERENCE constraint "FK_Task_Assignee". '
              'The conflict occurred in database "db", table "ppm.Task", column \'AssigneeID\'. (547)')
_NOTNULL = ("Cannot insert the value NULL into column 'JobTitle', table 'db.dbo.Employee'; "
            "column does not allow nulls. INSERT fails. (515)")
_TRUNC = ("String or binary data would be truncated in table 'db.dbo.Employee', "
          "column 'EmployeeNumber'. Truncated value: 'TOOLONG'. (2628)")


def test_unique_violation_names_column_and_value_when_table_known():
    msg, fields = friendly_constraint_violation(_UNIQUE, {"UQ_Employee_Email": ["Email"]})
    assert msg == "'Email' must be unique, but 'a@b.com' is already used."
    assert fields == {"Email": "'a@b.com' already exists."}


def test_unique_violation_without_table_keeps_constraint_and_value():
    msg, fields = friendly_constraint_violation(_UNIQUE)
    assert "must be unique" in msg and "a@b.com" in msg and "UQ_Employee_Email" in msg
    assert fields is None


def test_check_violation_recovers_column_from_constraint_expression():
    # The message omits the column, so it's supplied via columns_by_constraint
    # (which _constraint_columns builds from the catalog-reflected check data).
    msg, fields = friendly_constraint_violation(_CHECK, {"CK_Project_Percent": ["PercentComplete"]})
    assert msg == "'PercentComplete' is not allowed by validation rule CK_Project_Percent."
    assert fields == {"PercentComplete": "Not allowed by CK_Project_Percent."}


def test_check_violation_without_any_column_still_names_the_rule():
    msg, fields = friendly_constraint_violation(_CHECK)
    assert msg == "A value is not allowed by validation rule CK_Project_Percent."
    assert fields is None


def test_check_violation_multi_column_lists_them():
    text = 'conflicted with the CHECK constraint "CK_Project_Dates". The conflict occurred in table "ppm.Project". (547)'
    msg, fields = friendly_constraint_violation(text, {"CK_Project_Dates": ["StartDate", "EndDate"]})
    assert "StartDate, EndDate" in msg and "CK_Project_Dates" in msg
    assert fields == {"StartDate": "Not allowed by CK_Project_Dates.", "EndDate": "Not allowed by CK_Project_Dates."}


# ── CHECK violations quote the actual rule when the expression is known ───────
# When the reflected constraint expression is available (checks_by_constraint,
# from _check_constraint_expressions), the message quotes the rule itself rather
# than a constraint name the user has never seen.

_CHECKS = {"CK_Project_Percent": "PercentComplete >= 0 AND PercentComplete <= 100"}


def test_check_violation_quotes_the_rule_with_column():
    msg, fields = friendly_constraint_violation(
        _CHECK, {"CK_Project_Percent": ["PercentComplete"]}, _CHECKS
    )
    assert msg == "'PercentComplete' must satisfy: PercentComplete >= 0 AND PercentComplete <= 100."
    assert fields == {"PercentComplete": "Must satisfy: PercentComplete >= 0 AND PercentComplete <= 100."}


def test_check_violation_quotes_the_rule_without_a_column():
    msg, fields = friendly_constraint_violation(_CHECK, None, _CHECKS)
    assert msg == "A value must satisfy the rule: PercentComplete >= 0 AND PercentComplete <= 100."
    assert fields is None


def test_check_violation_falls_back_to_name_when_expression_unknown():
    # No entry in checks_by_constraint (e.g. VIEW DEFINITION not held at reflection)
    # → the pre-existing name-based message, unchanged.
    msg, fields = friendly_constraint_violation(_CHECK, {"CK_Project_Percent": ["PercentComplete"]}, {})
    assert msg == "'PercentComplete' is not allowed by validation rule CK_Project_Percent."
    assert fields == {"PercentComplete": "Not allowed by CK_Project_Percent."}


@pytest.mark.parametrize(
    "stored,expected",
    [
        ("([PercentComplete]>=(0) AND [PercentComplete]<=(100))",
         "PercentComplete >= 0 AND PercentComplete <= 100"),
        ("([Price]>(0))", "Price > 0"),
        ("([EndDate]>=[StartDate])", "EndDate >= StartDate"),
        ("([Status]='active')", "Status = 'active'"),
        ("([Discount]<>(1.5))", "Discount <> 1.5"),
        ("([Balance]>=(-1))", "Balance >= -1"),
    ],
)
def test_humanize_check_expression(stored, expected):
    assert _humanize_check_expression(stored) == expected


def test_check_expressions_reflected_from_table_info():
    # The mssql dialect doesn't reflect CHECK constraints onto table.constraints, so
    # reflection reads them from sys.check_constraints and parks them on
    # Table.info["check_constraints"] as {name: (column|None, definition)} — see
    # reflection._check_constraints / _build_table_info. This mirrors that shape.
    from sqlalchemy import Column, MetaData, Table
    from sqlalchemy.dialects.mssql import INTEGER

    md = MetaData()
    t = Table("Project", md, Column("PercentComplete", INTEGER()), schema="ppm")
    t.info["check_constraints"] = {
        "CK_Project_Percent": ("PercentComplete", "([PercentComplete]>=(0) AND [PercentComplete]<=(100))"),
    }
    assert _check_constraint_expressions(t) == {
        "CK_Project_Percent": "PercentComplete >= 0 AND PercentComplete <= 100"
    }
    # And the whole path end-to-end: a driver CHECK message + that table produces a
    # user-facing message that quotes the rule and highlights the column.
    mapped = map_database_exception(
        IntegrityError("UPDATE ppm.Project ...", {}, Exception(_CHECK)), t
    )
    assert mapped.code == ErrorCode.CONSTRAINT_VIOLATION
    assert mapped.message == "'PercentComplete' must satisfy: PercentComplete >= 0 AND PercentComplete <= 100."
    assert "UPDATE ppm.Project" not in mapped.message  # never leak the raw statement


def test_check_columns_recovered_from_table_level_definition():
    # A table-level check has no parent column (col is None), so _constraint_columns
    # recovers the columns from the bracketed identifiers in the definition text.
    from app.errors import _constraint_columns
    from sqlalchemy import Column, MetaData, Table
    from sqlalchemy.dialects.mssql import DATE

    md = MetaData()
    t = Table("Project", md, Column("StartDate", DATE()), Column("EndDate", DATE()), schema="ppm")
    t.info["check_constraints"] = {"CK_Dates": (None, "([EndDate]>=[StartDate])")}
    assert set(_constraint_columns(t)["CK_Dates"]) == {"StartDate", "EndDate"}


def test_fk_violation_resolves_to_the_local_column_the_user_edited():
    # The raw message names the referenced side (dbo.Employee.EmployeeID); with the
    # table's constraints we resolve it to the field the user actually edited.
    msg, fields = friendly_constraint_violation(_FK, {"FK_Project_Manager": ["ManagerID"]})
    assert msg.startswith("'ManagerID' must reference an existing record")
    assert fields == {"ManagerID": "Selected record does not exist."}


def test_fk_violation_without_table_reports_constraint_and_target():
    msg, fields = friendly_constraint_violation(_FK)
    assert "does not exist" in msg and "FK_Project_Manager" in msg and "dbo.Employee.EmployeeID" in msg
    assert fields is None


def test_reference_violation_on_delete_names_the_referencing_table():
    msg, fields = friendly_constraint_violation(_REFERENCE)
    assert "cannot be deleted" in msg and "ppm.Task" in msg and "FK_Task_Assignee" in msg
    assert fields is None


def test_not_null_violation_names_required_column():
    assert friendly_constraint_violation(_NOTNULL) == (
        "'JobTitle' is required and cannot be empty.", {"JobTitle": "Required."}
    )


def test_truncation_names_the_overlong_column():
    assert friendly_constraint_violation(_TRUNC) == (
        "The value for 'EmployeeNumber' is too long for this field.", {"EmployeeNumber": "Too long."}
    )


def test_truncation_without_column_is_still_clear():
    assert friendly_constraint_violation("String or binary data would be truncated. (8152)") == (
        "A value is too long for its column.", None
    )


def test_unrecognised_message_returns_none_so_caller_uses_generic():
    # A SQLite message, or anything we don't model — degrade gracefully.
    assert friendly_constraint_violation("UNIQUE constraint failed: Customer.Email") is None
    assert friendly_constraint_violation("some unexpected driver text") is None


def test_map_database_exception_surfaces_the_precise_message():
    mapped = map_database_exception(IntegrityError("INSERT INTO Employee ...", {}, Exception(_UNIQUE)))
    assert mapped.code == ErrorCode.CONSTRAINT_VIOLATION
    assert "must be unique" in mapped.message and "a@b.com" in mapped.message
    # The structured detail is surfaced, but never the raw SQL statement text.
    assert "INSERT INTO Employee" not in mapped.message


def test_map_database_exception_falls_back_to_generic_when_unrecognised():
    mapped = map_database_exception(IntegrityError("INSERT ...", {}, Exception("UNIQUE constraint failed: X.Y")))
    assert mapped.code == ErrorCode.CONSTRAINT_VIOLATION
    assert mapped.message == _DEFAULT_MESSAGES[ErrorCode.CONSTRAINT_VIOLATION]


def test_convenience_constructors():
    assert not_found().code == ErrorCode.NOT_FOUND
    assert bad_request("x").message == "x"
    assert permission_denied().code == ErrorCode.PERMISSION_DENIED
