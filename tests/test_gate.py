from sheet_audit.gate import prepare_sheet

ROWS = [[i, f"name{i}", i * 2] for i in range(6)]


def test_detects_header_below_title_rows(settings):
    table = prepare_sheet("S", [["Quarterly sales report"], [None], ["id", "name", "value"], *ROWS], settings)
    assert table.gate.auditable
    assert table.gate.header_row == 3
    assert list(table.df.columns) == ["id", "name", "value"]
    assert table.df.index[0] + 1 == 4  # first data row is spreadsheet row 4
    assert any("above the header" in w for w in table.gate.warnings)


def test_numeric_only_sheet_has_no_header(settings):
    table = prepare_sheet("S", [[1, 2, 3]] * 10, settings)
    assert not table.gate.auditable
    assert "no header row" in table.gate.reasons[0]


def test_empty_and_tiny_sheets_are_refused(settings):
    assert not prepare_sheet("S", [], settings).gate.auditable
    assert not prepare_sheet("S", [[None, None]], settings).gate.auditable
    tiny = prepare_sheet("S", [["a", "b"], [1, 2]], settings)
    assert not tiny.gate.auditable and "data row" in tiny.gate.reasons[0]


def test_single_column_is_refused(settings):
    table = prepare_sheet("S", [["a"], *[[i] for i in range(10)]], settings)
    assert not table.gate.auditable and "column" in table.gate.reasons[0]


def test_duplicate_and_missing_headers_are_renamed_with_warnings(settings):
    table = prepare_sheet("S", [["a", "a", None], *[[1, 2, 3]] * 6], settings)
    assert list(table.df.columns) == ["a", "a_2", "column_C"]
    assert len(table.gate.warnings) == 2


def test_interior_blank_rows_are_dropped_with_warning(settings):
    table = prepare_sheet("S", [["id", "name", "value"], *ROWS[:3], [None, None, None], *ROWS[3:]], settings)
    assert table.gate.n_rows == 6
    assert any("blank row" in w for w in table.gate.warnings)


def test_control_characters_are_stripped_from_names(settings):
    table = prepare_sheet("Sheet\x1b[31m", [["id\x07", "name", "value"], *ROWS], settings)
    assert table.name == "Sheet[31m"
    assert "id" in table.df.columns
