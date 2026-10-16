import yaml

from sheet_audit.cli import main


def test_deterministic_run_writes_report_and_contract(samples, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = samples["orders"].read_bytes()
    code = main([str(samples["orders"]), "--no-llm", "--auto", "-o", "report.md", "--save-contract", "contract.yaml"])
    assert code == 0
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "# Spreadsheet Audit Report" in text
    assert "## Sheet: Notes" in text and "Not audited" in text
    assert samples["orders"].read_bytes() == before  # original untouched

    contract = yaml.safe_load((tmp_path / "contract.yaml").read_text(encoding="utf-8"))
    assert "Orders" in contract["sheets"]


def test_saved_contract_can_be_reused_and_confirms_findings(samples, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main([str(samples["orders"]), "--no-llm", "--auto", "-o", "a.md", "--save-contract", "c.yaml"])
    assert main([str(samples["orders"]), "--no-llm", "--contract", "c.yaml", "-o", "b.md"]) == 0
    assert "confirmed" in (tmp_path / "b.md").read_text(encoding="utf-8")


def test_refuses_to_overwrite_the_input(samples, capsys):
    assert main([str(samples["orders"]), "--no-llm", "--auto", "-o", str(samples["orders"])]) == 2
    assert "never overwritten" in capsys.readouterr().err


def test_refuses_to_overwrite_existing_report_without_force(samples, tmp_path):
    out = tmp_path / "r.md"
    out.write_text("keep me")
    assert main([str(samples["clean"]), "--no-llm", "--auto", "-o", str(out)]) == 2
    assert out.read_text() == "keep me"
    assert main([str(samples["clean"]), "--no-llm", "--auto", "-o", str(out), "--force"]) == 0


def test_bad_input_exits_cleanly(tmp_path, capsys):
    bad = tmp_path / "x.exe"
    bad.write_bytes(b"MZ")
    assert main([str(bad), "--no-llm", "--auto", "-o", str(tmp_path / "r.md")]) == 2
    assert "unsupported file type" in capsys.readouterr().err


def test_report_opens_only_in_a_terminal_and_not_with_no_open(samples, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("sheet_audit.cli.open_report", opened.append)
    args = [str(samples["clean"]), "--no-llm", "--auto", "--force", "-o", str(tmp_path / "r.md")]

    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    main(args)
    assert opened == []  # scripts and CI: never

    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    main([*args, "--no-open"])
    assert opened == []
    main(args)
    assert opened == [tmp_path / "r.md"]


def test_non_interactive_without_auto_is_refused(samples, tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main([str(samples["clean"]), "--no-llm", "-o", str(tmp_path / "r.md")]) == 2


def test_falls_back_to_deterministic_when_llm_is_not_configured(samples, tmp_path, monkeypatch):
    monkeypatch.delenv("AUDIT_BEDROCK_MODEL_ID", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    assert main([str(samples["orders"]), "--auto", "-o", "r.md"]) == 0
    assert "agent failed" in (tmp_path / "r.md").read_text(encoding="utf-8")
