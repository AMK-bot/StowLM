"""Frozen complete verdicts for fixtures with unknown assessment evidence.

Digests were computed from the pre-assessor checker, including ordered complete
Violation JSON records. IMDG's evidence extension is independent of D61/D62/D64,
so the same baseline applies with the rule-freeze switch in either state.
"""

from hashlib import sha256
import json
from pathlib import Path

import pytest

from stowage.checker import rules_imdg
from stowage.conventions import cells_of_slot, format_slot, parse_slot
from stowage.model import Instance
from stowage.model.cargo import DangerousGoods
from stowage.rules.catalogue import rule_freeze_v1
from stowage.rules.imdg.dgl import lookup_dgl


ROOT = Path(__file__).resolve().parents[2]
_EXPECTED = {
    'empty_ship': '448cbcfada7288709133e2ce4456aa586e04a93a7b423887294381a867a65313',
    'layover': '448cbcfada7288709133e2ce4456aa586e04a93a7b423887294381a867a65313',
    'H1': '7524eb789b58486a3d0e7573c420cd92204fd6c99f9d38f0cc8fa91a23211d3a',
    'SW1': '859841bd704929e661cc29098045f746d483b3b7265762507f9bbe8521aa9e98',
    'SW2': '7f7a1aab14c54c8356be52ab70f928cd4f1a942f04fc629f9dd437bd20ef810a',
    'SW19': '9bf172e46bcd7ea6e62633fc06ab4b796558fee3b852451cf032bea7221e5b90',
}


def _sample_slots(instance, box):
    """First/last compatible deck/hold footprint; independent of occupancy."""
    cells = {cell.id: cell for cell in instance.vessel.cells}
    candidates = set()
    for cell in cells.values():
        address = parse_slot(cell.id)
        slot = cell.id if box.length_ft == 20 else format_slot(address.bay + 1, address.row, address.tier)
        footprint = cells_of_slot(slot)
        if all(cid in cells and getattr(cells[cid], f'accepts_{box.length_ft}') for cid in footprint):
            candidates.add(slot)
    selected = set()
    for on_deck in (False, True):
        group = sorted(s for s in candidates if parse_slot(s).on_deck is on_deck)
        if group:
            selected.update((group[0], group[-1]))
    return sorted(selected)


def _verdict_records(instance, checker=rules_imdg):
    records = [['onboard', [v.model_dump(mode='json') for v in checker.imdg_violations(
        instance, instance.onboard, call_index=instance.call_index)]]]
    for box in instance.containers:
        if box.dg is not None:
            for slot in _sample_slots(instance, box):
                records.append([box.id, slot, [v.model_dump(mode='json') for v in checker.imdg_placement_violations(
                    instance, instance.onboard, box, slot, call_index=instance.call_index)]])
    return records


def _digest(records):
    return sha256(json.dumps(records, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _unknown_case(name):
    fixture = name if name in {'empty_ship', 'layover'} else 'empty_ship'
    instance = Instance.model_validate_json((ROOT / f'data/fixtures/{fixture}.json').read_text())
    if name not in {'empty_ship', 'layover'}:
        un, pg = {'H1': (1333, 'II'), 'SW1': (1361, 'II'), 'SW2': (2810, 'II'), 'SW19': (3480, None)}[name]
        entry = lookup_dgl(un, pg)
        instance.containers[1].dg = DangerousGoods(
            un_number=f'{un:04d}', imdg_class=entry.hazard_class,
            subsidiary_risks=list(entry.subsidiary_risks), packing_group=pg,
            stowage_category=entry.stowage_category, stowage_codes=list(entry.stowage_codes),
            segregation_codes=list(entry.segregation_codes))
    return instance


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('name', _EXPECTED)
def test_unknown_assessment_evidence_keeps_complete_legacy_verdicts(name, enabled):
    instance = _unknown_case(name)
    with rule_freeze_v1(enabled=enabled):
        records = _verdict_records(instance)
    assert _digest(records) == _EXPECTED[name]
