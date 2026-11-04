"""Reproducible manual transcription of IMDG 42-24 PDF pp506-507.

This script reads no PDF at runtime: the literals below were visually verified.
Run with ``uv run python experiments/imdg-extract/build_ship_table.py``.
"""

import json
from pathlib import Path


DOCUMENT = "IMO MSC.556(108), IMDG Code Amendment 42-24"
PAIRS = ("closed_closed", "closed_open", "open_open")
LEVELS = {
    1: "Away from",
    2: "Separated from",
    3: "Separated by a complete compartment or hold from",
    4: "Separated longitudinally by an intervening complete compartment or hold from",
}
# Horizontal rows: closed/closed on, under; closed/open on, under; open/open on, under.
# s = container spaces; b = bulkheads; | = alternative; + = conjunction;
# h = not in or above same hold; m = horizontal distance metres; p = prohibited.
HORIZONTAL = {
    "closed_holds": {
        (1, "fore_aft"): ["0", "0", "0", "0", "s1", "s1|b1"],
        (1, "athwartships"): ["0", "0", "0", "0", "s1", "s1"],
        (2, "fore_aft"): ["s1", "s1|b1", "s1", "s1|b1", "s1", "b1"],
        (2, "athwartships"): ["s1", "s1", "s1", "s2", "s2", "b1"],
        (3, "fore_aft"): ["s1", "b1", "s1", "b1", "s2", "b2"],
        (3, "athwartships"): ["s2", "b1", "s2", "b1", "s3", "b2"],
        (4, "fore_aft"): ["m24", "b1+m24", "m24", "b2", "m24", "b2"],
        (4, "athwartships"): ["p"] * 6,
    },
    "hatchless": {
        (1, "fore_aft"): ["0", "0", "0", "0", "s1", "s1|b1"],
        (1, "athwartships"): ["0", "0", "0", "0", "s1", "s1"],
        (2, "fore_aft"): ["s1", "s1|b1", "s1", "s1|b1", "s1+h", "b1"],
        (2, "athwartships"): ["s1", "s1", "s2", "s2", "s2+h", "b1"],
        (3, "fore_aft"): ["s1+h", "b1", "s1+h", "b1", "s2+h", "b2"],
        (3, "athwartships"): ["s2+h", "b1", "s2+h", "b1", "s3+h", "b2"],
        (4, "fore_aft"): ["m24+h", "b1+m24", "m24+h", "b2", "m24+h", "b2"],
        (4, "athwartships"): ["p"] * 6,
    },
}


def horizontal(code):
    words = {1: "One", 2: "Two", 3: "Three"}
    alternatives, texts = [], []
    for option in code.split("|"):
        condition, terms = {}, []
        for term in option.split("+"):
            if term == "0":
                terms.append("No restriction")
            elif term == "p":
                terms.append("Prohibited")
                condition["prohibited"] = True
            elif term == "h":
                terms.append("not in or above same hold")
                condition["not_in_or_above_same_hold"] = True
            elif term.startswith("s"):
                n = int(term[1:])
                terms.append(f"{words[n]} container space" + ("s" if n > 1 else ""))
                condition["min_container_spaces"] = n
            elif term.startswith("b"):
                n = int(term[1:])
                terms.append(f"{words[n]} bulkhead" + ("s" if n > 1 else ""))
                condition.update(min_bulkheads=n, min_distance_from_intervening_bulkhead_m=6,
                                 barriers_resistant_to_fire_and_liquids=True)
            elif term.startswith("m"):
                n = int(term[1:])
                terms.append(f"Minimum horizontal distance of {n} m")
                condition["min_horizontal_m"] = n
        texts.append(" and ".join([terms[0]] + [s[0].lower() + s[1:] for s in terms[1:]]))
        alternatives.append(condition)
    return " or ".join([texts[0]] + [s[0].lower() + s[1:] for s in texts[1:]]), alternatives


def vertical(ship, level, pair):
    if level == 4:
        return "Prohibited", [{"prohibited": True}]
    if level == 1 and pair == "closed_closed":
        return "One on top of the other permitted", [{}]
    text = "Not in the same vertical line"
    alternatives = [{"not_same_vertical_line": True}]
    if ship == "closed_holds":
        text += " unless segregated by a deck"
        alternatives.append({"segregated_by_deck": True,
                             "barriers_resistant_to_fire_and_liquids": True})
    if pair == "closed_open":
        if level == 1:
            text = 'Open on top of closed permitted otherwise as for "open versus open"'
            alternatives.insert(0, {"open_above_closed": True})
        else:
            text = 'As for "open versus open"'
    return text, alternatives


def main():
    tables = []
    for ship, clause, page in [("closed_holds", "7.4.3.2", 506), ("hatchless", "7.4.3.3", 507)]:
        cells = []
        for level in LEVELS:
            for orientation in ("vertical", "fore_aft", "athwartships"):
                for pair_index, pair in enumerate(PAIRS):
                    for deck_index, deck in enumerate([None] if orientation == "vertical" else ["on", "under"]):
                        text, alternatives = (vertical(ship, level, pair) if orientation == "vertical" else
                                              horizontal(HORIZONTAL[ship][level, orientation][pair_index * 2 + deck_index]))
                        cells.append({"ship_type": ship, "level": level, "orientation": orientation,
                                      "pair": pair, "deck": deck, "text": text,
                                      "alternatives": alternatives,
                                      "source": {"document": DOCUMENT, "pdf_page": page, "table": clause,
                                                 "row": f'.{level} {LEVELS[level]}; {orientation}',
                                                 "column": f'{pair}; {deck or "all deck positions"}'}})
        tables.append({"id": clause, "ship_type": ship, "cells": cells,
                       "notes": [
                           {"text": "Containers not less than 6 m from intervening bulkhead.",
                            "source": {"document": DOCUMENT, "pdf_page": page, "table": clause,
                                       "row": "footer", "column": "all"}},
                           {"text": "All bulkheads and decks shall be resistant to fire and liquids.",
                            "source": {"document": DOCUMENT, "pdf_page": page, "table": clause,
                                       "row": "Note", "column": "all"}}]})
    payload = {"name": "imdg_42_24.containership_segregation_7_4_3", "tables": tables,
               "definitions": {"container_space_fore_aft_m": 6, "container_space_athwartships_m": 2.4,
                               "source": {"document": DOCUMENT, "pdf_page": 505, "table": "7.4.3.1.1",
                                          "row": "definition", "column": "container space"}},
               "semantics": "Alternatives are OR; conditions within each alternative are AND. Empty condition means no restriction in this table cell. Horizontal and vertical constraints must both be considered; this data is not a complete IMDG compliance decision. Mixed on/under-deck geometry and sensitive vertical lines also need 7.4.2.2 application."}
    target = Path("src/stowage/rules/imdg/data/segregation_7_4_3.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n")


if __name__ == "__main__":
    main()
