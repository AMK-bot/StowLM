"""Encode the visually checked IMDG 42-24 table on PDF page 487.

Run from the repository root with ``uv run python
experiments/imdg-extract/build_class_table.py``. No PDF is parsed at runtime.
"""

import json
from pathlib import Path


CLASSES = (
    "1.1/1.2/1.5", "1.3/1.6", "1.4", "2.1", "2.2", "2.3", "3",
    "4.1", "4.2", "4.3", "5.1", "5.2", "6.1", "6.2", "7", "8", "9",
)

# Row order and column order both follow the printed table, PDF page 487.
ROWS = (
    "* * * 4 2 2 4 4 4 4 4 4 2 4 2 4 X",
    "* * * 4 2 2 4 3 3 4 4 4 2 4 2 2 X",
    "* * * 2 1 1 2 2 2 2 2 2 X 4 2 2 X",
    "4 4 2 X X X 2 1 2 2 2 2 X 4 2 1 X",
    "2 2 1 X X X 1 X 1 X X 1 X 2 1 X X",
    "2 2 1 X X X 2 X 2 X X 2 X 2 1 X X",
    "4 4 2 2 1 2 X X 2 2 2 2 X 3 2 X X",
    "4 3 2 1 X X X X 1 X 1 2 X 3 2 1 X",
    "4 3 2 2 1 2 2 1 X 1 2 2 1 3 2 1 X",
    "4 4 2 2 X X 2 X 1 X 2 2 X 2 2 1 X",
    "4 4 2 2 X X 2 1 2 2 X 2 1 3 1 2 X",
    "4 4 2 2 1 2 2 2 2 2 2 X 1 3 2 2 X",
    "2 2 X X X X X X 1 X 1 1 X 1 X X X",
    "4 4 4 4 2 2 3 3 3 2 3 3 1 X 3 3 X",
    "2 2 2 2 1 1 2 2 2 2 1 2 X 3 X 2 X",
    "4 2 2 1 X X X 1 1 1 2 2 X 3 2 X X",
    "X X X X X X X X X X X X X X X X X",
)


def main() -> None:
    cells = []
    for row, values in zip(CLASSES, ROWS, strict=True):
        for column, value in zip(CLASSES, values.split(), strict=True):
            cells.append({
                "row_class": row,
                "column_class": column,
                "value": value,
                "source": {
                    "document": "IMO MSC.556(108), IMDG Code Amendment 42-24",
                    "pdf_page": 487,
                    "table": "7.2.4",
                    "row": row,
                    "column": column,
                },
            })
    data = {
        "name": "imdg_42_24.general_segregation_7_2_4",
        "amendment": "42-24",
        "classes": list(CLASSES),
        "meanings": {
            "1": "away from",
            "2": "separated from",
            "3": "separated by a complete compartment or hold from",
            "4": "separated longitudinally by an intervening complete compartment or hold from",
            "X": "Consult the Dangerous Goods List for specific segregation provisions; not an unconditional clearance.",
            "*": "Apply 7.2.7.1 segregation provisions between class 1 substances or articles.",
        },
        "qualifications": [
            {"clause": "7.2.3.1", "pdf_page": 486, "text": "Specific Dangerous Goods List column 16b provisions take precedence over conflicting general provisions."},
            {"clause": "7.2.3.3", "pdf_page": 487, "text": "Apply a single subsidiary hazard's provisions where more stringent; a class 1 subsidiary hazard uses division 1.3."},
            {"clause": "7.2.3.4", "pdf_page": 487, "text": "For two or more subsidiary hazards use Dangerous Goods List column 16b."},
            {"clause": "7.2.4", "pdf_page": 487, "text": "Always consult the Dangerous Goods List for particular segregation provisions."},
        ],
        "cells": cells,
    }
    target = Path("src/stowage/rules/imdg/data/segregation_7_2_4.json")
    target.write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    main()
