"""Describe a sheet's columns and infer candidate contract rules from the data alone.

Candidate rules are hypotheses ("total = subtotal + tax"). They are scored against every
row before anyone relies on them, and the user confirms them in interactive mode.
"""

import itertools
import re

import pandas as pd

from sheet_audit.config import Settings
from sheet_audit.gate import SheetTable
from sheet_audit.rules import evaluate_rule
from sheet_audit.sanitize import clean
from sheet_audit.schemas import (
    ArithmeticRule,
    ColumnProfile,
    InSetRule,
    NotNullRule,
    PatternRule,
    RangeRule,
    Rule,
    SheetProfile,
    UniqueRule,
    ValueCount,
)
from sheet_audit.values import EMAIL_REGEX, canonical, infer_type, to_numeric

_ID_HINT = re.compile(r"(^|_|\b)(id|code|key|number|no|ref)($|_|\b)", re.IGNORECASE)
_NUMERIC_TYPES = {"integer", "number"}

MAX_ARITHMETIC_COLUMNS = 12
ARITHMETIC_SAMPLE_ROWS = 2000
MAX_ARITHMETIC_CANDIDATES = 5
MAX_IN_SET_VALUES = 12


def profile_column(name: str, series: pd.Series, settings: Settings) -> ColumnProfile:
    non_null = series.dropna()
    kind, conforming = infer_type(series)
    n = len(series)
    profile = ColumnProfile(
        name=name,
        inferred_type=kind,
        non_null=len(non_null),
        null_rate=round(1 - len(non_null) / n, 4) if n else 1.0,
        distinct=non_null.map(canonical).nunique(),
        conforming_ratio=round(float(conforming.mean()), 4) if len(conforming) else 1.0,
        nonconforming=int((~conforming).sum()) if len(conforming) else 0,
    )
    if kind in _NUMERIC_TYPES:
        num = to_numeric(non_null).dropna()
        profile.min, profile.median, profile.max = float(num.min()), float(num.median()), float(num.max())
    if settings.share_sample_values and len(non_null):
        if profile.distinct <= 20:
            counts = non_null.map(lambda v: v.strip()).value_counts().head(5)
            profile.top_values = [ValueCount(value=clean(v, settings.max_value_chars), count=int(c)) for v, c in counts.items()]
        else:
            profile.examples = [clean(v, settings.max_value_chars) for v in non_null.drop_duplicates().head(3)]
    return profile


def _ref(column: str) -> str:
    return column if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column) else f"[{column}]"


def _arithmetic_candidates(df: pd.DataFrame, numeric_cols: list[str]) -> list[Rule]:
    cols = numeric_cols[:MAX_ARITHMETIC_COLUMNS]
    sample = df.head(ARITHMETIC_SAMPLE_ROWS)
    found: list[tuple[float, Rule]] = []
    seen: set[tuple[frozenset, str]] = set()
    # Additive and multiplicative forms first, so "total = subtotal + tax" wins over "tax = total - subtotal".
    for op, family in (("+", "add"), ("*", "mul"), ("-", "add"), ("/", "mul")):
        for target in cols:
            others = [c for c in cols if c != target]
            pairs = itertools.combinations(others, 2) if op in "+*" else itertools.permutations(others, 2)
            for a, b in pairs:
                key = (frozenset((target, a, b)), family)
                if key in seen:
                    continue
                rule = ArithmeticRule(target=target, expression=f"{_ref(a)} {op} {_ref(b)}")
                outcome = evaluate_rule(rule, sample)
                if outcome.applicable < 5:
                    continue
                support = 1 - len(outcome.failing) / outcome.applicable
                if support >= 0.8:
                    seen.add(key)
                    found.append((support, rule))
    found.sort(key=lambda t: -t[0])
    return [r for _, r in found[:MAX_ARITHMETIC_CANDIDATES]]


def candidate_rules(df: pd.DataFrame, columns: list[ColumnProfile]) -> list[Rule]:
    rules: list[Rule] = []
    n = len(df)
    for c in columns:
        if c.inferred_type == "empty":
            continue
        unique_ratio = c.distinct / c.non_null if c.non_null else 0
        if (
            c.inferred_type in {"integer", "text"}
            and c.non_null >= 0.95 * n
            and unique_ratio >= 0.95
            and (unique_ratio == 1 or _ID_HINT.search(c.name))
        ):
            rules.append(UniqueRule(columns=[c.name], rationale="nearly every value is distinct and the column looks like a key"))
        if c.null_rate <= 0.05:
            rules.append(NotNullRule(column=c.name, rationale="the column is almost always filled"))
        if c.inferred_type in _NUMERIC_TYPES:
            num = to_numeric(df[c.name]).dropna()
            if len(num) and (num >= 0).mean() >= 0.95:
                rules.append(RangeRule(column=c.name, min=0, rationale="values are almost always non-negative"))
        if c.inferred_type == "text":
            s = df[c.name].dropna()
            counts = s.map(canonical).value_counts()
            frequent = counts[counts >= 2]
            if len(s) >= 10 and len(counts) <= MAX_IN_SET_VALUES and frequent.sum() / len(s) >= 0.9:
                # Use the most common spelling of each allowed value.
                spelled = s.map(lambda v: v.strip()).groupby(s.map(canonical)).agg(lambda g: g.value_counts().index[0])
                rules.append(InSetRule(column=c.name, values=[spelled[k] for k in frequent.index], rationale="a small set of repeated categories"))
            if s.map(lambda v: re.fullmatch(EMAIL_REGEX, v.strip()) is not None).mean() >= 0.8:
                rules.append(PatternRule(column=c.name, regex=EMAIL_REGEX, rationale="values look like email addresses"))
    numeric_cols = [c.name for c in columns if c.inferred_type in _NUMERIC_TYPES and not set("[]") & set(c.name)]
    rules.extend(_arithmetic_candidates(df, numeric_cols))
    return rules


def profile_sheet(table: SheetTable, settings: Settings) -> SheetProfile:
    columns = [profile_column(name, table.df[name], settings) for name in table.df.columns]
    return SheetProfile(
        sheet=table.name,
        n_rows=len(table.df),
        columns=columns,
        candidate_rules=candidate_rules(table.df, columns),
        warnings=table.gate.warnings,
    )
