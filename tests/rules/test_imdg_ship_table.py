"""Independent spot checks transcribed from visually reviewed source PDF tables.

Pages below are one-based PDF pages in MSC.556(108), not printed Code pages.
"""

import json
from itertools import product
from pathlib import Path

import pytest

from stowage.rules.imdg.tables import ShipAlternative, containership_segregation


DATA = json.loads((Path(__file__).resolve().parents[2] /
                   "src/stowage/rules/imdg/data/segregation_7_4_3.json").read_text())
CELLS = {(c["ship_type"], c["level"], c["orientation"], c["pair"], c["deck"]): c
         for table in DATA["tables"] for c in table["cells"]}


def test_all_container_ship_cells_present():
    expected = set()
    for ship, level, pair in product(("closed_holds", "hatchless"), range(1, 5),
                                     ("closed_closed", "closed_open", "open_open")):
        expected.add((ship, level, "vertical", pair, None))
        for orientation, deck in product(("fore_aft", "athwartships"), ("on", "under")):
            expected.add((ship, level, orientation, pair, deck))
    assert set(CELLS) == expected
    assert sum(len(t["cells"]) for t in DATA["tables"]) == 120
    for cell in CELLS.values():
        assert cell["alternatives"]
        assert all(cell["source"][key] for key in ("document", "pdf_page", "table", "row", "column"))


# Quotes preserve words/cell structure, folding source line breaks and case.
@pytest.mark.parametrize("page,ship,level,orientation,pair,deck,quote", [
    (506, "closed_holds", 1, "vertical", "closed_closed", None, "One on top of the other permitted"),
    (506, "closed_holds", 1, "vertical", "closed_open", None,
     'Open on top of closed permitted otherwise as for "open versus open"'),
    (506, "closed_holds", 3, "vertical", "closed_closed", None,
     "Not in the same vertical line unless segregated by a deck"),
    (506, "closed_holds", 2, "vertical", "closed_open", None, 'As for "open versus open"'),
    (506, "closed_holds", 1, "fore_aft", "open_open", "under", "One container space or one bulkhead"),
    (506, "closed_holds", 2, "athwartships", "closed_open", "on", "One container space"),
    (506, "closed_holds", 2, "athwartships", "closed_open", "under", "Two container spaces"),
    (506, "closed_holds", 2, "athwartships", "open_open", "under", "One bulkhead"),
    (506, "closed_holds", 3, "athwartships", "open_open", "on", "Three container spaces"),
    (506, "closed_holds", 3, "fore_aft", "open_open", "on", "Two container spaces"),
    (506, "closed_holds", 3, "fore_aft", "open_open", "under", "Two bulkheads"),
    (506, "closed_holds", 4, "fore_aft", "closed_closed", "under",
     "One bulkhead and minimum horizontal distance of 24 m"),
    (506, "closed_holds", 4, "fore_aft", "closed_open", "under", "Two bulkheads"),
    (506, "closed_holds", 4, "athwartships", "closed_closed", "on", "Prohibited"),
    (507, "hatchless", 1, "vertical", "open_open", None, "Not in the same vertical line"),
    (507, "hatchless", 3, "vertical", "closed_closed", None, "Not in the same vertical line"),
    (507, "hatchless", 4, "vertical", "open_open", None, "Prohibited"),
    (507, "hatchless", 1, "athwartships", "closed_open", "under", "No restriction"),
    (507, "hatchless", 2, "athwartships", "closed_open", "on", "Two container spaces"),
    (507, "hatchless", 2, "fore_aft", "open_open", "on",
     "One container space and not in or above same hold"),
    (507, "hatchless", 2, "athwartships", "open_open", "on",
     "Two container spaces and not in or above same hold"),
    (507, "hatchless", 3, "fore_aft", "closed_closed", "on",
     "One container space and not in or above same hold"),
    (507, "hatchless", 3, "athwartships", "closed_open", "on",
     "Two container spaces and not in or above same hold"),
    (507, "hatchless", 3, "athwartships", "open_open", "on",
     "Three container spaces and not in or above same hold"),
    (507, "hatchless", 4, "fore_aft", "closed_open", "on",
     "Minimum horizontal distance of 24 m and not in or above same hold"),
    (507, "hatchless", 4, "fore_aft", "closed_closed", "under",
     "One bulkhead and minimum horizontal distance of 24 m"),
    (507, "hatchless", 4, "athwartships", "closed_open", "under", "Prohibited"),
])
def test_source_quoted_ship_cells(page, ship, level, orientation, pair, deck, quote):
    cell = CELLS[ship, level, orientation, pair, deck]
    assert cell["text"].casefold() == quote.casefold()
    assert cell["source"]["pdf_page"] == page
    assert cell["source"]["table"] == ("7.4.3.2" if page == 506 else "7.4.3.3")
    expected = SOURCE_ALTERNATIVES[quote]
    assert cell["alternatives"] == expected
    loaded = containership_segregation(level, ship_type=ship, orientation=orientation,
                                       container_pair=pair, deck=deck)
    assert loaded.alternatives == tuple(ShipAlternative(**option) for option in expected)


# Independent executable transcription of the quoted cells above, not a parser
# of their descriptive text or an import from the transcription/build script.
# MSC.556(108) PDF pp506-507, tables 7.4.3.2/.3: OR separates alternatives;
# fields in a dictionary are simultaneous (AND) requirements. Existing shared
# 6 m footer fields are retained; their scope remains an unresolved source caveat.
DECK_BARRIER = {"segregated_by_deck": True, "barriers_resistant_to_fire_and_liquids": True}
ONE_BULKHEAD = {"min_bulkheads": 1, "min_distance_from_intervening_bulkhead_m": 6,
                "barriers_resistant_to_fire_and_liquids": True}
TWO_BULKHEADS = {**ONE_BULKHEAD, "min_bulkheads": 2}
SOURCE_ALTERNATIVES = {
    "One on top of the other permitted": [{}],
    'Open on top of closed permitted otherwise as for "open versus open"': [
        {"open_above_closed": True}, {"not_same_vertical_line": True}, DECK_BARRIER],
    "Not in the same vertical line unless segregated by a deck": [
        {"not_same_vertical_line": True}, DECK_BARRIER],
    'As for "open versus open"': [{"not_same_vertical_line": True}, DECK_BARRIER],
    "One container space or one bulkhead": [{"min_container_spaces": 1}, ONE_BULKHEAD],
    "One container space": [{"min_container_spaces": 1}],
    "Two container spaces": [{"min_container_spaces": 2}],
    "Three container spaces": [{"min_container_spaces": 3}],
    "One bulkhead": [ONE_BULKHEAD],
    "Two bulkheads": [TWO_BULKHEADS],
    "One bulkhead and minimum horizontal distance of 24 m": [
        {**ONE_BULKHEAD, "min_horizontal_m": 24}],
    "Prohibited": [{"prohibited": True}],
    "Not in the same vertical line": [{"not_same_vertical_line": True}],
    "No restriction": [{}],
    "One container space and not in or above same hold": [
        {"min_container_spaces": 1, "not_in_or_above_same_hold": True}],
    "Two container spaces and not in or above same hold": [
        {"min_container_spaces": 2, "not_in_or_above_same_hold": True}],
    "Three container spaces and not in or above same hold": [
        {"min_container_spaces": 3, "not_in_or_above_same_hold": True}],
    "Minimum horizontal distance of 24 m and not in or above same hold": [
        {"min_horizontal_m": 24, "not_in_or_above_same_hold": True}],
}


def test_bulkheads_decks_and_space_definition():
    # Both PDF p506 and p507 table footers: "Containers not less than 6 m
    # from intervening bulkhead." Note: "All bulkheads and decks shall be
    # resistant to fire and liquids."
    for cell in CELLS.values():
        for condition in cell["alternatives"]:
            if "min_bulkheads" in condition:
                assert condition["min_distance_from_intervening_bulkhead_m"] == 6
                assert condition["barriers_resistant_to_fire_and_liquids"] is True
            if condition.get("segregated_by_deck"):
                assert condition["barriers_resistant_to_fire_and_liquids"] is True
    # PDF p505, 7.4.3.1.1: "not less than 6 m fore and aft" /
    # "not less than 2.4 m athwartships".
    assert DATA["definitions"]["container_space_fore_aft_m"] == 6
    assert DATA["definitions"]["container_space_athwartships_m"] == 2.4


def test_vertical_exception_and_or_conditions_remain_distinct():
    alternatives = CELLS["closed_holds", 1, "vertical", "closed_open", None]["alternatives"]
    assert alternatives[0] == {"open_above_closed": True}
    assert alternatives[1] == {"not_same_vertical_line": True}
    assert alternatives[2]["segregated_by_deck"] is True
    assert CELLS["hatchless", 1, "vertical", "open_open", None]["alternatives"] == [
        {"not_same_vertical_line": True}]
    alternatives = CELLS["closed_holds", 2, "fore_aft", "closed_open", "under"]["alternatives"]
    assert len(alternatives) == 2
    assert alternatives[0] == {"min_container_spaces": 1}
    assert alternatives[1]["min_bulkheads"] == 1
