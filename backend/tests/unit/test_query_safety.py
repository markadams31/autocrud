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
    _pk_filter,
    _reject_supplementary,
)


def _col(type_):
    return Column("C", type_)


# Filterability/searchability are decided at reflection time (ColumnInfo.filterable
# /.searchable — see test_reflection_types_and_models for the per-type policy);
# _filter_clause only enforces those flags (plus `name`, for per-field error
# detail), so a stand-in carrying them is all it reads. FILTERABLE stands in for
# a text column (searchable); NUMERIC for a filterable-but-not-text column (LIKE
# operators aren't valid on it); OPAQUE for a non-comparable type (blob/xml).
FILTERABLE = SimpleNamespace(filterable=True, searchable=True, name="C")
NUMERIC = SimpleNamespace(filterable=True, searchable=False, name="C")
OPAQUE = SimpleNamespace(filterable=False, searchable=False, name="C")


# ── Operator vs column filterability ─────────────────────────────────────────

def test_like_on_opaque_column_is_rejected():
    with pytest.raises(ApiError) as ei:
        _filter_clause(OPAQUE, _col(LargeBinary()), {"op": "contains", "value": "ff"})
    assert ei.value.code == ErrorCode.BAD_REQUEST


def test_equality_shorthand_on_opaque_column_is_rejected():
    with pytest.raises(ApiError) as ei:
        _filter_clause(OPAQUE, _col(LargeBinary()), "deadbeef")
    assert ei.value.code == ErrorCode.BAD_REQUEST


def test_comparison_on_opaque_column_is_rejected():
    with pytest.raises(ApiError) as ei:
        _filter_clause(OPAQUE, _col(LargeBinary()), {"op": "gt", "value": "x"})
    assert ei.value.code == ErrorCode.BAD_REQUEST


def test_null_checks_on_opaque_column_are_allowed():
    # A null check compares no value, so it's fine even on a blob column.
    assert _filter_clause(OPAQUE, _col(LargeBinary()), {"op": "isnull"}) is not None
    assert _filter_clause(OPAQUE, _col(LargeBinary()), {"op": "notnull"}) is not None


@pytest.mark.parametrize("raw", [
    {"op": "contains", "value": "abc"},
    {"op": "eq", "value": "abc"},
    "abc",
    ["a", "b"],
])
def test_text_column_still_filters(raw):
    assert _filter_clause(FILTERABLE, _col(String(50)), raw) is not None


def test_numeric_column_still_filters():
    assert _filter_clause(FILTERABLE, _col(Integer()), {"op": "gt", "value": 5}) is not None
    assert _filter_clause(FILTERABLE, _col(Integer()), 5) is not None


# ── Unknown operator is rejected, never dropped (BUG-3) ──────────────────────

@pytest.mark.parametrize("op", ["starts_with", "INVALID_OP_XYZ", "like", "="])
def test_unknown_operator_is_rejected(op):
    # A typo'd operator must fail loudly. Dropping it removes the constraint,
    # which — with an "all matching" bulk op — becomes a full-table write.
    with pytest.raises(ApiError) as ei:
        _filter_clause(FILTERABLE, _col(String(50)), {"op": op, "value": "x"})
    assert ei.value.code == ErrorCode.VALIDATION_ERROR
    assert ei.value.fields == {"C": f"Unknown filter operator '{op}'."}


@pytest.mark.parametrize("op", [
    "eq", "ne", "gt", "gte", "lt", "lte",
    "contains", "startswith", "endswith", "in", "between", "isnull", "notnull",
])
def test_known_operators_are_accepted(op):
    # Every documented operator survives validation (value shaped to suit each).
    value: object = ["a", "b"] if op in ("in", "between") else "a"
    # Should not raise the unknown-operator error; may return a clause or None.
    _filter_clause(FILTERABLE, _col(String(50)), {"op": op, "value": value})


# ── between with malformed arity is rejected, incomplete range still drops (BUG-A) ──

@pytest.mark.parametrize("value", [[1], [1, 2, 3], 5, "1,2", []])
def test_between_wrong_arity_is_rejected(value):
    # A between value that isn't a [low, high] pair is malformed — rejecting it
    # avoids silently matching every row (a full-table write under all_matching).
    with pytest.raises(ApiError) as ei:
        _filter_clause(FILTERABLE, _col(Integer()), {"op": "between", "value": value})
    assert ei.value.code == ErrorCode.VALIDATION_ERROR


@pytest.mark.parametrize("value", [[None, 5], [1, None], ["", 5], [1, ""]])
def test_between_incomplete_bound_still_drops(value):
    # One blank bound is a half-filled range chip, not a malformed request — it
    # drops harmlessly, exactly like gt/lt with an empty value.
    assert _filter_clause(FILTERABLE, _col(Integer()), {"op": "between", "value": value}) is None


# ── LIKE operators are rejected on non-text columns (BUG-E) ──────────────────

@pytest.mark.parametrize("op", ["contains", "startswith", "endswith"])
def test_like_on_numeric_column_is_rejected(op):
    with pytest.raises(ApiError) as ei:
        _filter_clause(NUMERIC, _col(Integer()), {"op": op, "value": "1"})
    assert ei.value.code == ErrorCode.VALIDATION_ERROR


def test_comparison_on_numeric_column_still_works():
    # Only LIKE is gated by searchable; comparisons on a numeric column are fine.
    assert _filter_clause(NUMERIC, _col(Integer()), {"op": "gt", "value": 5}) is not None


# ── Supplementary-plane (emoji) text is rejected in LIKE patterns (BUG-1) ─────

@pytest.mark.parametrize("term", ["📊", "🔥", "a🙂b", "\U0001F4CA"])
def test_supplementary_characters_rejected(term):
    with pytest.raises(ApiError) as ei:
        _reject_supplementary(term, "search")
    assert ei.value.code == ErrorCode.VALIDATION_ERROR
    assert "search" in (ei.value.fields or {})


@pytest.mark.parametrize("term", ["hello", "café", "日本語", "العربية", "[a]%_"])
def test_bmp_text_is_allowed(term):
    # Everything in the Basic Multilingual Plane matches correctly — no rejection.
    assert _reject_supplementary(term, "search") is None


def test_emoji_like_filter_is_rejected():
    with pytest.raises(ApiError) as ei:
        _filter_clause(FILTERABLE, _col(String(50)), {"op": "contains", "value": "📊"})
    assert ei.value.code == ErrorCode.VALIDATION_ERROR


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
        _filter_clause(FILTERABLE, _col(Integer()), {"op": "in", "value": list(range(_MAX_IN_VALUES + 1))})
    assert ei.value.code == ErrorCode.BAD_REQUEST


def test_in_filter_at_cap_is_allowed():
    clause = _filter_clause(FILTERABLE, _col(Integer()), {"op": "in", "value": list(range(_MAX_IN_VALUES))})
    assert clause is not None


def test_in_shorthand_list_over_cap_is_rejected():
    # The bare-list shorthand also builds an IN, so it's capped too.
    with pytest.raises(ApiError) as ei:
        _filter_clause(FILTERABLE, _col(Integer()), list(range(_MAX_IN_VALUES + 1)))
    assert ei.value.code == ErrorCode.BAD_REQUEST
