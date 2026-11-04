"""Curated IMDG 42-24 Dangerous Goods List and stowage-code definitions.

Entries are indexed by UN number and packing group (I, II or III indicates
increasingly lower packing danger). A UN number alone is not always a unique
entry. The sample covers every class/division; it is not the complete DGL.

Column 16b contains both segregation groups (SGG: chemical families) and special
segregation instructions (SG). Consumers must resolve these instructions and
conditional stowage/handling provisions, rather than use the class table alone.
Column 16a's category is the base category: e.g. SW19 can change lithium battery
stowage depending on special provisions and voyage. This module supplies data,
not a complete shipment authorization or an evaluator of those conditions.

``source_stowage_codes`` contains the actual column 16a SW/H codes.
``stowage_codes`` additionally carries explicitly labelled internal guards for
unresolved mandatory provisions. Checker 2.4 merges these from the entry and
requires its existing external ``verified_provisions`` facts; a generator cannot
silently clear the guard by dropping it from a declaration. These identifiers
are integration metadata, not invented IMDG SW codes.

The US HMT is a cross-check only; its labels and vessel provisions have different
semantics from IMDG columns 4 and 16b. IMDG 42-24 is authoritative here.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import json
from types import MappingProxyType
from typing import Any, Mapping

DGL_TABLE = 'imdg_42_24.dangerous_goods_list_3_2'
PROVISIONS_TABLE = 'imdg_42_24.stowage_and_handling_provisions'


@dataclass(frozen=True)
class DGLSource:
    """One-based PDF page and DGL row/columns in the public IMO resolution."""

    document: str
    pdf_page: int
    table: str
    row: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class CFRRow:
    """US HMT row, including continuation rows with inherited UN/name/class."""

    un_number: int
    proper_shipping_name: str
    hazard_class: str
    packing_group: str | None
    labels: str
    stowage_category: str
    vessel_provisions: str
    xml_row: int


@dataclass(frozen=True)
class CFRCrossCheck:
    """Recorded comparison with the 2026-10-23 snapshot of 49 CFR 172.101."""

    status: str
    differences: tuple[str, ...]
    authority: str
    rows: tuple[CFRRow, ...]


@dataclass(frozen=True)
class DangerousGoodsEntry:
    """A single sampled DGL row; empty subsidiary risks may have a condition."""

    id: str
    un_number: int
    proper_shipping_name: str
    hazard_class: str
    subsidiary_risks: tuple[str, ...]
    subsidiary_hazard_note: str | None
    compatibility_group: str | None
    packing_group: str | None
    stowage_category: str
    source_stowage_codes: tuple[str, ...]
    segregation_codes: tuple[str, ...]
    special_provisions: tuple[str, ...]
    source: DGLSource
    cfr_cross_check: CFRCrossCheck
    unresolved_provisions: tuple[StowageHandlingProvision, ...] = ()

    @property
    def stowage_codes(self) -> tuple[str, ...]:
        """DGL SW/H codes plus explicit fail-closed checker guard identifiers.

        Use source_stowage_codes when quoting column 16a. Internal guards must
        be externally verified through checker 2.4's verified_provisions facts.
        """
        return self.source_stowage_codes + tuple(p.code for p in self.unresolved_provisions)


@dataclass(frozen=True)
class StowageHandlingProvision:
    """Plain-language meaning of a sampled SW/H code, with its source."""

    code: str
    meaning: str
    source: DGLSource


def _source(raw: dict[str, Any]) -> DGLSource:
    return DGLSource(raw['document'], raw['pdf_page'], raw['table'], raw['row'],
                     tuple(raw.get('columns', [raw.get('column')])))


@lru_cache(maxsize=1)
def _load() -> tuple[Mapping[str, DangerousGoodsEntry], Mapping[str, StowageHandlingProvision]]:
    raw = json.loads(files(__package__).joinpath('data/dgl.json').read_text())
    entries: dict[str, DangerousGoodsEntry] = {}
    for item in raw['entries']:
        row = dict(item)
        row['source'] = _source(row['source'])
        for key in ('subsidiary_risks', 'stowage_codes', 'segregation_codes', 'special_provisions'):
            row[key] = tuple(row[key])
        row['source_stowage_codes'] = row.pop('stowage_codes')
        row['unresolved_provisions'] = tuple(
            provision for un_numbers, provision in _checker_guards()
            if row['un_number'] in un_numbers
        )
        check = row['cfr_cross_check']
        cfr_rows = tuple(CFRRow(**{k: v for k, v in r.items() if k != 'source'},
                                 xml_row=r['source']['xml_row']) for r in check['rows'])
        row['cfr_cross_check'] = CFRCrossCheck(check['status'], tuple(check['differences']),
                                             check['authority'], cfr_rows)
        entry = DangerousGoodsEntry(**row)
        if entry.id in entries:
            raise ValueError(f'Duplicate DGL ID: {entry.id}')
        entries[entry.id] = entry
    provisions = {r['code']: StowageHandlingProvision(r['code'], r['meaning'], _source(r['source']))
                  for r in raw['provisions']}
    return MappingProxyType(entries), MappingProxyType(provisions)


def get_table(name: str) -> Mapping[str, DangerousGoodsEntry] | Mapping[str, StowageHandlingProvision]:
    """Return a cached read-only table under its rule-catalogue name."""
    if name == DGL_TABLE:
        return _load()[0]
    if name == PROVISIONS_TABLE:
        return _load()[1]
    raise KeyError(name)


def dangerous_goods_list() -> tuple[DangerousGoodsEntry, ...]:
    """Return all sampled rows in source insertion/packing-group order."""
    return tuple(_load()[0].values())


@lru_cache(maxsize=1)
def _by_un() -> Mapping[int, tuple[DangerousGoodsEntry, ...]]:
    index: dict[int, list[DangerousGoodsEntry]] = {}
    for entry in dangerous_goods_list():
        index.setdefault(entry.un_number, []).append(entry)
    return MappingProxyType({k: tuple(v) for k, v in index.items()})


def entries_for_un(un_number: int) -> tuple[DangerousGoodsEntry, ...]:
    """O(1) lookup of all sampled packing-group variants; absent UN returns ()."""
    return _by_un().get(un_number, ())


@lru_cache(maxsize=1)
def _by_class() -> Mapping[str, tuple[DangerousGoodsEntry, ...]]:
    index: dict[str, list[DangerousGoodsEntry]] = {}
    for entry in dangerous_goods_list():
        index.setdefault(entry.hazard_class, []).append(entry)
    return MappingProxyType({k: tuple(v) for k, v in index.items()})


def entries_by_class(hazard_class: str) -> tuple[DangerousGoodsEntry, ...]:
    """O(1) sampling pool by exact class/division, e.g. '1.4' or '5.1'."""
    return _by_class().get(hazard_class, ())


def lookup_dgl(un_number: int, packing_group: str | None = None) -> DangerousGoodsEntry:
    """Return an exact row; require a packing group for a multivariant UN.

    None means the DGL has no assigned packing group. An ambiguous lookup raises
    ValueError; an absent UN/packing-group combination raises KeyError.
    """
    entries = entries_for_un(un_number)
    if packing_group is None and len(entries) > 1:
        raise ValueError(f'UN {un_number:04d} requires a packing group')
    key = f'UN{un_number:04d}-{packing_group or "none"}'
    return _load()[0][key]


def stowage_handling_provision(code: str) -> StowageHandlingProvision:
    """Return a sampled SW/H or explicit internal guard; unknown codes raise KeyError."""
    if code in _load()[1]:
        return _load()[1][code]
    for _, provision in _checker_guards():
        if provision.code == code:
            return provision
    raise KeyError(code)


@lru_cache(maxsize=1)
def _checker_guards() -> tuple[tuple[tuple[int, ...], StowageHandlingProvision], ...]:
    raw = json.loads(files(__package__).joinpath('data/checker_guards.json').read_text())
    return tuple(
        (tuple(row['un_numbers']), StowageHandlingProvision(
            row['code'], row['meaning'], _source(row['source'])))
        for row in raw['guards']
    )
