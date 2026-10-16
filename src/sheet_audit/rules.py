"""Deterministic evaluation of contract rules against a sheet."""

import re
from dataclasses import dataclass, field

import pandas as pd

from sheet_audit import expr
from sheet_audit.schemas import (
    ArithmeticRule,
    InSetRule,
    NotNullRule,
    PatternRule,
    RangeRule,
    Rule,
    RuleSource,
    ScoredRule,
    UniqueRule,
)
from sheet_audit.values import EMAIL_REGEX, canonical, to_numeric

MAX_PATTERN_INPUT = 500  # bound regex work per cell


@dataclass
class RuleOutcome:
    applicable: int = 0
    failing: list = field(default_factory=list)  # DataFrame index labels
    expected: dict = field(default_factory=dict)  # arithmetic only: index -> expected value
    error: str | None = None


def rule_columns(rule: Rule) -> list[str]:
    if isinstance(rule, UniqueRule):
        return list(rule.columns)
    if isinstance(rule, ArithmeticRule):
        return [rule.target, *sorted(expr.columns(expr.parse(rule.expression)))]
    return [rule.column]


def describe_rule(rule: Rule) -> str:
    if isinstance(rule, UniqueRule):
        return f"{', '.join(rule.columns)} is unique" if len(rule.columns) == 1 else f"({', '.join(rule.columns)}) is unique"
    if isinstance(rule, NotNullRule):
        return f"{rule.column} is never empty"
    if isinstance(rule, InSetRule):
        shown = ", ".join(rule.values[:8]) + (", …" if len(rule.values) > 8 else "")
        return f"{rule.column} is one of: {shown}"
    if isinstance(rule, RangeRule):
        if rule.min is not None and rule.max is not None:
            return f"{rule.min:g} ≤ {rule.column} ≤ {rule.max:g}"
        return f"{rule.column} ≥ {rule.min:g}" if rule.min is not None else f"{rule.column} ≤ {rule.max:g}"
    if isinstance(rule, PatternRule):
        if rule.regex == EMAIL_REGEX:
            return f"{rule.column} is a valid email address"
        return f"{rule.column} matches /{rule.regex}/"
    return f"{rule.target} = {rule.expression} (±{rule.tolerance:g})"


def evaluate_rule(rule: Rule, df: pd.DataFrame) -> RuleOutcome:
    missing = [c for c in rule_columns(rule) if c not in df.columns]
    if missing:
        return RuleOutcome(error=f"unknown column(s): {', '.join(missing)}")

    if isinstance(rule, UniqueRule):
        sub = df[rule.columns].dropna()
        dup = sub.duplicated(keep=False)
        return RuleOutcome(applicable=len(sub), failing=list(sub.index[dup]))

    if isinstance(rule, NotNullRule):
        return RuleOutcome(applicable=len(df), failing=list(df.index[df[rule.column].isna()]))

    if isinstance(rule, InSetRule):
        s = df[rule.column].dropna()
        allowed = {canonical(v) for v in rule.values}
        bad = ~s.map(canonical).isin(allowed)
        return RuleOutcome(applicable=len(s), failing=list(s.index[bad]))

    if isinstance(rule, RangeRule):
        n = to_numeric(df[rule.column]).dropna()
        bad = pd.Series(False, index=n.index)
        if rule.min is not None:
            bad |= n < rule.min
        if rule.max is not None:
            bad |= n > rule.max
        return RuleOutcome(applicable=len(n), failing=list(n.index[bad]))

    if isinstance(rule, PatternRule):
        rx = re.compile(rule.regex)
        s = df[rule.column].dropna().map(lambda v: v.strip()[:MAX_PATTERN_INPUT])
        bad = ~s.map(lambda v: rx.fullmatch(v) is not None)
        return RuleOutcome(applicable=len(s), failing=list(s.index[bad]))

    # ArithmeticRule: compare only rows where every referenced value is numeric.
    tree = expr.parse(rule.expression)
    cols = rule_columns(rule)
    nums = {c: to_numeric(df[c]) for c in cols}
    mask = pd.concat(nums.values(), axis=1).notna().all(axis=1)
    rhs = expr.evaluate(tree, {c: v[mask] for c, v in nums.items()})
    lhs = nums[rule.target][mask]
    ok_rows = rhs.notna()
    diff = (lhs[ok_rows] - rhs[ok_rows]).abs()
    bad = diff > rule.tolerance + 1e-9 * rhs[ok_rows].abs()
    failing = list(diff.index[bad])
    return RuleOutcome(
        applicable=int(ok_rows.sum()),
        failing=failing,
        expected={i: float(rhs[i]) for i in failing},
    )


def score_rule(rule: Rule, df: pd.DataFrame, source: RuleSource) -> ScoredRule:
    outcome = evaluate_rule(rule, df)
    return ScoredRule(
        rule=rule,
        source=source,
        applicable=outcome.applicable,
        passed=outcome.applicable - len(outcome.failing),
        error=outcome.error,
    )
