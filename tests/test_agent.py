"""Agent wiring, driven by a scripted fake model so no network is needed."""

import os

import pytest
from dotenv import dotenv_values
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from sheet_audit.agent.agent import run_agent
from sheet_audit.loader import load_file
from sheet_audit.report import render_markdown
from sheet_audit.session import AuditSession


class ScriptedModel(GenericFakeChatModel):
    """Replays fixed AI messages, ignoring the tools it is bound to."""

    def bind_tools(self, tools, **kwargs):
        return self


def call(name: str, args: dict, n: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{n}", "type": "tool_call"}])


def test_agent_profiles_audits_and_returns_a_narrative(samples, settings):
    session = AuditSession(load_file(samples["orders"], settings), settings)
    extra_rule = {"kind": "range", "column": "tax", "min": 0, "max": 1000, "rationale": "tax is a small positive amount"}
    script = [
        call("profile_table", {"sheet": "Orders"}, 1),
        call("run_audit", {"sheet": "Orders", "rules": [extra_rule]}, 2),
        call("run_audit", {"sheet": "Notes"}, 3),  # refused; the agent must cope
        call(
            "AuditNarrative",
            {
                "summary": "Orders has broken totals and duplicate records.",
                "finding_notes": [
                    {"finding_id": "orders-01", "explanation": "Totals drive invoicing.", "recommendation": "Recompute totals."},
                    {"finding_id": "made-up-7", "explanation": "Invented.", "recommendation": "Ignore."},
                ],
            },
            4,
        ),
    ]
    narrative = run_agent(session, settings, model=ScriptedModel(messages=iter(script)))

    assert narrative.summary.startswith("Orders has")
    report = session.reports["Orders"]
    assert any(r.source.value == "agent" and r.accepted for r in report.rules)

    session.ensure_all_audited()  # Customers was skipped by the agent; the fallback audits it
    assert "Customers" in session.reports
    text = render_markdown(session.build_report("orders.xlsx", "agent (fake)", narrative))
    assert "Recompute totals." in text and "Invented." not in text


def test_step_budget_stops_a_looping_agent(samples, settings):
    settings.max_agent_steps = 4
    session = AuditSession(load_file(samples["orders"], settings), settings)
    loop = (call("profile_table", {"sheet": "Customers"}, n) for n in range(100))
    with pytest.raises(Exception, match="(?i)recursion"):
        run_agent(session, settings, model=ScriptedModel(messages=loop))


LIVE_ENV = {**dotenv_values(".env"), **os.environ}


@pytest.mark.bedrock
@pytest.mark.skipif(not LIVE_ENV.get("AUDIT_BEDROCK_MODEL_ID"), reason="AUDIT_BEDROCK_MODEL_ID not set")
def test_real_bedrock_agent_end_to_end(samples, monkeypatch):
    from sheet_audit.config import Settings

    for key, value in LIVE_ENV.items():
        if key.startswith(("AUDIT_", "AWS_")) and value:
            monkeypatch.setenv(key, value)
    settings = Settings()
    session = AuditSession(load_file(samples["orders"], settings), settings)
    narrative = run_agent(session, settings)
    assert set(session.reports) >= {"Orders", "Customers"}
    known = {f.id for r in session.reports.values() for f in r.findings}
    assert narrative.summary and any(n.finding_id in known for n in narrative.finding_notes)
