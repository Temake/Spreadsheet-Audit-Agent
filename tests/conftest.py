import pytest
from make_samples import write_samples

from sheet_audit.config import Settings
from sheet_audit.gate import prepare_sheet


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """Keep a developer's real .env (and Bedrock key) out of every test."""
    monkeypatch.setattr("sheet_audit.cli.load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr("sheet_audit.cli.open_report", lambda path: None)
    monkeypatch.setattr("sheet_audit.config.Settings.model_config", {**Settings.model_config, "env_file": None})


@pytest.fixture
def settings() -> Settings:
    # _env_file=None keeps a developer's local .env out of the tests.
    return Settings(_env_file=None, bedrock_model_id=None)


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    return write_samples(tmp_path_factory.mktemp("samples"))


@pytest.fixture
def make_table(settings):
    """Build a SheetTable from a header and data rows, as if loaded from a file."""

    def _make(header: list, rows: list[list], name: str = "Sheet1"):
        return prepare_sheet(name, [header, *rows], settings)

    return _make
