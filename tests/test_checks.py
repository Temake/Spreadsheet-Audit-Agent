from sheet_audit import checks


def test_duplicate_rows(make_table, settings):
    t = make_table(["a", "b"], [[1, "x"], [2, "y"], [1, "x"], [3, "z"], [4, "w"]])
    [f] = checks.duplicate_rows(t, settings)
    assert f.rows == [2, 4] and f.confidence == "confirmed"


def test_formula_errors(make_table, settings):
    t = make_table(["a", "b"], [[1, "#REF!"], [2, 3], [3, 4], [4, "#DIV/0!"], [5, 6]])
    [f] = checks.formula_errors(t, settings)
    assert f.rows == [2, 5] and f.severity == "high"


def test_invalid_types_ignore_formula_errors(make_table, settings):
    rows = [[i, i * 1.5] for i in range(18)] + [[18, "n/a?"], [19, "#REF!"]]
    t = make_table(["id", "amount"], rows)
    [f] = checks.invalid_types(t, settings)
    assert f.columns == ["amount"] and f.rows == [20]


def test_no_invalid_type_finding_for_text_columns(make_table, settings):
    t = make_table(["id", "label"], [[i, f"item {i}"] for i in range(10)])
    assert checks.invalid_types(t, settings) == []


def test_missing_values_skip_mostly_empty_and_contract_columns(make_table, settings):
    rows = [[i, None if i == 3 else "v", None if i % 2 else "n"] for i in range(20)]
    t = make_table(["id", "sparse_gap", "half_empty"], rows)
    found = checks.missing_values(t, settings, skip=set())
    assert [f.columns for f in found] == [["sparse_gap"]]
    assert checks.missing_values(t, settings, skip={"sparse_gap"}) == []


def test_inconsistent_variants(make_table, settings):
    rows = [[i, "North"] for i in range(8)] + [[8, "north"], [9, " North"]]
    t = make_table(["id", "region"], rows)
    [f] = checks.inconsistent_variants(t, settings)
    assert f.rows == [10, 11]
    assert f.evidence[1].values["exact_value"] == "' North'"  # the stray space stays visible


def test_outliers_use_robust_statistics(make_table, settings):
    rows = [[i, 10 + (i % 5)] for i in range(30)] + [[30, 500]]
    t = make_table(["id", "price"], rows)
    [f] = checks.outliers(t, settings)
    assert f.rows == [32] and f.confidence == "possible"


def test_no_outliers_in_ordinary_spread(make_table, settings):
    t = make_table(["id", "price"], [[i, 10 + (i % 7) * 3] for i in range(40)])
    assert checks.outliers(t, settings) == []
