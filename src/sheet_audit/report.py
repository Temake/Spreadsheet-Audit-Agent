"""Render an AuditReport as Markdown. All file- and LLM-derived text is escaped."""

from collections import Counter

from sheet_audit.rules import describe_rule
from sheet_audit.sanitize import md
from sheet_audit.schemas import AuditReport, Confidence, Finding, Severity

DEFAULT_RECOMMENDATIONS = {
    "rule:unique": "Check whether these are the same record entered twice or distinct records that need new keys.",
    "rule:arithmetic": "Recalculate the affected rows from their inputs and find out why the stored values differ.",
    "rule:not_null": "Fill in the missing values from the source system, or document why they may be blank.",
    "rule:in_set": "Correct these values to one of the allowed categories, or add the new category to the list if it is valid.",
    "rule:range": "Confirm these values with the source; negative or out-of-range numbers often mean a sign or entry error.",
    "rule:pattern": "Correct the format of these values, for example a malformed email address.",
    "duplicate_rows": "Remove the duplicate copies after confirming they are not legitimate repeat entries.",
    "formula_error": "Fix the formulas that produce these errors in the original workbook, then re-export.",
    "invalid_type": "Correct these entries so the column holds a single type of value.",
    "missing_values": "Fill in the gaps if the column should be complete.",
    "inconsistent_variants": "Standardise the spelling so grouping and filtering treat these values as one category.",
    "outlier": "Check these values against the source; they may be valid but unusual, or typing errors.",
}
LIMITATIONS = [
    "Formulas are read as their last saved values; a workbook that was never recalculated may show stale or empty results.",
    "Rules inferred from the data describe what is typical, not what is correct; review them before relying on the results.",
    "Each sheet is audited on its own; relationships between sheets are not checked.",
]
_BADGE = {Severity.HIGH: "🔴 High", Severity.MEDIUM: "🟠 Medium", Severity.LOW: "🟡 Low"}


def _rows(rows: list[int], limit: int = 20) -> str:
    """[4, 5, 6, 9] -> '4–6, 9'"""
    parts, start, prev = [], None, None
    for r in sorted(set(rows)):
        if start is None:
            start = prev = r
        elif r == prev + 1:
            prev = r
        else:
            parts.append(f"{start}–{prev}" if prev > start else str(start))
            start = prev = r
    if start is not None:
        parts.append(f"{start}–{prev}" if prev > start else str(start))
    shown = ", ".join(parts[:limit])
    return shown + (f", … (+{len(parts) - limit} more)" if len(parts) > limit else "")


def _finding(f: Finding, notes: dict) -> list[str]:
    out = [
        f"#### {f.id} · {_BADGE[f.severity]} · {f.confidence.value}: {md(f.title, 120)}",
        "",
        md(f.description, 300),
        "",
        f"**Rows:** {_rows(f.rows)}",
        "",
    ]
    if f.evidence:
        cols = list(dict.fromkeys(k for e in f.evidence for k in e.values))
        out.append("| Row | " + " | ".join(md(c, 40) for c in cols) + " |")
        out.append("|---" * (len(cols) + 1) + "|")
        for e in f.evidence:
            out.append(f"| {e.row} | " + " | ".join(md(e.values.get(c, ""), 60) for c in cols) + " |")
        if len(f.rows) > len(f.evidence):
            out.append(f"\n*Showing {len(f.evidence)} of {len(f.rows)} rows.*")
        out.append("")
    note = notes.get(f.id)
    if note:
        out += [f"**Why it matters:** {md(note.explanation, 800)}", ""]
    recommendation = md(note.recommendation, 800) if note else DEFAULT_RECOMMENDATIONS.get(f.check, "Review these rows.")
    out += [f"**Recommended action:** {recommendation}", ""]
    return out


def render_markdown(report: AuditReport) -> str:
    findings = [f for s in report.sheets for f in s.findings]
    known_ids = {f.id for f in findings}
    # Notes are joined to findings by ID; the LLM cannot introduce a finding that code did not produce.
    notes = {n.finding_id: n for n in report.narrative.finding_notes if n.finding_id in known_ids} if report.narrative else {}

    out = [
        "# Spreadsheet Audit Report",
        "",
        f"- **File:** {md(report.source_file, 120)}",
        f"- **Generated:** {report.generated_at:%Y-%m-%d %H:%M} UTC",
        f"- **Mode:** {md(report.mode, 120)}",
        "",
        "## Summary",
        "",
    ]
    if report.narrative:
        out += [md(report.narrative.summary, 2000), ""]

    counts = Counter((f.severity, f.confidence) for f in findings)
    if findings:
        out += ["| Severity | " + " | ".join(c.value for c in Confidence) + " |", "|---|---|---|---|"]
        for sev in Severity:
            out.append(f"| {_BADGE[sev]} | " + " | ".join(str(counts[(sev, c)]) for c in Confidence) + " |")
        out.append("")
        # One bad entry often trips several checks; point the reader at the root cause.
        per_row = Counter((f.sheet, r) for f in findings for r in set(f.rows))
        hotspots = [(k, n) for k, n in per_row.most_common(5) if n >= 3]
        if hotspots:
            listed = ", ".join(f"{md(s, 40)} row {r} ({n} findings)" for (s, r), n in hotspots)
            out += [f"**Rows with several issues** (often one root cause): {listed}.", ""]
    else:
        out += ["No issues were found in the audited sheets.", ""]

    for sheet in report.sheets:
        gate = sheet.gate
        out += [f"## Sheet: {md(gate.sheet, 64)}", ""]
        if not gate.auditable:
            out += [f"**Not audited:** {md('; '.join(gate.reasons), 300)}", ""]
            continue
        out.append(f"{gate.n_rows} data rows × {gate.n_columns} columns, header on row {gate.header_row}.")
        out.append("")
        for w in gate.warnings:
            out.append(f"- ⚠️ {md(w, 200)}")
        if gate.warnings:
            out.append("")
        if sheet.rules:
            out += ["### Contract", "", "| Rule | Holds | Source | Used |", "|---|---|---|---|"]
            for r in sheet.rules:
                holds = r.error or (f"{r.passed}/{r.applicable} ({r.support:.0%})" if r.applicable else "no applicable rows")
                used = ("✅ confirmed" if r.user_confirmed else "✅") if r.accepted else "—"
                out.append(f"| {md(describe_rule(r.rule), 100)} | {md(holds, 80)} | {r.source.value} | {used} |")
            out.append("")
        out += ["### Findings", ""]
        if not sheet.findings:
            out += ["No issues found.", ""]
        for f in sheet.findings:
            out += _finding(f, notes)

    out += ["## Limitations", ""]
    extra = report.narrative.limitations if report.narrative else []
    out += [f"- {md(x, 300)}" for x in [*LIMITATIONS, *extra]]
    return "\n".join(out) + "\n"
