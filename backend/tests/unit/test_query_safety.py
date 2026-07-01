"""
Operator-vs-column-type guard on POST /query: a value filter (equality,
comparison, or LIKE) on a column the database can't compare — varbinary/xml — is
rejected with a clean 400 instead of a driver error the grid would swallow.
(The page_size cap lives with the pagination tests in test_crud_routes.py.)
"""

import pytest
from sqlalchemy import Column, Integer, LargeBinary, String

from app.errors import ApiError, ErrorCode
from app.routes.crud import _filter_clause, _is_likeable_column


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
