"""Cell normalisation and type inference shared by the gate, profiler, rules, and checks.

Every cell is held as a string (or None for missing) so that raw entries like "two" in a
numeric column survive loading and can be reported, instead of being silently coerced.
"""

import math
import re
import warnings
from datetime import date, datetime, time

import numpy as np
import pandas as pd

MISSING_TOKENS = {"", "na", "n/a", "null", "none", "nan"}
EXCEL_ERRORS = {"#DIV/0!", "#N/A", "#NAME?", "#NULL!", "#NUM!", "#REF!", "#VALUE!"}
_BOOLEAN_TOKENS = {"true", "false", "yes", "no"}


def normalize_cell(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return str(int(value)) if value.is_integer() else format(value, ".15g")
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    s = str(value)
    return None if s.strip().lower() in MISSING_TOKENS else s


def to_numeric(series: pd.Series) -> pd.Series:
    """Parse to float, tolerating whitespace and thousands separators. Unparseable -> NaN."""
    cleaned = series.map(lambda v: v.strip().replace(",", "") if isinstance(v, str) else v)
    return pd.to_numeric(cleaned, errors="coerce").replace([np.inf, -np.inf], np.nan)


def to_datetime(series: pd.Series) -> pd.Series:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pd.to_datetime(series, errors="coerce", format="mixed")


def canonical(value: str) -> str:
    """Comparison key that ignores case and surrounding whitespace."""
    return value.strip().casefold()


def looks_numeric(value: str) -> bool:
    try:
        float(value.strip().replace(",", ""))
        return True
    except ValueError:
        return False


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def looks_like_label(value: str) -> bool:
    """True for header-like text: not a number and not a date."""
    return not looks_numeric(value) and not _ISO_DATE.match(value.strip())


def infer_type(series: pd.Series) -> tuple[str, pd.Series]:
    """Return (type name, boolean mask of non-null values that conform to it).

    A type is assigned when at least 90% of non-null values conform; the rest are the
    "invalid entries" reported by the invalid_type check.
    """
    s = series.dropna()
    s = s[~s.isin(EXCEL_ERRORS)]
    if s.empty:
        return "empty", pd.Series(dtype=bool)
    num = to_numeric(s)
    if num.notna().mean() >= 0.9:
        valid = num.dropna()
        kind = "integer" if (valid == np.floor(valid)).all() else "number"
        return kind, num.notna()
    is_bool = s.map(lambda v: v.strip().lower() in _BOOLEAN_TOKENS)
    if is_bool.mean() >= 0.9:
        return "boolean", is_bool
    dates = to_datetime(s)
    if dates.notna().mean() >= 0.9:
        return "date", dates.notna()
    return "text", pd.Series(True, index=s.index)
