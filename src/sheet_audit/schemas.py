import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from sheet_audit import expr

ColumnName = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Confidence(StrEnum):
    CONFIRMED = "confirmed" 
    LIKELY = "likely"  
    POSSIBLE = "possible"  


# --- Contract rules: a closed set of rule types. The LLM can only express these. ---


class _Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rationale: str | None = Field(None, max_length=300, description="Why this rule should hold.")


class UniqueRule(_Rule):
    kind: Literal["unique"] = "unique"
    columns: list[ColumnName] = Field(min_length=1, max_length=5)


class NotNullRule(_Rule):
    kind: Literal["not_null"] = "not_null"
    column: ColumnName


class InSetRule(_Rule):
    kind: Literal["in_set"] = "in_set"
    column: ColumnName
    values: list[Annotated[str, StringConstraints(max_length=128)]] = Field(min_length=1, max_length=100)


class RangeRule(_Rule):
    kind: Literal["range"] = "range"
    column: ColumnName
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _check_bounds(self):
        if self.min is None and self.max is None:
            raise ValueError("range rule needs min, max, or both")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


# A quantified group that is itself quantified, e.g. (a+)+ : the classic catastrophic-backtracking shape.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")


class PatternRule(_Rule):
    kind: Literal["pattern"] = "pattern"
    column: ColumnName
    regex: str = Field(min_length=1, max_length=100, description="Python regex the whole value must match.")

    @field_validator("regex")
    @classmethod
    def _safe_regex(cls, v: str) -> str:
        if _NESTED_QUANTIFIER.search(v) or re.search(r"\\\d", v):
            raise ValueError("regex uses nested quantifiers or backreferences, which are not allowed")
        try:
            re.compile(v)
        except re.error as exc:  # not a ValueError, so Pydantic would not catch it
            raise ValueError(f"invalid regex: {exc}") from exc
        return v


class ArithmeticRule(_Rule):
    kind: Literal["arithmetic"] = "arithmetic"
    target: ColumnName
    expression: str = Field(
        max_length=expr.MAX_EXPRESSION_CHARS,
        description="Columns, numbers and + - * / ( ). Quote names with spaces as [Unit Price].",
    )
    tolerance: float = Field(0.01, ge=0, le=1000, description="Allowed absolute difference.")

    @field_validator("expression")
    @classmethod
    def _parses(cls, v: str) -> str:
        expr.parse(v)
        return v


Rule = Annotated[
    UniqueRule | NotNullRule | InSetRule | RangeRule | PatternRule | ArithmeticRule,
    Field(discriminator="kind"),
]


class RuleSource(StrEnum):
    PROFILER = "profiler"  
    AGENT = "agent"  # proposed by the LLM
    CONTRACT_FILE = "contract-file"  

class ScoredRule(BaseModel):
    rule: Rule
    source: RuleSource
    applicable: int = 0
    passed: int = 0
    error: str | None = None
    accepted: bool = False
    user_confirmed: bool = False

    @property
    def support(self) -> float | None:
        return self.passed / self.applicable if self.applicable else None


class SheetContract(BaseModel):
    rules: list[Rule] = Field(default_factory=list)


class ContractFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sheets: dict[str, SheetContract] = Field(default_factory=dict)


# --- Gate, profile, findings ---


class GateResult(BaseModel):
    sheet: str
    auditable: bool
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    header_row: int | None = None  # 1-based spreadsheet row number
    n_rows: int = 0
    n_columns: int = 0


class ValueCount(BaseModel):
    value: str
    count: int


class ColumnProfile(BaseModel):
    name: str
    inferred_type: Literal["integer", "number", "date", "boolean", "text", "empty"]
    non_null: int
    null_rate: float
    distinct: int
    conforming_ratio: float  # share of non-null values that match inferred_type
    nonconforming: int
    top_values: list[ValueCount] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    min: float | None = None
    median: float | None = None
    max: float | None = None


class SheetProfile(BaseModel):
    sheet: str
    n_rows: int
    columns: list[ColumnProfile]
    candidate_rules: list[Rule]
    warnings: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    row: int  
    values: dict[str, str]


class Finding(BaseModel):
    id: str = ""
    sheet: str
    check: str
    title: str
    description: str
    severity: Severity
    confidence: Confidence
    columns: list[str]
    rows: list[int]
    evidence: list[Evidence] = Field(default_factory=list, max_length=5)


# --- LLM narrative: the only free text the LLM contributes to the report ---


class FindingNote(BaseModel):
    finding_id: str = Field(max_length=40)
    explanation: str = Field(max_length=800, description="Why this matters, in plain language.")
    recommendation: str = Field(max_length=800, description="A concrete corrective action.")


class AuditNarrative(BaseModel):
    """Final answer of the audit agent. It can explain findings but never add new ones."""

    summary: str = Field(max_length=2000, description="Overall assessment of the file in 2-5 sentences.")
    finding_notes: list[FindingNote] = Field(default_factory=list, max_length=200)
    limitations: list[Annotated[str, StringConstraints(max_length=300)]] = Field(
        default_factory=list, max_length=5, description="Caveats about this particular audit."
    )


class SheetReport(BaseModel):
    gate: GateResult
    rules: list[ScoredRule] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)


class AuditReport(BaseModel):
    source_file: str
    generated_at: datetime
    mode: str
    sheets: list[SheetReport]
    narrative: AuditNarrative | None = None
