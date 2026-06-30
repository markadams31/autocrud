"""
Metadata serialisation (the JSON shape the frontend consumes) and the
config module's parsing / fail-fast behaviour.
"""

import pytest
from sqlalchemy import Column, Identity, MetaData, Table, text
from sqlalchemy.dialects.mssql import DATETIME2, DECIMAL, INTEGER, NVARCHAR

from app import config
from app.config import _build_settings
from app.reflection import _build_column_info
from app.routes.meta import _column_response, _field_type


# ── Metadata serialisation ───────────────────────────────────────────────────

def _project_columns():
    md = MetaData()
    t = Table(
        "Project", md,
        Column("ProjectID", INTEGER(), Identity(), primary_key=True),
        Column("ProjectName", NVARCHAR(200), nullable=False),
        Column("Budget", DECIMAL(18, 2), nullable=False),
        Column("ManagerID", INTEGER(), nullable=False),
        Column("CreatedDate", DATETIME2(), nullable=True),
        schema="ppm",
    )
    fk_map = {("ppm", "Project", "ManagerID"): ("dbo", "Employee", "EmployeeID")}
    return {c.name: _build_column_info(c, set(), fk_map) for c in t.columns}


def test_field_type_strings():
    cols = _project_columns()
    assert _field_type(cols["ProjectName"]) == "text"
    assert _field_type(cols["Budget"]) == "decimal"
    assert _field_type(cols["ManagerID"]) == "integer"
    assert _field_type(cols["CreatedDate"]) == "datetime"


def test_column_response_identity_pk():
    body = _column_response(_project_columns()["ProjectID"])
    assert body["is_primary_key"] is True
    assert body["editable"] is False          # identity → DB-owned
    assert body["field_type"] == "integer"


def test_column_response_decimal_precision_scale():
    body = _column_response(_project_columns()["Budget"])
    assert body["field_type"] == "decimal"
    assert body["precision"] == 18
    assert body["scale"] == 2
    assert body["required"] is True           # NOT NULL, no default, editable


def test_column_response_foreign_key_shape():
    body = _column_response(_project_columns()["ManagerID"])
    assert body["foreign_key"] == {"schema": "dbo", "table": "Employee", "column": "EmployeeID"}


def test_column_response_audit_column():
    body = _column_response(_project_columns()["CreatedDate"])
    assert body["is_audit"] is True
    assert body["editable"] is False


def test_column_response_max_length():
    assert _column_response(_project_columns()["ProjectName"])["max_length"] == 200


# ── Config parsing / fail-fast ───────────────────────────────────────────────

def test_db_schemas_parsed_to_list():
    assert config.DB_SCHEMAS == ["dbo"]       # set by conftest


def test_audit_columns_lowercased_set():
    assert config.DB_AUDIT_COLUMNS == {"createdby", "createddate", "modifiedby", "modifieddate"}


def test_missing_required_vars_reported_together(monkeypatch):
    monkeypatch.delenv("DB_SERVER", raising=False)
    monkeypatch.delenv("DB_DATABASE", raising=False)
    with pytest.raises(RuntimeError) as ei:
        _build_settings()
    msg = str(ei.value)
    assert "DB_SERVER" in msg and "DB_DATABASE" in msg   # reports all at once


def test_empty_required_var_is_rejected(monkeypatch):
    monkeypatch.setenv("DB_SERVER", "")
    with pytest.raises(RuntimeError) as ei:
        _build_settings()
    assert "DB_SERVER" in str(ei.value)


def test_empty_schemas_list_is_rejected(monkeypatch):
    # Present but parses to no schemas (stray commas / whitespace).
    monkeypatch.setenv("DB_SCHEMAS", " , ,")
    with pytest.raises(RuntimeError) as ei:
        _build_settings()
    assert "DB_SCHEMAS" in str(ei.value)
