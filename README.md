# Spreadsheet Audit Agent

An AI agent that audits CSV and Excel files for data quality problems: duplicate records, missing values, invalid entries, broken calculations, and unusual values. It writes a Markdown report in which every finding points to the exact rows and values behind it.

It is built around one principle: **the LLM reasons, the code decides.** Claude (via LangChain on AWS Bedrock) looks at a sheet's profile and proposes what rules the data should follow. Deterministic code scores those rules against every row, lets the user confirm them, and produces all findings. The LLM then explains the findings and recommends fixes, but it cannot add a finding, remove one, or change its severity.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python samples/make_samples.py   # optional: generates two demo files with known problems (see below)

# No AWS needed: a fully deterministic audit, confirming each proposed rule interactively
sheet-audit samples/orders_with_issues.xlsx --no-llm

# Same, but accept well-supported rules without asking (scripts/CI)
sheet-audit samples/orders_with_issues.xlsx --no-llm --auto --force

# With the agent (Claude Sonnet 4.6 on Bedrock, using a Bedrock API key)
cp .env.example .env    # set AWS_BEARER_TOKEN_BEDROCK; model ID and region are pre-filled
sheet-audit samples/orders_with_issues.xlsx
```

### Auditing your own file

Pass the file's path. A relative path is resolved from the folder you run the command in, so a file in the project folder can be given by name alone. Quote paths that contain spaces.

```bash
sheet-audit sales.xlsx                                        # file in the current folder
sheet-audit data/sales.csv                                    # file in a subfolder
sheet-audit "C:\Users\me\Documents\Sales Q3.xlsx"             # absolute path (Windows: Explorer → right-click → Copy as path)
sheet-audit ~/Documents/sales.xlsx -o ~/Documents/sales_audit.md
```

The file is only read, never modified. The report goes to `<file name>_audit.md` in the current folder unless you pass `-o`.

### Sample files

`python samples/make_samples.py` writes two files into `samples/` for trying the tool and for the tests:

- `orders_with_issues.xlsx`: an orders sheet with 13 deliberately planted problems (a duplicate row, a reused order ID, a total that doesn't add up, a `#DIV/0!`, "Shiped", a 999 price typo, a prompt-injection attempt in a notes cell, …), plus a clean Customers sheet and a Notes sheet that isn't a table.
- `clean_orders.csv`: the same kind of data with no problems. The audit should report nothing.

Because the planted rows are known (`EXPECTED` in the script), the tests can check that every problem is found at exactly the right rows, and that nothing is invented.

| Option | Effect |
|---|---|
| *(default)* | Interactive: shows each proposed rule with how often it holds, and lets you toggle rules before the audit runs |
| `--auto` | Accept rules that hold in at least 90% of rows, without asking (for CI and scripts) |
| `--contract rules.yaml` | Use your own rules instead of inferring them; violations are reported as *confirmed* |
| `--save-contract rules.yaml` | Save the accepted rules, to review, edit, and reuse on next month's file |
| `--no-llm` | Deterministic audit only; no network calls |
| `--sheet NAME` | Audit only the named sheet(s) |
| `-o report.md` | Report path. Refuses to overwrite the input, or an existing file without `--force` |
| `--no-open` | Don't open the report when done. By default it opens in VS Code (when run from its terminal) or your default app; never in scripts or CI |

## How it works

```
file ─► loader ─► gate ──── not a table? ──► "Not audited: no header row found"
                   │
                   ▼
   ┌─────────── agent (LangChain + Claude) ───────────┐
   │  profile_table(sheet)  → types, null rates,      │
   │                          stats, candidate rules  │
   │  reasons about the data and proposes extra rules │
   │  run_audit(sheet, rules)                         │
   │     ├ score every rule against every row         │
   │     ├ user confirms rules (or --auto)            │
   │     └ rules + generic checks → findings with IDs │
   │  writes narrative: summary + notes per finding ID│
   └──────────────────────────────────────────────────┘
                   │
                   ▼
       report.md  (findings from code, explanations from LLM, joined by ID)
```

**Gate.** Before anything is audited, each sheet must look like a table: a header row (it skips title rows above it), at least 5 data rows and 2 columns, and sizes within limits. A sheet that fails is listed in the report with the reason. This is the answer to "what if someone uploads a random spreadsheet?". The tool refuses what it can't audit instead of reporting layout as anomalies.

**Two tiers of checks.**

| Tier | Checks | Needs a contract? |
|---|---|---|
| Generic | exact duplicate rows · spreadsheet errors (`#DIV/0!`, `#REF!`) · wrong-type entries in a typed column · missing values · spelling variants (`North` / `north ` ) · robust outliers (median absolute deviation) | No |
| Contract | `unique` · `not_null` · `in_set` · `range` · `pattern` · `arithmetic` (e.g. `total = subtotal + tax`) | Yes: inferred, proposed by the agent, or supplied |

**Rules must be earned.** A proposed rule is only used if it holds in at least 90% of applicable rows (configurable). If `total = subtotal + tax` holds in 47 of 49 rows, the other 2 rows are strong findings. If it held in only 40%, the rule is probably wrong, so it's dropped rather than flagging 60% of the sheet.

**Severity and confidence are separate.** Severity is how much the issue matters (a broken calculation is high, a spelling variant is low). Confidence is how sure we are that it's a real problem, and it's decided by evidence, not by the LLM:

| Confidence | Meaning |
|---|---|
| confirmed | A user-confirmed rule is broken, or the fact is certain (exact duplicate, `#REF!`) |
| likely | A rule the data strongly supports is broken, or a value doesn't match its column's type |
| possible | Statistical signal only, such as an outlier; worth a human look |

## Security decisions

| Risk | Mitigation |
|---|---|
| Prompt injection in cell text | All file content reaches the LLM inside tool results that are labelled untrusted, and truncated. More importantly, the LLM has no power that injection could abuse: it can't create, hide, or rescore findings, and it can't drop the profiler's rules. The report joins LLM notes to findings by ID, and notes for unknown IDs are discarded. The sample file contains an injection attempt. |
| Code execution through rules | Rules are a closed set of Pydantic types. Arithmetic rules use a hand-written parser for `+ − × ÷` over column names ([`expr.py`](src/sheet_audit/expr.py)); nothing is ever passed to `eval`. Regexes with nested quantifiers or backreferences are rejected (ReDoS). |
| Malicious files | Only `.csv` and `.xlsx`. File-size, row, column and sheet limits. XLSX containers are checked for zip bombs and macros before parsing, and `defusedxml` protects openpyxl from XML entity attacks. Formulas are read as cached values, never evaluated. |
| Runaway agent | Two narrow tools, each sheet profiled at most twice and audited once, a step budget (`AUDIT_MAX_AGENT_STEPS`), and timeouts and retries on Bedrock calls. |
| LLM failure | Any agent error falls back to the deterministic audit, so a report is always produced, and the report says which mode it ran in. |
| Secrets | No keys in code or config. Bedrock credentials come from the standard AWS chain (profile, SSO, or IAM role). `.env` is git-ignored. |
| Data leakage | Logs record counts and sheet numbers, never cell values or sheet names (tested). `AUDIT_SHARE_SAMPLE_VALUES=false` sends the LLM only names, types, and statistics. |
| Output injection | Report text is stripped of control characters and Markdown-escaped; terminal output is stripped of ANSI escapes. |
| Data loss | The input file is opened read-only and the CLI refuses to write over it. |

## Project layout

```
src/sheet_audit/
  loader.py      safe reading of CSV/XLSX with limits
  gate.py        "is this a table?" — header detection, shaping, refusal reasons
  values.py      cell normalisation and type inference
  profiling.py   column profiles and candidate rules inferred from data
  expr.py        safe arithmetic-expression parser
  rules.py       deterministic rule evaluation and scoring
  checks.py      generic checks
  audit.py       rules + checks → findings
  session.py     audit state; the only thing the tools can touch
  report.py      Markdown rendering
  schemas.py     Pydantic models for rules, findings, and the LLM narrative
  agent/         LangChain agent, its two tools, and the system prompt
  cli.py         command-line interface
samples/make_samples.py   sample workbook with 13 planted issues + a clean control file
tests/                    unit, end-to-end, security, and CLI tests (no network needed)
```

## Testing

```bash
pytest
```

The tests don't call the LLM. They cover every rule and check, the gate, malformed and hostile files (zip bombs, renamed macro workbooks, binary disguised as CSV), the tools' limits, rejection of unsafe rules, report escaping, and log hygiene. The end-to-end test asserts that each planted issue is found **at exactly the expected rows**, and that the clean control file produces **zero findings**. That second test protects against the most common failure of anomaly detectors: finding problems that aren't there.

## Known limitations

- Formulas are read as their last saved values. A workbook saved by a tool that doesn't calculate (like openpyxl) has no cached values.
- Sheets are audited independently; cross-sheet rules (order totals matching a line-items sheet) are not supported.
- Inferred rules describe what is typical, not what is correct. If a problem affects more than about 10% of rows, the rule that would catch it isn't accepted automatically. Review the proposed contract, or supply one.
- Arithmetic discovery looks for relations between two columns (`a ± b`, `a × b`, `a ÷ b`). Relations with constants (`tax = subtotal × 0.08`) need the agent or a contract.
- Sheets with several tables stacked vertically get a warning, not a split.
- The regex safety check is a heuristic, not a proof of linear-time matching.
