"""Traceable IMDG 42-24 data for segregation lookups (no runtime PDF parsing).

The four segregation terms increase protection: (1) ``away from`` keeps goods
apart, (2) ``separated from`` requires greater separation, (3) ``separated by a
complete compartment or hold from`` adds compartment separation, and (4)
``separated longitudinally by an intervening complete compartment or hold from``
requires fore-and-aft separation. On container ships their precise implementation
is table 7.4.3.2 (closed holds) or 7.4.3.3 (hatchless), not a universal distance.
A container space is at least 6 m fore-and-aft or 2.4 m athwartships (7.4.3.1.1).

These are table lookups, not a complete dangerous-goods clearance checker. ``X``
requires consulting the DGL; ``*`` requires class-1 compatibility rules 7.2.7.1.
DGL specific provisions take precedence (7.2.3.1); a single subsidiary hazard
can increase segregation (7.2.3.3); multiple subsidiary hazards require column
16b (7.2.3.4). Never convert X or * to numeric zero. The returned ship-cell
alternatives are OR choices; conditions within an alternative are AND conditions.
Select table cases applicable to the actual geometry; do not indiscriminately
AND vertical, fore-and-aft and athwartship cases. For example, a qualifying deck
can permit vertical alignment without horizontal spacing.
Fire/liquid resistance, sensitive vertical lines (7.4.2.2), chemical reactivity,
packaging exceptions and special provisions still need checking by consumers.
All source page numbers are one-based PDF pages of MSC.556(108).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import json
from types import MappingProxyType
from typing import Any, Literal, Mapping

GENERAL_TABLE_NAME = "imdg_42_24.general_segregation_7_2_4"
CONTAINERSHIP_TABLE_NAME = "imdg_42_24.containership_segregation_7_4_3"
DGL_TABLE_NAME = "imdg_42_24.dangerous_goods_list_3_2"
STOWAGE_PROVISIONS_NAME = "imdg_42_24.stowage_and_handling_provisions"

SegregationValue = Literal["1", "2", "3", "4", "X", "*"]
ShipType = Literal["closed_holds", "hatchless"]
Orientation = Literal["vertical", "fore_aft", "athwartships"]
ContainerPair = Literal["closed_closed", "closed_open", "open_open"]
Deck = Literal["on", "under"] | None


def _read(name: str) -> dict[str, Any]:
    return json.loads(files(__package__).joinpath("data", name).read_text(encoding="utf-8"))


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class GeneralCell:
    """One class-by-class cell, retaining symbols and its source coordinates."""

    row_class: str
    column_class: str
    value: SegregationValue
    source: Mapping[str, Any]

    @property
    def level(self) -> int | None:
        """Numeric level, or None when DGL/class-1 consultation is required."""
        return int(self.value) if self.value in {"1", "2", "3", "4"} else None


@dataclass(frozen=True)
class ShipAlternative:
    """One allowed set of simultaneous conditions in a container-ship cell.

    Fields absent from an alternative do not impose a condition. Text and source
    on the parent cell are authoritative when implementing a geometry checker.
    """

    min_container_spaces: int = 0
    min_horizontal_m: float = 0
    min_bulkheads: int = 0
    not_in_or_above_same_hold: bool = False
    not_same_vertical_line: bool = False
    segregated_by_deck: bool = False
    barriers_resistant_to_fire_and_liquids: bool = False
    open_above_closed: bool = False
    prohibited: bool = False
    min_distance_from_intervening_bulkhead_m: float = 0


@dataclass(frozen=True)
class ShipCell:
    """Expanded merged cell of 7.4.3, including exact text and OR alternatives."""

    ship_type: ShipType
    level: int
    orientation: Orientation
    container_pair: ContainerPair
    deck: Deck
    text: str
    alternatives: tuple[ShipAlternative, ...]
    source: Mapping[str, Any]


_CLASS_ALIASES = {
    **dict.fromkeys(("1.1", "1.2", "1.5"), "1.1/1.2/1.5"),
    **dict.fromkeys(("1.3", "1.6"), "1.3/1.6"),
}


@lru_cache(maxsize=1)
def general_cells() -> Mapping[tuple[str, str], GeneralCell]:
    """Load and cache the complete 17×17 general table, indexed in O(1)."""
    data = _read("segregation_7_2_4.json")
    cells = {
        (row["row_class"], row["column_class"]): GeneralCell(
            row["row_class"], row["column_class"], row["value"], _freeze(row["source"])
        )
        for row in data["cells"]
    }
    expected = {(a, b) for a in data["classes"] for b in data["classes"]}
    if set(cells) != expected or len(data["cells"]) != len(expected):
        raise ValueError("IMDG 7.2.4 table is incomplete or contains duplicate cells")
    if any(cell.value not in {"1", "2", "3", "4", "X", "*"} for cell in cells.values()):
        raise ValueError("Unknown IMDG 7.2.4 segregation symbol")
    return MappingProxyType(cells)


def general_segregation(class_a: str, class_b: str) -> GeneralCell:
    """Return the primary-class table cell; accepts individual class-1 divisions.

    Raises KeyError for an unknown class. Class-1 compatibility suffixes are not
    silently stripped: callers must retain those separately for 7.2.7 evaluation.
    """
    return general_cells()[(_CLASS_ALIASES.get(class_a, class_a), _CLASS_ALIASES.get(class_b, class_b))]


@lru_cache(maxsize=1)
def containership_cells() -> Mapping[tuple[str, int, str, str, str | None], ShipCell]:
    """Load both complete ship tables; merged cells are explicitly expanded."""
    data = _read("segregation_7_4_3.json")
    cells = {}
    for table in data["tables"]:
        for row in table["cells"]:
            cell = ShipCell(
                table["ship_type"], row["level"], row["orientation"],
                row["pair"], row["deck"], row["text"],
                tuple(ShipAlternative(**item) for item in row["alternatives"]),
                _freeze(row["source"]),
            )
            key = (cell.ship_type, cell.level, cell.orientation, cell.container_pair, cell.deck)
            if key in cells:
                raise ValueError(f"Duplicate IMDG ship cell: {key}")
            cells[key] = cell
    expected = {
        (ship, level, direction, pair, deck)
        for ship in ("closed_holds", "hatchless")
        for level in range(1, 5)
        for pair in ("closed_closed", "closed_open", "open_open")
        for direction, decks in (("vertical", (None,)), ("fore_aft", ("on", "under")), ("athwartships", ("on", "under")))
        for deck in decks
    }
    if set(cells) != expected:
        raise ValueError("IMDG 7.4.3 tables are incomplete")
    return MappingProxyType(cells)


def containership_segregation(
    level: int,
    *,
    ship_type: ShipType,
    orientation: Orientation,
    container_pair: ContainerPair,
    deck: Deck = None,
) -> ShipCell:
    """Return the precise ship-cell conditions for a numeric segregation level.

    Use deck=None for vertical cells, otherwise 'on' or 'under'. The
    closed/open vertical cell is directional: open above closed can be permitted
    while closed above open is not. This distinction is retained in alternatives.
    """
    if orientation == "vertical":
        deck = None
    return containership_cells()[(ship_type, level, orientation, container_pair, deck)]


def get_table(name: str) -> Any:
    """Resolve the edition-qualified table identifiers used by catalogue.toml."""
    if name == GENERAL_TABLE_NAME:
        return general_cells()
    if name == CONTAINERSHIP_TABLE_NAME:
        return containership_cells()
    # DGL/provisions are resolved lazily to avoid loading samples for matrix-only use.
    if name in (DGL_TABLE_NAME, STOWAGE_PROVISIONS_NAME):
        from . import dgl
        return dgl.get_table(name)
    raise KeyError(name)


@dataclass(frozen=True)
class SegregationCode:
    """Source-traced meaning of a segregation code used by the DGL sample.

    These descriptions retain conditional rules and cross-references; they are
    not an executable rule engine or a full segregation-group membership list.
    """

    code: str
    meaning: str
    source: Mapping[str, Any]


@lru_cache(maxsize=1)
def segregation_codes() -> Mapping[str, SegregationCode]:
    """Return immutable SG/SGG definitions for all codes in the curated sample."""
    return MappingProxyType({
        row["code"]: SegregationCode(row["code"], row["meaning"], _freeze(row["source"]))
        for row in _read("segregation_codes.json")["codes"]
    })
