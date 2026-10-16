"""End-to-end: the planted issues in the sample workbook are found, and nothing is invented."""

import pytest
from make_samples import EXPECTED

from sheet_audit.loader import load_file
from sheet_audit.schemas import Confidence, ContractFile, NotNullRule, SheetContract
from sheet_audit.session import AuditSession


@pytest.fixture(scope="module")
def orders_report(samples):
    from sheet_audit.config import Settings

    session = AuditSession(load_file(samples["orders"], Settings(_env_file=None)), Settings(_env_file=None))
    session.ensure_all_audited()
    return session.reports["Orders"]


def rows_for(report, check, column=None):
    # For arithmetic rules the target column is listed first.
    hits = [f for f in report.findings if f.check == check and (column is None or f.columns[0] == column)]
    return sorted({r for f in hits for r in f.rows})


@pytest.mark.parametrize(("check", "column", "rows"), EXPECTED, ids=[f"{c}-{col}" for c, col, _ in EXPECTED])
def test_planted_issue_is_found_at_the_right_rows(orders_report, check, column, rows):
    assert rows_for(orders_report, check, column) == rows


def test_exact_duplicates_are_not_double_reported_as_key_collisions(orders_report):
    assert rows_for(orders_report, "rule:unique") == [17, 22]


def test_every_finding_has_evidence_from_real_rows(orders_report):
    for f in orders_report.findings:
        assert f.rows, f.id
        assert f.evidence, f.id
        assert {e.row for e in f.evidence} <= set(f.rows), f.id


def test_finding_ids_are_unique(orders_report):
    ids = [f.id for f in orders_report.findings]
    assert len(ids) == len(set(ids))


def test_inferred_rules_give_likely_not_confirmed(orders_report):
    rule_findings = [f for f in orders_report.findings if f.check.startswith("rule:")]
    assert rule_findings and all(f.confidence == Confidence.LIKELY for f in rule_findings)


@pytest.mark.parametrize("which", ["clean"])
def test_clean_file_produces_no_findings(samples, settings, which):
    session = AuditSession(load_file(samples[which], settings), settings)
    session.ensure_all_audited()
    findings = [f for r in session.reports.values() for f in r.findings]
    assert findings == [], [f.title for f in findings]


def test_clean_sheet_in_workbook_produces_no_findings(samples, settings):
    session = AuditSession(load_file(samples["orders"], settings), settings)
    session.ensure_all_audited()
    assert session.reports["Customers"].findings == []
    assert "Notes" not in session.reports


def test_contract_file_rules_are_confirmed(samples, settings):
    contract = ContractFile(sheets={"Orders": SheetContract(rules=[NotNullRule(column="customer")])})
    session = AuditSession(load_file(samples["orders"], settings), settings, contract=contract)
    report = session.audit("Orders", [])
    [f] = [f for f in report.findings if f.check == "rule:not_null"]
    assert f.confidence == Confidence.CONFIRMED and f.rows == [14, 29]
    assert all(r.source.value == "contract-file" for r in report.rules)


def test_weak_rules_are_not_accepted(samples, settings):
    from sheet_audit.schemas import InSetRule

    session = AuditSession(load_file(samples["orders"], settings), settings)
    report = session.audit("Orders", [InSetRule(column="region", values=["North"])])
    [weak] = [r for r in report.rules if r.source.value == "agent"]
    assert weak.support < settings.min_rule_support and not weak.accepted
    assert not any("region is one of: North" == f.title.removeprefix("Rule broken: ") for f in report.findings)
