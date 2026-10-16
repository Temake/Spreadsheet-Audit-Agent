import pytest
from pydantic import TypeAdapter, ValidationError

from sheet_audit.rules import evaluate_rule, score_rule
from sheet_audit.schemas import (
    ArithmeticRule,
    InSetRule,
    NotNullRule,
    PatternRule,
    RangeRule,
    Rule,
    RuleSource,
    UniqueRule,
)

HEADER = ["id", "qty", "price", "total", "status", "email"]
DATA = [
    ["A1", 2, 5, 10, "open", "a@x.io"],
    ["A2", 3, 5, 15, "Open ", "b@x.io"],
    ["A3", 1, 4, 9, "closed", "bad-email"],  # total wrong
    ["A3", 2, 2, 4, "closd", "c@x.io"],  # duplicate id, bad status
    ["A5", -1, 3, -3, "open", None],  # negative qty
    ["A6", "x", 3, 6, "open", "d@x.io"],  # non-numeric qty
]


@pytest.fixture
def df(make_table):
    return make_table(HEADER, DATA).df


def rows(outcome):
    return [i + 1 for i in outcome.failing]


def test_unique(df):
    assert rows(evaluate_rule(UniqueRule(columns=["id"]), df)) == [4, 5]


def test_not_null(df):
    assert rows(evaluate_rule(NotNullRule(column="email"), df)) == [6]


def test_in_set_ignores_case_and_spaces(df):
    out = evaluate_rule(InSetRule(column="status", values=["open", "closed"]), df)
    assert rows(out) == [5]


def test_range_skips_non_numeric(df):
    out = evaluate_rule(RangeRule(column="qty", min=0), df)
    assert out.applicable == 5 and rows(out) == [6]


def test_pattern(df):
    out = evaluate_rule(PatternRule(column="email", regex=r"[^@\s]+@[^@\s]+\.\w+"), df)
    assert out.applicable == 5 and rows(out) == [4]


def test_arithmetic_compares_only_numeric_rows_and_reports_expected(df):
    out = evaluate_rule(ArithmeticRule(target="total", expression="qty * price"), df)
    assert out.applicable == 5
    assert rows(out) == [4]
    assert out.expected[3] == 4.0


def test_unknown_columns_are_an_error_not_a_crash(df):
    scored = score_rule(NotNullRule(column="nope"), df, RuleSource.AGENT)
    assert scored.error and "nope" in scored.error and scored.applicable == 0


def test_support_is_computed(df):
    scored = score_rule(ArithmeticRule(target="total", expression="qty * price"), df, RuleSource.AGENT)
    assert (scored.passed, scored.applicable) == (4, 5)
    assert scored.support == pytest.approx(0.8)


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "python", "code": "import os"},  # not a rule type
        {"kind": "arithmetic", "target": "t", "expression": "__import__('os')"},
        {"kind": "pattern", "column": "c", "regex": "(a+)+$"},  # catastrophic backtracking
        {"kind": "pattern", "column": "c", "regex": r"(a)\1"},  # backreference
        {"kind": "pattern", "column": "c", "regex": "("},  # invalid regex
        {"kind": "range", "column": "c"},  # no bounds
        {"kind": "range", "column": "c", "min": 5, "max": 1},
        {"kind": "not_null", "column": "c", "extra": "field"},  # unknown field
    ],
)
def test_rule_schema_rejects_unsafe_or_invalid_rules(payload):
    with pytest.raises(ValidationError):
        TypeAdapter(Rule).validate_python(payload)
