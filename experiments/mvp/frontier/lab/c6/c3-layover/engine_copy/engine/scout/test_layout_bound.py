# Provenance: copied from experiments/mvp/frontier/lab/c2/c2-layout/test_bound.py.
"""Validate the relaxation mapping on four independently certified V6 plans."""
from collections import defaultdict
import importlib.util
import json
from pathlib import Path
import sys

from stowage.feasibility.tables import build_tables
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.moves import apply_plan

HERE = Path(__file__).resolve().parent
from . import layout_bound as bound


def test_checked_v6_witnesses_embed_in_relaxation():
    paths = [line for line in (HERE/'bound_maps.txt').read_text().splitlines()
             if line and not line.startswith('#')]
    summaries = {}
    for path in Path('experiments/mvp/scale/runs/v6/maps').glob('*/summary.json'):
        row = json.loads(path.read_text())
        if (row.get('checker_passed') is True and row.get('full_checker_unmodified') is True
                and row['job'].get('split') == 'train'):
            summaries[row['job']['input']] = path
    for path in paths:
        summary = summaries[path]
        certified = json.loads(summary.read_text())
        instance = load_instance(path)
        T = build_tables(instance)
        plan = MovePlan.model_validate(json.loads((summary.parent/'finished.json').read_text())['plan'])
        replay = apply_plan(instance, plan)
        assert replay.legal
        assert replay.count.weighted_nonproductive == certified['count']['weighted_nonproductive']
        assignment = {p.container_id: p.slot for p in replay.final_state.ship.placements}
        assert set(assignment) == set(instance.load_list)
        units, mass, height = defaultdict(int), defaultdict(int), defaultdict(int)
        holds, edges, decks = set(), set(), []
        calls = {}
        for k in range(instance.call_index+1, len(instance.rotation.calls)):
            calls.setdefault(instance.rotation.calls[k].locode, k)
        boxes = {b.id: b for b in instance.containers}
        for bid, slot in assignment.items():
            b, s = T.box_index[bid], T.slot_index[slot]
            assert T.static[b, s], (instance.id, bid, slot)
            cover_decks = set()
            for j in T.slot_stacks[s]:
                if j < 0:
                    continue
                region = T.stack_cover[j], bool(T.stack_deck[j])
                units[region] += 1
                mass[region] += (2//int(T.units[b]))*(bound._floor(T.mass[b])//1000)*1000
                height[region] += bound._floor(T.height[b],1000)
                if T.stack_deck[j]:
                    cover_decks.add(T.stack_cover[j])
                else:
                    holds.add(T.stack_cover[j])
                    edges.add((T.stack_cover[j],calls[boxes[bid].pod]))
            decks.append((calls[boxes[bid].pod],cover_decks))
        for region in units:
            js = [j for j in range(len(T.stack_ids))
                  if (T.stack_cover[j],bool(T.stack_deck[j])) == region]
            assert units[region] <= sum(len(T.stack_cells[j]) for j in js)
            assert mass[region] <= sum(bound._ceil(max(T.stack_lim_20[j],T.stack_lim_40[j],T.stack_lim_mixed[j]),2) for j in js)
            assert height[region] <= sum(bound._ceil(T.stack_hmax[j],1000) for j in js)
        restows = sum(any((cover,earlier) in edges for cover in covers)
                      for call,covers in decks for earlier in set(k for _,k in edges) if earlier < call)
        assert 2*len(holds) <= replay.count.hatch_cover_lifts
        assert 2*len(edges) <= sum(f.hatch_cover_lifts for f in replay.count.future)
        assert restows*instance.objective.future_restow_lifts_per_box <= sum(f.restow_lifts for f in replay.count.future)
        result = bound.lower_bound(instance, tables=T, budget_s=10)
        assert result['status'] in ('optimal', 'counting_only')
        assert result['lower_bound'] <= replay.count.weighted_nonproductive
        print(instance.id, result['lower_bound'], 'at most', replay.count.weighted_nonproductive)


def test_rounding_is_outward_at_boundaries():
    assert bound._floor(19999.999999999996)//1000 == 19
    assert bound._floor(20000.)//1000 == 20
    assert bound._ceil(20000.000000000004,2) == 40001
    assert bound._floor(.9999999999999999,2000) == 1999
