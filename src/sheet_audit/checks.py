"""Generic checks that make sense on any table, with or without a contract."""

import numpy as np
import pandas as pd

from sheet_audit.config import Settings
from sheet_audit.gate import SheetTable, row_number
from sheet_audit.sanitize import clean
from sheet_audit.schemas import Confidence, Evidence, Finding, Severity
from sheet_audit.values import EXCEL_ERRORS, canonical, infer_type, to_numeric

MAX_EVIDENCE = 5
OUTLIER_Z = 3.5  
MIN_OUTLIER_VALUES = 10
MAX_VARIANT_CATEGORIES = 50


def evidence(df: pd.DataFrame, index: list, columns: list[str], settings: Settings, extra: dict | None = None) -> list[Evidence]:
    out = []
    for i in index[:MAX_EVIDENCE]:
        values = {c: clean(df.at[i, c], settings.max_value_chars) if df.at[i, c] is not None else "(empty)" for c in columns}
        values.update((extra or {}).get(i, {}))
        out.append(Evidence(row=row_number(i), values=values))
    return out


def _finding(table: SheetTable, **kwargs) -> Finding:
    return Finding(sheet=table.name, **kwargs)


def duplicate_rows(table: SheetTable, settings: Settings) -> list[Finding]:
    df = table.df
    dup = df.duplicated(keep=False)
    if not dup.any():
        return []
    index = list(df.index[dup])
    groups = df[dup].apply(tuple, axis=1).nunique()
    return [
        _finding(
            table,
            check="duplicate_rows",
            title="Exact duplicate rows",
            description=f"{len(index)} rows are exact copies of another row ({groups} distinct record(s) repeated).",
            severity=Severity.MEDIUM,
            confidence=Confidence.CONFIRMED,
            columns=list(df.columns),
            rows=[row_number(i) for i in index],
            evidence=evidence(df, index, list(df.columns)[:6], settings),
        )
    ]


def formula_errors(table: SheetTable, settings: Settings) -> list[Finding]:
    findings = []
    for col in table.df.columns:
        s = table.df[col]
        bad = s.isin(EXCEL_ERRORS)
        if bad.any():
            index = list(s.index[bad])
            findings.append(
                _finding(
                    table,
                    check="formula_error",
                    title=f"Spreadsheet errors in {col}",
                    description=f"{len(index)} cell(s) contain a spreadsheet error value such as #DIV/0! or #REF!.",
                    severity=Severity.HIGH,
                    confidence=Confidence.CONFIRMED,
                    columns=[col],
                    rows=[row_number(i) for i in index],
                    evidence=evidence(table.df, index, [col], settings),
                )
            )
    return findings


def invalid_types(table: SheetTable, settings: Settings) -> list[Finding]:
    findings = []
    for col in table.df.columns:
        kind, conforming = infer_type(table.df[col])
        if kind in {"text", "empty"} or conforming.all():
            continue
        index = list(conforming.index[~conforming])
        findings.append(
            _finding(
                table,
                check="invalid_type",
                title=f"Non-{kind} values in {col}",
                description=f"{conforming.mean():.0%} of values in {col} are of type {kind}; {len(index)} value(s) are not.",
                severity=Severity.MEDIUM,
                confidence=Confidence.LIKELY,
                columns=[col],
                rows=[row_number(i) for i in index],
                evidence=evidence(table.df, index, [col], settings),
            )
        )
    return findings


def missing_values(table: SheetTable, settings: Settings, skip: set[str]) -> list[Finding]:
    findings = []
    n = len(table.df)
    for col in table.df.columns:
        if col in skip:
            continue
        missing = table.df[col].isna()
        rate = missing.mean()
        if not 0 < rate < 0.5:  # mostly-empty columns are probably optional
            continue
        index = list(table.df.index[missing])
        findings.append(
            _finding(
                table,
                check="missing_values",
                title=f"Missing values in {col}",
                description=f"{len(index)} of {n} rows ({rate:.0%}) have no value in {col}.",
                severity=Severity.LOW,
                confidence=Confidence.LIKELY if rate <= 0.05 else Confidence.POSSIBLE,
                columns=[col],
                rows=[row_number(i) for i in index],
                evidence=evidence(table.df, index, list(table.df.columns)[:4], settings),
            )
        )
    return findings


def inconsistent_variants(table: SheetTable, settings: Settings) -> list[Finding]:
    """Values that differ only by case or surrounding spaces, e.g. 'North', 'north', 'North '."""
    findings = []
    for col in table.df.columns:
        s = table.df[col].dropna()
        if s.empty or infer_type(table.df[col])[0] != "text":
            continue
        keys = s.map(canonical)
        if keys.nunique() > MAX_VARIANT_CATEGORIES:
            continue
        bad_index = []
        for _, group in s.groupby(keys):
            counts = group.value_counts()
            if len(counts) > 1:
                bad_index.extend(group.index[group != counts.index[0]])
        if bad_index:
            bad_index.sort()
            findings.append(
                _finding(
                    table,
                    check="inconsistent_variants",
                    title=f"Inconsistent spelling in {col}",
                    description=f"{len(bad_index)} value(s) in {col} differ from the usual spelling only by case or spaces.",
                    severity=Severity.LOW,
                    confidence=Confidence.LIKELY,
                    columns=[col],
                    rows=[row_number(i) for i in bad_index],
                    evidence=evidence(
                        table.df,
                        bad_index,
                        [col],
                        settings,
                        # repr() keeps the stray spaces visible; clean() would strip them.
                        extra={i: {"exact_value": repr(table.df.at[i, col][: settings.max_value_chars])} for i in bad_index},
                    ),
                )
            )
    return findings


def outliers(table: SheetTable, settings: Settings) -> list[Finding]:
    """Robust outliers via the modified z-score (median absolute deviation), not mean ± 3σ."""
    findings = []
    for col in table.df.columns:
        if infer_type(table.df[col])[0] not in {"integer", "number"}:
            continue
        num = to_numeric(table.df[col]).dropna()
        if len(num) < MIN_OUTLIER_VALUES:
            continue
        median = num.median()
        mad = (num - median).abs().median()
        if mad == 0:
            continue
        z = 0.6745 * (num - median) / mad
        flagged = z[np.abs(z) > OUTLIER_Z]
        if flagged.empty:
            continue
        index = list(flagged.abs().sort_values(ascending=False).index)
        findings.append(
            _finding(
                table,
                check="outlier",
                title=f"Unusual values in {col}",
                description=f"{len(index)} value(s) in {col} are far from the typical range (median {median:g}).",
                severity=Severity.LOW,
                confidence=Confidence.POSSIBLE,
                columns=[col],
                rows=sorted(row_number(i) for i in index),
                evidence=evidence(table.df, index, [col], settings, extra={i: {"modified_z": f"{z[i]:.1f}"} for i in index}),
            )
        )
    return findings
