"""
Query- and PK-input safety for the CRUD routes:
  - operator-vs-column-type guard on POST /query (value filters on varbinary/xml
    are rejected with a clean 400, not a swallowed driver error);
  - LIKE-wildcard escaping — a user's [ / % / _ match literally (QA-2: an
    unescaped [ opened a T-SQL character class → over-match);
  - primary-key parsing — a single-column natural key is never split on comma
    (QA-3), while composite keys still split with an arity check;
  - the `in` filter value cap that keeps a huge list under SQL Server's ~2100
    parameter limit instead of a 500 (QA-4).
(The page_size cap lives with the pagination tests in test_crud_routes.py.)
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Integer, LargeBinary, MetaData, String, Table

from app.errors import ApiError, ErrorCode
from app.routes.crud import (
    _MAX_IN_VALUES,
    _escape_like,
    _filter_clause,
    _is_likeable_column,
    _pk_filter,
)


def _col(type_):
    return Column("C", type_)


# ── Operator vs column type ──────────────────────────────────────────────────

def test_like_on_binary_column_is_rejected():
    with pytest.raises(ApiError) as ei:
        _filter_clause(_col(LargeBinary()), {"op": "contains", "value": "ff"})
    assert ei.value.code == ErrorCode.BAD_REQUEST


def test_equality_shorthand_on_binary_column_is_rejected():
    with pytest.raises(ApiError) as ei:
        _filter_clause(_col(LargeBinary()), "deadbeef")
    assert ei.value.code == ErrorCode.BAD_REQUEST


def test_comparison_on_binary_column_is_rejected():
    with pytest.raises(ApiError) as ei:
        _filter_clause(_col(LargeBinary()), {"op": "gt", "value": "x"})
    assert ei.value.code == ErrorCode.BAD_REQUEST


def test_null_checks_on_binary_column_are_allowed():
    # A null check compares no value, so it's fine even on a blob column.
    assert _filter_clause(_col(LargeBinary()), {"op": "isnull"}) is not None
    assert _filter_clause(_col(LargeBinary()), {"op": "notnull"}) is not None


@pytest.mark.parametrize("raw", [
    {"op": "contains", "value": "abc"},
    {"op": "eq", "value": "abc"},
    "abc",
    ["a", "b"],
])
def test_text_column_still_filters(raw):
    assert _filter_clause(_col(String(50)), raw) is not None


def test_numeric_column_still_filters():
    assert _filter_clause(_col(Integer()), {"op": "gt", "value": 5}) is not None
    assert _filter_clause(_col(Integer()), 5) is not None


# ── Free-text search: only genuine string columns are LIKE-able ──────────────

def test_is_likeable_only_true_for_string_columns():
    # Real string columns are searchable; binary/rowversion (bytes) and numbers
    # are not — searching them would throw at the driver (see _search_clause).
    assert _is_likeable_column(_col(String(50))) is True
    assert _is_likeable_column(_col(LargeBinary())) is False
    assert _is_likeable_column(_col(Integer())) is False


# ── LIKE-wildcard escaping (QA-2) ────────────────────────────────────────────

def test_escape_like_escapes_bracket_and_wildcards():
    # An unescaped [ opens a T-SQL character class — the QA-2 over-match. A lone
    # ] is literal, so only [ (plus % and _) need escaping.
    assert _escape_like("[a]") == r"\[a]"
    assert _escape_like("100%_x") == r"100\%\_x"
    assert _escape_like("a\\b") == r"a\\b"   # the escape char itself is doubled


# ── Primary-key parsing: single-column keys are never split on comma (QA-3) ───

def _table(*cols):
    return SimpleNamespace(sa_table=Table("T", MetaData(), *cols), schema="dbo", name="T")


def _sql(clause) -> str:
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


def test_single_column_pk_with_comma_is_one_value():
    # A natural key "A,B" must address as a single value, not be split into two.
    clause = _pk_filter(_table(Column("Code", String(3), primary_key=True)), "A,B")
    assert "A,B" in _sql(clause)


def test_composite_pk_still_splits_on_comma():
    t = _table(
        Column("A", Integer, primary_key=True),
        Column("B", Integer, primary_key=True),
    )
    sql = _sql(_pk_filter(t, "1,2"))
    assert "1" in sql and "2" in sql


def test_composite_pk_arity_mismatch_is_400():
    t = _table(
        Column("A", Integer, primary_key=True),
        Column("B", Integer, primary_key=True),
    )
    with pytest.raises(ApiError) as ei:
        _pk_filter(t, "1")           # one value for a two-column key
    assert ei.value.code == ErrorCode.BAD_REQUEST


# ── `in` filter capped under SQL Server's parameter limit (QA-4) ──────────────

def test_in_filter_over_cap_is_rejected():
    with pytest.raises(ApiError) as ei:
        _filter_clause(_col(Integer()), {"op": "in", "value": list(range(_MAX_IN_VALUES + 1))})
    assert ei.value.code == ErrorCode.BAD_REQUEST


def test_in_filter_at_cap_is_allowed():
    clause = _filter_clause(_col(Integer()), {"op": "in", "value": list(range(_MAX_IN_VALUES))})
    assert clause is not None


def test_in_shorthand_list_over_cap_is_rejected():
    # The bare-list shorthand also builds an IN, so it's capped too.
    with pytest.raises(ApiError) as ei:
        _filter_clause(_col(Integer()), list(range(_MAX_IN_VALUES + 1)))
    assert ei.value.code == ErrorCode.BAD_REQUEST
