
import argparse
import logging
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from sheet_audit.config import Settings
from sheet_audit.loader import InputError, load_file
from sheet_audit.report import render_markdown
from sheet_audit.rules import describe_rule
from sheet_audit.sanitize import clean
from sheet_audit.schemas import ContractFile, ScoredRule, Severity
from sheet_audit.session import AuditSession, auto_confirmer

log = logging.getLogger("sheet_audit")

EXIT_OK, EXIT_INPUT_ERROR = 0, 2


def interactive_confirmer(sheet: str, rules: list[ScoredRule]) -> list[ScoredRule]:
    """Show proposed rules with their support and let the user toggle them."""
    print(f"\nProposed contract for sheet '{clean(sheet, 64)}':")
    for n, r in enumerate(rules, start=1):
        mark = "x" if r.accepted else " "
        holds = r.error or (f"holds in {r.passed}/{r.applicable} rows ({r.support:.0%})" if r.applicable else "no applicable rows")
        print(f"  [{mark}] {n:>2}. {clean(describe_rule(r.rule), 90)}  — {clean(holds, 60)}  [{r.source.value}]")
    while True:
        answer = input("Enter to accept [x] rules, numbers to toggle (e.g. 2 5), or 'none': ").strip().lower()
        if answer == "":
            break
        if answer == "none":
            for r in rules:
                r.accepted = False
            break
        try:
            picks = {int(tok) for tok in answer.replace(",", " ").split()}
        except ValueError:
            print("  Please enter rule numbers, 'none', or press Enter.")
            continue
        for n in picks:
            if 1 <= n <= len(rules) and rules[n - 1].error is None:
                rules[n - 1].accepted = not rules[n - 1].accepted
        for n, r in enumerate(rules, start=1):
            print(f"  [{'x' if r.accepted else ' '}] {n:>2}. {clean(describe_rule(r.rule), 90)}")
    for r in rules:
        r.user_confirmed = r.accepted
    return rules


def load_contract(path: Path) -> ContractFile:
    try:
        return ContractFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise InputError(f"invalid contract file {path.name}: {exc}") from exc


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sheet-audit", description="Audit a CSV or XLSX file for data quality issues.")
    p.add_argument("file", type=Path, help="spreadsheet to audit (.csv or .xlsx); never modified")
    p.add_argument("-o", "--out", type=Path, help="report path (default: <file name>_audit.md in the current directory)")
    p.add_argument("--auto", action="store_true", help="accept well-supported rules without asking (for CI and scripts)")
    p.add_argument("--no-llm", action="store_true", help="deterministic audit only; no LLM calls")
    p.add_argument("--contract", type=Path, help="YAML contract to use instead of inferring rules")
    p.add_argument("--save-contract", type=Path, help="write the accepted rules to this YAML file")
    p.add_argument("--sheet", action="append", help="audit only this sheet (repeatable)")
    p.add_argument("--force", action="store_true", help="overwrite an existing report or contract file")
    p.add_argument("-v", "--verbose", action="store_true", help="log progress (metadata only, never cell values)")
    return p.parse_args(argv)


def _check_output(path: Path, source: Path, force: bool) -> None:
    if path.resolve() == source.resolve():
        raise InputError("output path is the input file; the original file is never overwritten")
    if path.exists() and not force:
        raise InputError(f"{path} already exists; use --force to overwrite")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings()
    out = args.out or Path(f"{args.file.stem}_audit.md")

    try:
        _check_output(out, args.file, args.force)
        if args.save_contract:
            _check_output(args.save_contract, args.file, args.force)
        contract = load_contract(args.contract) if args.contract else None
        tables = load_file(args.file, settings)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    if args.sheet:
        tables = [t for t in tables if t.name in set(args.sheet)]
        if not tables:
            print("error: none of the requested sheets exist", file=sys.stderr)
            return EXIT_INPUT_ERROR

    if not args.auto and not args.contract and not sys.stdin.isatty():
        print("error: no terminal for confirming rules; pass --auto or --contract", file=sys.stderr)
        return EXIT_INPUT_ERROR
    confirmer = auto_confirmer if (args.auto or args.contract) else interactive_confirmer
    session = AuditSession(tables, settings, confirmer=confirmer, contract=contract)

    narrative, mode = None, "deterministic (no LLM)"
    if not args.no_llm:
        from sheet_audit.agent.agent import run_agent  # imported lazily so --no-llm needs no AWS setup

        try:
            narrative = run_agent(session, settings)
            mode = f"agent ({settings.bedrock_model_id} on Bedrock)"
        except Exception as exc:  # any model, network, or budget failure falls back to deterministic
            # Log the type only: model error messages can echo file contents.
            log.warning("agent unavailable (%s); continuing with a deterministic audit", type(exc).__name__)
            mode = "deterministic (agent failed; see logs)"
    session.ensure_all_audited()

    report = session.build_report(args.file.name, mode, narrative)
    out.write_text(render_markdown(report), encoding="utf-8")
    if args.save_contract:
        data = session.accepted_contract().model_dump(mode="json", exclude_none=True)
        args.save_contract.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    findings = [f for s in report.sheets for f in s.findings]
    high = sum(f.severity == Severity.HIGH for f in findings)
    skipped = [s.gate.sheet for s in report.sheets if not s.gate.auditable]
    print(f"{len(findings)} finding(s), {high} high severity. Report written to {out}")
    if skipped:
        print(f"Not audited: {', '.join(clean(s, 64) for s in skipped)} (see report for reasons)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
