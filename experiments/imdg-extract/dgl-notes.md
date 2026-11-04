# DGL sample, IMDG 42-24

`dgl_build.py` reproducibly writes `src/stowage/rules/imdg/data/dgl.json` from
53 manually transcribed IMDG rows (41 UN numbers) and the downloaded US HMT XML.
This count covers all 20 class/division keys, PG I/II/III and unassigned PG,
all common UN numbers named in task 1.3, explosives compatibility groups,
multiple subsidiary hazards, special segregation and conditional stowage.
It is a deliberate sample, not a complete shipping classification database.

The data names match the catalogue:

- `imdg_42_24.dangerous_goods_list_3_2`
- `imdg_42_24.stowage_and_handling_provisions`

Every row retains document, one-based PDF page, DGL row (UN + PG), and columns
1/2/3/4/5/6/16a/16b. There are fourteen SW/H code definitions, with their own
7.1.5/7.1.6 row/page pointers. Special provisions preserve variant-specific
column 6, including differing PG III provisions. The `P` marine-pollutant marker
in column 4 is not a subsidiary hazard and is not included in the requested
subsidiary-risk list. Conditional column 4 `See SP290` is preserved separately;
its empty fixed-risk tuple does not mean the cargo never has subsidiary hazards.

## Visual source verification

On 2026-10-27 the full pages below were rasterized using read-only macOS
CoreGraphics through `dgl_render.py`, then visually inspected against every
sampled row's name, class, subsidiary risk, PG, special provisions, columns
16a/16b and row boundaries. No layout interpretation discrepancies remained.
PDF pages: 581, 590, 596, 598, 600, 602, 607, 610, 614, 616, 617, 619, 620, 635, 639,
642, 647, 652, 653, 660, 693, 694, 698, 699, 709, 712, 717, 736, 746.
Stowage and handling code definitions: PDF pages 484–485.

Reproduce a render (files go to `/private/tmp/dgl-page-N.png`):

```sh
UV_CACHE_DIR=/private/tmp/stowage-data-uv-cache uv run python experiments/imdg-extract/dgl_render.py data/raw/imdg-42-24/msc556-108.pdf 639 746
```

`dgl_inspect.py` is a discovery aid for broken-word extracted text, not a
validated parser. The neighbouring raw PDF remains the authority. Native
Swift/Objective-C compile attempts failed because the installed compiler and SDK
do not match; the working ctypes renderer needs no added package or compilation.

## Cross-check and differences

Each of the 53 rows has a recorded US comparison. HMT column 3 (class), 5 (PG),
6 (labels, a subsidiary-risk cross-check) and 10A (vessel stowage category) were
compared. Every US row for that UN is preserved with its one-based XML `TR`
index; continuation rows inherit their name/UN/class from the preceding explicit
row. Tests independently check retained rows against the original XML.

- **UN1090 acetone, PG II:** IMDG PDF602 column 16a is category **E**; the US
  2026-10-23 snapshot column 10A is **B**. The data keeps **E**: IMDG 42-24 wins.
- **UN3373 biological substance, category B:** IMDG PDF736 column 16a is base
  category **C**, with **SW2/SW18**. SW18 (PDF485) permits category **A** when
  transported in accordance with **P650**. The US HMT gives base category **A**,
  vessel provision **40**. The data keeps IMDG **C + SW18**; the condition must
  be evaluated, never discarded.
- **UN1005 ammonia:** the US HMT has both a domestic class 2.2 row and a class
  2.3 corrosive row; IMDG PDF598 is **2.3, subsidiary 8**. This is recorded as a
  comparable match because the latter US variant matches. It does not select the
  domestic 2.2 classification for IMDG use.
- All other sampled rows have a comparable US class/PG/label/category match.
  US names may split alternatives or use US spelling; their exact strings are
  retained rather than compared as byte-identical IMDG names.

US vessel provision numbers are preserved as US-only text; they are not mapped
to IMDG SG/SGG/SW codes. US labels are not a complete substitute for IMDG column
4, and SP290/SP172-dependent hazards are not resolved by this cross-check.

## APIs and bounds

`dgl.py` exposes frozen typed entries, immutable cached catalogue mappings,
`lookup_dgl(un, pg)`, `entries_for_un(un)` and `entries_by_class(class)`.
The indexes use dictionaries; there is no runtime PDF/XML parsing.
A multivariant UN without PG raises `ValueError`; unsampled exact rows raise
`KeyError`. None means PG unassigned, not permission to guess a packing group.

SW/H definitions are plain-language faithful paraphrases with source pointers.
Conditional rules (e.g. SW16 open-unit loads, SW19 damaged/waste batteries),
SP290/SP172 hazards and the external provisions referenced by codes must be evaluated
by the checker with shipment/voyage facts. This task implements data, not that
future evaluator. There is no physics or regulatory approximation here.

The resolution source is the adopted 42-24 text; later corrigenda have not been
verified. The US comparison is the downloaded 2026-10-23 snapshot, not an assertion
of edition equivalence. No downloads or raw source files were modified.

Verification:

```sh
UV_CACHE_DIR=/private/tmp/stowage-data-uv-cache PYTHONPATH=src uv run pytest tests/rules/test_imdg_dgl.py -q -p no:cacheprovider
```

70 tests passed, including all 53 independent source spot checks and 13 additional code-cell checks and an XML
reconciliation covering every retained US comparison row. All work was single
core, used no GPU, and took under one second per render/build/test command;
there were no heavy compute jobs or reservations.

## Generator UN follow-up

Added UN0336, UN1978, UN1333, UN1361, UN1402, UN3109, UN2810, UN3373 and
UN2915, including all their IMDG 42-24 packing-group rows: 13 additional rows
for nine UN numbers. No distinct rows share the same UN/PG key. The full sample
now contains **53 rows / 41 UN numbers / 14 SW/H definitions**. SW18, SW20,
SW21, SW27 and H2 were added from PDF485. All source codes remain unchanged.

The follow-up visually checked the added source rows on pages 590, 614, 616,
619, 652, 694, 699, 712 and 736, using the same renderer. Previously rendered
pages were reused; new pages 616, 619, 652 and 736 were rendered separately.

UN0336 is genuinely class 1.4G, stowage category **02**, per PDF590. An incomplete
class-1 stowage implementation must fail closed for this category. UN2915
retains **SW20/SW21** and column 4 **See SP172** from PDF699. SW20 and SW21
are conditional category overrides for specific radioactive contents; they do
not encode radiation protection under **7.1.4.5**. The runtime adapter therefore
adds a separate, source-traced external-verification requirement for that
section. Missing radiation verification, unresolved subsidiary hazards or
required SW conditions must fail closed. `source_stowage_codes` exposes the
authentic column 16a cells while runtime `stowage_codes` can also include the
checker guard. The raw JSON remains an authentic transcription.
