import pandas as pd
import pytest

from sheet_audit import expr


def test_parses_precedence_and_quoted_names():
    tree = expr.parse("[Unit Price] * qty + 2")
    assert expr.columns(tree) == {"Unit Price", "qty"}
    values = {"Unit Price": pd.Series([2.0, 3.0]), "qty": pd.Series([4.0, 5.0])}
    assert expr.evaluate(tree, values).tolist() == [10.0, 17.0]


def test_parentheses_and_unary_minus():
    tree = expr.parse("-(a - b) / 2")
    out = expr.evaluate(tree, {"a": pd.Series([1.0]), "b": pd.Series([5.0])})
    assert out.tolist() == [2.0]


def test_division_by_zero_is_nan_not_error():
    out = expr.evaluate(expr.parse("a / b"), {"a": pd.Series([1.0]), "b": pd.Series([0.0])})
    assert out.isna().all()


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os').system('echo hi')",
        "a.__class__",
        "a ** 2",
        "open('x')",
        "a; b",
        "a +",
        "(a + b",
        "",
        "a" + " + a" * 30,  # too many nodes
        "x" * 201,  # too long
    ],
)
def test_rejects_anything_outside_the_grammar(text):
    with pytest.raises(ValueError):
        expr.parse(text)
