# Copied from experiments/mvp/frontier/lab/c3/c2-layout/test_bound.py; engine integration changes are local.
"""Capacity-cut mathematics and certified-witness mapping checks."""
from collections import defaultdict
import importlib.util
import json
from pathlib import Path

from stowage.feasibility.tables import build_tables
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.moves import apply_plan

HERE = Path(__file__).resolve().parent
from . import physical_capacity as bound

def test_minimum_covers_is_capacity_necessary():
    assert bound.minimum_covers(0, [18, 18, 8, 8, 4, 4]) == 0
    assert bound.minimum_covers(37, [18, 18, 8, 8, 4, 4]) == 3
    assert bound.minimum_covers(53, [18, 18, 8, 8, 4, 4]) == 5
    assert bound.minimum_covers(61, [18, 18, 8, 8, 4, 4]) == 7


def test_rounding_is_outward():
    assert bound._floor(19999.999999999996) == 19999
    assert bound._ceil(20000.000000000004, 2) == 40001


def test_certified_v6_witnesses_obey_stack_and_e6_cuts():
    summaries = {}
    for path in Path('experiments/mvp/scale/runs/v6/maps').glob('*/summary.json'):
        row = json.loads(path.read_text())
        if (row.get('checker_passed') is True and row.get('full_checker_unmodified') is True
                and row['job'].get('split') == 'train'):
            summaries[row['job']['input']] = path
    for path in (HERE/'fixtures/capacity-train4.txt').read_text().splitlines():
        summary = summaries[path]
        instance = load_instance(path)
        T = build_tables(instance)
        from .unary import optimistic_dg_tables
        T = optimistic_dg_tables(T)
        plan = MovePlan.model_validate(json.loads((summary.parent/'finished.json').read_text())['plan'])
        replay = apply_plan(instance, plan)
        assert replay.legal
        assignment = {p.container_id: p.slot for p in replay.final_state.ship.placements}
        assert set(assignment) == set(instance.load_list)
        counts, mass, height = defaultdict(int), defaultdict(int), defaultdict(int)
        lengths, used = defaultdict(set), set()
        for bid, slot in assignment.items():
            b, s = T.box_index[bid], T.slot_index[slot]
            assert T.static[b, s]
            for j in T.slot_stacks[s]:
                if j < 0:
                    continue
                counts[j] += 1
                mass[j] += (2//int(T.units[b]))*bound._floor(T.mass[b])
                height[j] += bound._floor(T.height[b], 1000)
                lengths[j].add(T.length[b] == 20)
                if not T.stack_deck[j]:
                    used.add(T.stack_cover[j])
        for j in counts:
            assert counts[j] <= len(T.stack_cells[j])
            assert mass[j] <= bound._ceil(max(T.stack_lim_mixed[j], T.stack_lim_20[j], T.stack_lim_40[j]), 2)
            assert height[j] <= bound._ceil(T.stack_hmax[j], 1000)
            if not T.stack_deck[j]:
                assert len(lengths[j]) <= 1
        witnesses = bound._e6_witness_covers(instance, T)
        for bid, covers in witnesses.items():
            if T.slot_deck[T.slot_index[assignment[bid]]]:
                assert set(covers) <= used
        result = bound.lower_bound(instance, tables=T, budget_s=15)
        assert result['lower_bound'] <= replay.count.weighted_nonproductive
        assert result['lower_bound'] >= result['weighted_forced_relocations']


def test_e6_cut_is_disabled_when_rule_is_soft(monkeypatch):
    monkeypatch.setattr(bound, 'rule_freeze_v1_enabled', lambda: True)
    assert bound._e6_witness_covers(None, None) == {}
