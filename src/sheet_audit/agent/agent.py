"""Build and run the audit agent: Claude on AWS Bedrock, orchestrated by LangChain."""

import json
import logging

from botocore.config import Config
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_aws import ChatBedrockConverse
from langchain_core.language_models import BaseChatModel

from sheet_audit.agent.prompts import SYSTEM_PROMPT
from sheet_audit.agent.tools import UNTRUSTED_NOTE, build_tools
from sheet_audit.config import Settings
from sheet_audit.schemas import AuditNarrative
from sheet_audit.session import AuditSession

log = logging.getLogger(__name__)


class AgentUnavailable(Exception):
    pass


def bedrock_model(settings: Settings) -> BaseChatModel:
    if not settings.bedrock_model_id:
        raise AgentUnavailable("AUDIT_BEDROCK_MODEL_ID is not set")
    return ChatBedrockConverse(
        model=settings.bedrock_model_id,
        region_name=settings.aws_region,
        temperature=0,
        max_tokens=settings.llm_max_tokens,
        config=Config(
            connect_timeout=10,
            read_timeout=settings.llm_timeout_s,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def run_agent(session: AuditSession, settings: Settings, model: BaseChatModel | None = None) -> AuditNarrative:
    """Run the agent to completion. Raises on model errors or when the step budget is exhausted."""
    agent = create_agent(
        model=model or bedrock_model(settings),
        tools=build_tools(session),
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(AuditNarrative),
    )
    overview = json.dumps({"note": UNTRUSTED_NOTE, "sheets": session.overview()})
    result = agent.invoke(
        {"messages": [{"role": "user", "content": f"Audit this file. Sheet overview:\n{overview}"}]},
        config={"recursion_limit": settings.max_agent_steps},
    )
    narrative = result.get("structured_response")
    if not isinstance(narrative, AuditNarrative):
        raise AgentUnavailable("agent finished without a structured narrative")
    log.info("agent finished: %d finding notes", len(narrative.finding_notes))
    return narrative
