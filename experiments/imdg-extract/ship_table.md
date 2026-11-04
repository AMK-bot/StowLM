# Container-ship segregation transcription

Source: local public IMO resolution MSC.556(108), Amendment 42-24 PDF,
one-based PDF pages 505 (definitions), 506 (7.4.3.2, closed cargo holds),
507 (7.4.3.3, hatchless ships). Text extraction alone loses merged-cell bounds.
Both table pages were rendered at 3x with PyMuPDF and visually inspected,
including vertical merges and both footers. Rendering took under one second,
one CPU process, no GPU; no heavy compute reservation was needed.

`build_ship_table.py` contains a reproducible manual transcription; it writes
`src/stowage/rules/imdg/data/segregation_7_4_3.json`. No PDF package or PDF
parsing is required by consumers. The table name matches catalogue.toml.
Each table has 60 expanded cells: 4 levels x 3 vertical container pairs plus
4 levels x 2 horizontal directions x 3 container pairs x 2 deck positions.
Merged vertical cells are expanded with each logical row/column source pointer.

The cell `text` folds source line breaks and capitalization; quotation marks
around referenced open/open cells are normalized to ASCII. `alternatives` is
OR; fields within an alternative are AND. `{}` means no restriction from this
cell. For closed/open vertical level 1, an open container above a closed one
is permitted; otherwise open/open rules apply. Vertical level 2 and 3
closed/open cells inherit open/open requirements. Hatchless ships have no
deck-separation exception. Prohibition is explicit, never represented as zero
distance. Table 7.4.3.3 adds same-hold restrictions for specified on-deck cells;
its closed/open on-deck level 2 athwartships value is **two** spaces, unlike
the **one** space in the closed-hold table.

Both table footers state: "Containers not less than 6 m from intervening
bulkhead." The JSON preserves this as a source-traced table note and places
`min_distance_from_intervening_bulkhead_m: 6` on bulkhead alternatives.
It applies when an intervening bulkhead provides segregation; it is a distance
from each container to that bulkhead, not merely container-to-container
distance. All qualifying decks and bulkheads must resist fire and liquids.

These are exact table conditions, not a stand-alone compliance verdict.
Application also needs chapter 7.4.2.2: weather-tightness, effective gutterbars,
clear gaps and sensitive vertical lines for on/under-deck interactions.
Ship geometry must provide these facts. Do not assume that different decks
automatically separate incompatible goods or that the same-hold predicate can
be inferred from bay distance. The checker must also evaluate DGL special
provisions and chapter 7.2 precedence outside these tables.

Tests independently quote 26 cells with PDF page numbers, check all 120
logical cells, source pointers, definitions and the footers. No approximation
has been introduced.
