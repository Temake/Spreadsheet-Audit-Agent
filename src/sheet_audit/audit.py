"""Turn accepted rules and generic checks into findings. Every finding comes from code here."""

from sheet_audit import checks
from sheet_audit.config import Settings
from sheet_audit.gate import SheetTable, row_number
from sheet_audit.rules import describe_rule, evaluate_rule, rule_columns
from sheet_audit.schemas import (
    ArithmeticRule,
    Confidence,
    Finding,
    NotNullRule,
    ScoredRule,
    Severity,
    UniqueRule,
)

RULE_SEVERITY = {
    "unique": Severity.HIGH,
    "arithmetic": Severity.HIGH,
    "not_null": Severity.MEDIUM,
    "in_set": Severity.MEDIUM,
    "range": Severity.MEDIUM,
    "pattern": Severity.MEDIUM,
}
_SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
_CONFIDENCE_ORDER = {Confidence.CONFIRMED: 0, Confidence.LIKELY: 1, Confidence.POSSIBLE: 2}


def _drop_exact_duplicate_groups(table: SheetTable, rule: UniqueRule, failing: list) -> list:
    """Key collisions caused purely by exact duplicate rows are already reported as duplicate_rows."""
    sub = table.df.loc[failing]
    keep = []
    for _, group in sub.groupby(list(rule.columns), dropna=True):
        if len(group.drop_duplicates()) > 1:
            keep.extend(group.index)
    return sorted(keep)


def rule_findings(table: SheetTable, scored: ScoredRule, settings: Settings) -> list[Finding]:
    rule = scored.rule
    outcome = evaluate_rule(rule, table.df)
    failing = outcome.failing
    if isinstance(rule, UniqueRule):
        failing = _drop_exact_duplicate_groups(table, rule, failing)
    if outcome.error or not failing:
        return []

    cols = rule_columns(rule)
    extra = None
    if isinstance(rule, ArithmeticRule):
        extra = {i: {f"expected {rule.target}": f"{v:.10g}"} for i, v in outcome.expected.items()}
    shown_cols = cols if not isinstance(rule, (NotNullRule,)) else list(table.df.columns)[:4]
    description = (
        f"{len(failing)} of {outcome.applicable} rows break the rule “{describe_rule(rule)}”, "
        f"which holds for the other {outcome.applicable - len(failing)}."
    )
    return [
        Finding(
            sheet=table.name,
            check=f"rule:{rule.kind}",
            title=f"Rule broken: {describe_rule(rule)}",
            description=description,
            severity=RULE_SEVERITY[rule.kind],
            confidence=Confidence.CONFIRMED if scored.user_confirmed else Confidence.LIKELY,
            columns=cols,
            rows=[row_number(i) for i in failing],
            evidence=checks.evidence(table.df, failing, shown_cols, settings, extra=extra),
        )
    ]


def audit_table(table: SheetTable, rules: list[ScoredRule], settings: Settings, id_prefix: str) -> list[Finding]:
    accepted = [r for r in rules if r.accepted and not r.error]
    findings: list[Finding] = []
    for scored in accepted:
        findings.extend(rule_findings(table, scored, settings))

    not_null_cols = {r.rule.column for r in accepted if isinstance(r.rule, NotNullRule)}
    findings.extend(checks.duplicate_rows(table, settings))
    findings.extend(checks.formula_errors(table, settings))
    findings.extend(checks.invalid_types(table, settings))
    findings.extend(checks.missing_values(table, settings, skip=not_null_cols))
    findings.extend(checks.inconsistent_variants(table, settings))
    findings.extend(checks.outliers(table, settings))

    findings.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], _CONFIDENCE_ORDER[f.confidence], f.rows[0]))
    return [f.model_copy(update={"id": f"{id_prefix}-{n:02d}"}) for n, f in enumerate(findings, start=1)]
