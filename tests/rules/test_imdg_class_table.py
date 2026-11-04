"""Source checks against IMO MSC.556(108), PDF page 487, table 7.2.4.

The expected values below were read from the printed table, independently of
the runtime loader. Row/column references quote the table's numeric class labels.
"""

import json
from pathlib import Path

import pytest


DATA = Path(__file__).resolve().parents[2] / "src/stowage/rules/imdg/data/segregation_7_2_4.json"


@pytest.fixture(scope="module")
def table():
    return json.loads(DATA.read_text())


def test_complete_class_matrix_and_provenance(table):
    classes = table["classes"]
    assert len(classes) == len(set(classes)) == 17
    cells = table["cells"]
    assert len(cells) == 17 * 17
    indexed = {(c["row_class"], c["column_class"]): c for c in cells}
    assert set(indexed) == {(a, b) for a in classes for b in classes}
    for (row, column), cell in indexed.items():
        assert cell["value"] in {"1", "2", "3", "4", "X", "*"}
        source = cell["source"]
        assert source["pdf_page"] == 487
        assert source["table"] == "7.2.4"
        assert source["row"] == row
        assert source["column"] == column
        assert "MSC.556(108)" in source["document"]
        assert cell["value"] == indexed[column, row]["value"]


@pytest.mark.parametrize(
    "row,column,quoted_cell",
    [
        # All quotes: PDF page 487, table 7.2.4, row/column labels as shown.
        ("1.1/1.2/1.5", "1.3/1.6", "*"),
        ("1.1/1.2/1.5", "2.1", "4"),
        ("1.1/1.2/1.5", "6.1", "2"),
        ("1.3/1.6", "4.1", "3"),
        ("1.3/1.6", "8", "2"),
        ("1.4", "2.2", "1"),
        ("1.4", "6.1", "X"),
        ("1.4", "6.2", "4"),
        ("2.1", "4.1", "1"),
        ("2.1", "6.2", "4"),
        ("2.2", "5.1", "X"),
        ("2.2", "5.2", "1"),
        ("2.3", "3", "2"),
        ("2.3", "7", "1"),
        ("3", "4.1", "X"),
        ("3", "6.2", "3"),
        ("4.1", "4.2", "1"),
        ("4.1", "5.2", "2"),
        ("4.2", "6.1", "1"),
        ("4.3", "6.2", "2"),
        ("5.1", "7", "1"),
        ("5.2", "6.2", "3"),
        ("6.1", "6.2", "1"),
        ("6.2", "8", "3"),
        ("7", "7", "X"),
        ("8", "3", "X"),
        ("9", "9", "X"),
    ],
)
def test_quoted_source_cells_pdf_page_487(table, row, column, quoted_cell):
    indexed = {(c["row_class"], c["column_class"]): c for c in table["cells"]}
    assert indexed[row, column]["value"] == quoted_cell


def test_special_symbols_are_not_numeric_clearance(table):
    assert "Dangerous Goods List" in table["meanings"]["X"]
    assert "7.2.7.1" in table["meanings"]["*"]
    assert table["name"] == "imdg_42_24.general_segregation_7_2_4"
