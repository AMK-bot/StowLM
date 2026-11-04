# IMDG 42-24 general segregation matrix

Source: public IMO resolution MSC.556(108), downloaded snapshot under
`data/raw/imdg-42-24/`; table 7.2.4 is on **1-based PDF page 487**.
The printed page was rendered with PyMuPDF and visually checked in full against
the extracted text. `build_class_table.py` records the 17 printed row vectors;
the generated JSON contains all **289** row/column cells, including the mirrored
half, with an explicit source pointer on every cell.

The runtime data name matches catalogue entry `imdg.segregation.7_2_4`:
`imdg_42_24.general_segregation_7_2_4`. Class groups retain the printed grouping:
`1.1/1.2/1.5` and `1.3/1.6`. Divisions are not collapsed across those groups.

`X` requires consulting the DGL; it is not proof that a pair is unrestricted.
`*` refers to class 1 compatibility provisions in 7.2.7.1. Specific DGL
requirements prevail (7.2.3.1, PDF page 486). A single subsidiary hazard applies
when stricter, with subsidiary class 1 treated as 1.3 (7.2.3.3, PDF page 487).
Two or more subsidiary hazards require the DGL column 16b provisions
(7.2.3.4, PDF page 487). The table is data, not a complete DG decision procedure.

Tests include 27 independently selected source cell spot checks spanning every
printed row, every symbol, both grouped explosive divisions, and the particularly
easy-to-misread class 3 / class 2.3 cell. Integrity checks require the complete
square, unique coordinates, mirrored equality, and page/table/row/column
provenance on every cell. No approximation was made.

Rebuild: `UV_CACHE_DIR=$PWD/.uv-cache uv run --no-sync python experiments/imdg-extract/build_class_table.py`.

Verify: `UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=src uv run pytest tests/rules/test_imdg_class_table.py -q -p no:cacheprovider`.
