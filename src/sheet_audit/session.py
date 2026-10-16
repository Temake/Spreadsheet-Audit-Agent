import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone

from sheet_audit.audit import audit_table
from sheet_audit.config import Settings
from sheet_audit.gate import SheetTable
from sheet_audit.profiling import profile_sheet
from sheet_audit.rules import score_rule
from sheet_audit.schemas import (
    AuditNarrative,
    AuditReport,
    ContractFile,
    Rule,
    RuleSource,
    ScoredRule,
    SheetContract,
    SheetProfile,
    SheetReport,
)

log = logging.getLogger(__name__)


Confirmer = Callable[[str, list[ScoredRule]], list[ScoredRule]]

MAX_PROFILE_CALLS_PER_SHEET = 2


class AuditError(Exception):
    
 def auto_confirmer(sheet: str, rules: list[ScoredRule]) -> list[ScoredRule]:
     
     return rules


class AuditSession:
    def __init__(self, tables: list[SheetTable], settings: Settings, confirmer: Confirmer = auto_confirmer, contract: ContractFile | None = None):
        self.settings = settings
        self.tables = {t.name: t for t in tables}
        self.confirmer = confirmer
        self.contract = contract
        self.profiles: dict[str, SheetProfile] = {}
        self.reports: dict[str, SheetReport] = {}
        self._profile_calls: dict[str, int] = {}
        self._prefixes = self._make_prefixes()

    def _make_prefixes(self) -> dict[str, str]:
        prefixes, used = {}, set()
        for i, name in enumerate(self.tables, start=1):
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:20] or f"sheet{i}"
            if slug in used:
                slug = f"{slug}{i}"
            used.add(slug)
            prefixes[name] = slug
        return prefixes

    # --- helpers ---

    def _table(self, sheet: str) -> SheetTable:
        table = self.tables.get(sheet)
        if table is None:
            raise AuditError(f"unknown sheet; available sheets: {list(self.tables)}")
        if not table.gate.auditable:
            raise AuditError(f"sheet is not auditable: {'; '.join(table.gate.reasons)}")
        return table

    def overview(self) -> list[dict]:
        return [g.model_dump(exclude={"header_row"}) for g in (t.gate for t in self.tables.values())]

    def pending_sheets(self) -> list[str]:
        return [n for n, t in self.tables.items() if t.gate.auditable and n not in self.reports]

    # --- the two operations exposed as tools ---

    def profile(self, sheet: str) -> SheetProfile:
        table = self._table(sheet)
        calls = self._profile_calls.get(sheet, 0) + 1
        if calls > MAX_PROFILE_CALLS_PER_SHEET:
            raise AuditError("profile_table was already called for this sheet; use the earlier result")
        self._profile_calls[sheet] = calls
        if sheet not in self.profiles:
            self.profiles[sheet] = profile_sheet(table, self.settings)
            log.info("profiled sheet %d: %d candidate rules", list(self.tables).index(sheet) + 1, len(self.profiles[sheet].candidate_rules))
        return self.profiles[sheet]

    def audit(self, sheet: str, proposed: list[Rule]) -> SheetReport:
        table = self._table(sheet)
        if sheet in self.reports:
            raise AuditError("this sheet has already been audited; each sheet is audited once")

        sheet_contract: SheetContract | None = self.contract.sheets.get(sheet) if self.contract else None
        if sheet_contract is not None:
            scored = [score_rule(r, table.df, RuleSource.CONTRACT_FILE) for r in sheet_contract.rules]
            for s in scored:
                s.accepted = s.error is None
                s.user_confirmed = s.accepted
        else:
            # Profiler candidates are always included, so injected text can't make the agent drop them.
            candidates = self.profiles[sheet].candidate_rules if sheet in self.profiles else profile_sheet(table, self.settings).candidate_rules
            scored, seen = [], set()
            for rule, source in [*((r, RuleSource.PROFILER) for r in candidates), *((r, RuleSource.AGENT) for r in proposed)]:
                key = rule.model_dump_json(exclude={"rationale"})
                if key in seen:
                    continue
                seen.add(key)
                s = score_rule(rule, table.df, source)
                s.accepted = (
                    s.error is None
                    and s.applicable >= self.settings.min_rule_rows
                    and (s.support or 0) >= self.settings.min_rule_support
                )
                scored.append(s)
            scored = self.confirmer(sheet, scored)

        findings = audit_table(table, scored, self.settings, self._prefixes[sheet])
        report = SheetReport(gate=table.gate, rules=scored, findings=findings)
        self.reports[sheet] = report
        log.info("audited sheet %d: %d rules accepted, %d findings", list(self.tables).index(sheet) + 1, sum(r.accepted for r in scored), len(findings))
        return report

    # --- completion ---

    def ensure_all_audited(self) -> None:
        """Deterministic fallback: audit any auditable sheet the agent did not get to."""
        for sheet in self.pending_sheets():
            self.audit(sheet, [])

    def accepted_contract(self) -> ContractFile:
        return ContractFile(
            sheets={name: SheetContract(rules=[r.rule for r in rep.rules if r.accepted]) for name, rep in self.reports.items()}
        )

    def build_report(self, source_file: str, mode: str, narrative: AuditNarrative | None) -> AuditReport:
        sheets = [self.reports[n] if n in self.reports else SheetReport(gate=t.gate) for n, t in self.tables.items()]
        return AuditReport(
            source_file=source_file,
            generated_at=datetime.now(timezone.utc),
            mode=mode,
            sheets=sheets,
            narrative=narrative,
        )
