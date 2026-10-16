"""Security boundaries: tool limits, untrusted-data handling, report escaping, logging."""

import json
import logging

import pytest
from langchain_core.tools import ToolException
from make_samples import INJECTION

from sheet_audit.agent.tools import build_tools
from sheet_audit.loader import load_file
from sheet_audit.report import render_markdown
from sheet_audit.sanitize import clean, md
from sheet_audit.schemas import AuditNarrative, FindingNote
from sheet_audit.session import AuditSession


@pytest.fixture
def session(samples, settings):
    return AuditSession(load_file(samples["orders"], settings), settings)


@pytest.fixture
def tools(session):
    return {t.name: t for t in build_tools(session)}


def test_agent_has_exactly_two_narrow_tools(tools):
    assert set(tools) == {"profile_table", "run_audit"}


def test_tool_output_marks_file_content_as_untrusted(tools):
    out = json.loads(tools["profile_table"].invoke({"sheet": "Orders"}))
    assert "untrusted" in out["note"]
    assert all(len(v) <= 80 for c in out["profile"]["columns"] for v in c["examples"])


def test_tools_refuse_unknown_and_unauditable_sheets(tools):
    assert "unknown sheet" in tools["profile_table"].invoke({"sheet": "Secrets"})
    assert "not auditable" in tools["run_audit"].invoke({"sheet": "Notes"})


def test_each_sheet_is_audited_at_most_once(tools):
    tools["run_audit"].invoke({"sheet": "Orders"})
    assert "already been audited" in tools["run_audit"].invoke({"sheet": "Orders"})


def test_profile_calls_are_bounded(tools):
    for _ in range(2):
        tools["profile_table"].invoke({"sheet": "Orders"})
    assert "already called" in tools["profile_table"].invoke({"sheet": "Orders"})


def test_malicious_rule_from_the_llm_is_rejected_before_running(tools, session):
    bad = {"kind": "arithmetic", "target": "total", "expression": "__import__('os').system('x')"}
    result = tools["run_audit"].invoke({"sheet": "Orders", "rules": [bad]})
    assert "error" in result.lower() or "validation" in result.lower()
    assert "Orders" not in session.reports  # nothing ran


def test_agent_cannot_drop_profiler_candidates(session):
    report = session.audit("Orders", proposed=[])
    assert any(r.source.value == "profiler" and r.accepted for r in report.rules)
    assert any(f.check.startswith("rule:") for f in report.findings)


def test_narrative_cannot_add_findings_and_is_escaped(session):
    session.ensure_all_audited()
    real_id = session.reports["Orders"].findings[0].id
    narrative = AuditNarrative(
        summary="All good. <script>alert(1)</script> [click](http://evil.example)",
        finding_notes=[
            FindingNote(finding_id="orders-99", explanation="Invented issue", recommendation="Wire money"),
            FindingNote(finding_id=real_id, explanation="Real **explanation**", recommendation="Fix it"),
        ],
    )
    md_text = render_markdown(session.build_report("orders.xlsx", "test", narrative))
    assert "Invented issue" not in md_text and "Wire money" not in md_text
    assert "<script>" not in md_text and "[click](" not in md_text
    assert "Real \\*\\*explanation\\*\\*" in md_text


def test_cell_values_can_be_withheld_from_the_llm(samples, settings):
    settings.share_sample_values = False
    tools = {t.name: t for t in build_tools(AuditSession(load_file(samples["orders"], settings), settings))}
    out = tools["profile_table"].invoke({"sheet": "Orders"})
    assert INJECTION not in out and "Ada Park" not in out


def test_terminal_escape_sequences_are_stripped():
    assert clean("ok\x1b[2J\x1b]0;pwned\x07") == "ok[2J]0;pwned"
    assert md("| a | `b` |") == "\\| a \\| \\`b\\` \\|"


def test_logs_contain_no_cell_values(samples, settings, caplog):
    caplog.set_level(logging.DEBUG)
    session = AuditSession(load_file(samples["orders"], settings), settings)
    session.ensure_all_audited()
    for secret in ("Ada Park", "example.com", "ORD-1001", "IGNORE ALL", "Orders"):
        assert secret not in caplog.text
