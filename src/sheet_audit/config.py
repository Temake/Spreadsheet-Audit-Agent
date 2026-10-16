"""Runtime settings, read from environment variables (prefix ``AUDIT_``) or a local .env file."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUDIT_", env_file=".env", extra="ignore")

    # LLM. Credentials are never stored here; boto3 resolves them from the AWS chain.
    bedrock_model_id: str | None = None
    aws_region: str | None = None
    llm_max_tokens: int = Field(4096, gt=0)
    llm_timeout_s: int = Field(60, gt=0)
    max_agent_steps: int = Field(25, gt=0, le=100)

    # Input limits.
    max_file_mb: float = Field(10, gt=0)
    max_uncompressed_mb: float = Field(100, gt=0)
    max_rows: int = Field(100_000, gt=0)
    max_columns: int = Field(200, gt=0)
    max_sheets: int = Field(20, gt=0)

    # Auditability gate.
    min_rows: int = Field(5, gt=0)
    min_columns: int = Field(2, gt=0)

    # A rule is pre-accepted only if the data mostly supports it.
    min_rule_support: float = Field(0.9, gt=0, le=1)
    min_rule_rows: int = Field(5, gt=0)

    # Privacy: whether any cell values (top values, examples) may be sent to the LLM.
    share_sample_values: bool = True
    max_value_chars: int = Field(80, gt=0)
