import zipfile

import openpyxl
import pytest
from openpyxl.xml import DEFUSEDXML

from sheet_audit.config import Settings
from sheet_audit.loader import InputError, load_file


def test_defusedxml_is_active():
    # openpyxl only blocks XML entity-expansion attacks when defusedxml is installed.
    assert DEFUSEDXML is True


def test_loads_all_sheets_and_gates_each(samples, settings):
    tables = {t.name: t for t in load_file(samples["orders"], settings)}
    assert set(tables) == {"Orders", "Customers", "Notes"}
    assert tables["Orders"].gate.auditable and tables["Orders"].gate.n_rows == 50
    assert tables["Customers"].gate.auditable
    assert not tables["Notes"].gate.auditable
    assert tables["Notes"].gate.reasons


def test_loads_csv(samples, settings):
    [table] = load_file(samples["clean"], settings)
    assert table.gate.auditable and table.gate.n_rows == 30


def test_does_not_modify_the_input(samples, settings):
    before = samples["orders"].read_bytes()
    load_file(samples["orders"], settings)
    assert samples["orders"].read_bytes() == before


@pytest.mark.parametrize("name", ["data.xls", "data.xlsm", "data.json", "script.py"])
def test_rejects_unsupported_extensions(tmp_path, settings, name):
    path = tmp_path / name
    path.write_text("a,b\n1,2\n")
    with pytest.raises(InputError, match="unsupported file type"):
        load_file(path, settings)


def test_rejects_missing_and_empty_files(tmp_path, settings):
    with pytest.raises(InputError, match="not found"):
        load_file(tmp_path / "nope.csv", settings)
    (tmp_path / "empty.csv").write_bytes(b"")
    with pytest.raises(InputError, match="empty"):
        load_file(tmp_path / "empty.csv", settings)


def test_enforces_file_size_limit(tmp_path):
    path = tmp_path / "big.csv"
    path.write_text("a,b\n" + "1,2\n" * 1000)
    with pytest.raises(InputError, match="limit"):
        load_file(path, Settings(_env_file=None, max_file_mb=0.001))


def test_enforces_row_and_column_limits(tmp_path):
    rows = tmp_path / "rows.csv"
    rows.write_text("a,b\n" + "1,2\n" * 50)
    with pytest.raises(InputError, match="rows"):
        load_file(rows, Settings(_env_file=None, max_rows=20))
    cols = tmp_path / "cols.csv"
    cols.write_text(",".join(f"c{i}" for i in range(30)) + "\n")
    with pytest.raises(InputError, match="columns"):
        load_file(cols, Settings(_env_file=None, max_columns=10))


def test_rejects_binary_disguised_as_csv(tmp_path, settings):
    path = tmp_path / "evil.csv"
    path.write_bytes(b"MZ\x00\x00\x90binary")
    with pytest.raises(InputError, match="binary"):
        load_file(path, settings)


def test_rejects_non_zip_xlsx(tmp_path, settings):
    path = tmp_path / "fake.xlsx"
    path.write_text("a,b\n1,2\n")
    with pytest.raises(InputError, match="not a valid"):
        load_file(path, settings)


def test_rejects_macro_workbooks_even_when_renamed(tmp_path, settings):
    path = tmp_path / "renamed.xlsx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("xl/vbaProject.bin", b"\x00" * 10)
    with pytest.raises(InputError, match="macro"):
        load_file(path, settings)


def test_rejects_zip_bombs(tmp_path, settings):
    path = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("xl/worksheets/sheet1.xml", b"0" * (5 * 1024 * 1024))
    with pytest.raises(InputError, match="compression"):
        load_file(path, settings)


def test_corrupt_workbook_gives_clean_error(tmp_path, settings):
    path = tmp_path / "corrupt.xlsx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "not xml at all")
    with pytest.raises(InputError, match="could not open"):
        load_file(path, settings)


def test_reads_cached_formula_values_not_formulas(tmp_path, settings):
    path = tmp_path / "formulas.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["a", "b", "c"])
    for i in range(6):
        ws.append([i, i, f"=A{i + 2}+B{i + 2}"])
    wb.save(path)
    [table] = load_file(path, settings)
    # openpyxl writes no cached values, so formula cells read as empty, never as formula text.
    assert table.df["c"].isna().all()
