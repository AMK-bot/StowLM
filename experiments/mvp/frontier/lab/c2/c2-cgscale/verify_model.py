"""Reproducible deterministic pricing and same-pool master regressions (TRAIN only)."""
from __future__ import annotations

import os
for variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[variable] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

import argparse
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'results/vessel-cache')
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier')]

import numpy as np
from alns import Problem
from stowage.io import load_instance


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


class TestOracle:
    """Deterministic tier-varying weights exercise the securing DP without a reference stow."""
    def weights(self, length, slots):
        return .02, np.asarray([.000008*(1.+.3*k) for k in range(len(slots))])

    def ratio(self, boxes, slots):
        return 0.


def verify(output):
    old = module('cgscale_verify_original', ROOT/'experiments/mvp/frontier/lab/c1/fx-scout/pages.py')
    new = module('cgscale_verify_optimized', HERE/'pages.py')
    path = ROOT/'experiments/mvp/strategy/blockrule/maps/train/medium/feeder_1700/random_yard/seed-185100200.json'
    e9 = module('cgscale_verify_e9', ROOT/'experiments/mvp/frontier/lab/c1/leader/e9fix.py')
    e9.install()
    problem = Problem(load_instance(path))
    rng = np.random.default_rng(20261101)
    report = {'input': str(path.relative_to(ROOT)), 'n_boxes': len(problem.instance.load_list),
              'pricing': [], 'master': []}
    for securing in (False, True):
        oracle = TestOracle() if securing else None
        a, b = old.Model(problem, oracle=oracle), new.Model(problem, oracle=oracle)
        assert np.array_equal(a.static, b.static)
        # Include both nonbinding-height (compressed DP) and binding-height (full DP) cases.
        for binding in (False, True):
            if binding:
                for k, page in enumerate(a.pages):
                    a.pages[k] = replace(page, hmax=max(2.591, len(page.slots)*2.72))
                    b.pages[k] = replace(b.pages[k], hmax=a.pages[k].hmax)
            for phase in (1, 3):
                for constant in (False, True):
                    value = np.full(len(a.boxes), 1000.) if constant else rng.uniform(-10., 1000., len(a.boxes))
                    elapsed = [0., 0.]
                    compared = 0
                    for pages in a.types.values():
                        k = pages[0]
                        t = monotonic(); expected = a.price(k, value, phase=phase); elapsed[0] += monotonic()-t
                        t = monotonic(); actual = b.price(k, value, phase=phase); elapsed[1] += monotonic()-t
                        assert expected.keys() == actual.keys(), (securing, binding, phase, k)
                        for h in expected:
                            assert abs(expected[h][0]-actual[h][0]) < 1e-8
                            assert expected[h][1] == actual[h][1], (securing, binding, phase, constant, k, h)
                            compared += 1
                    report['pricing'].append(dict(securing=securing, binding_height=binding, phase=phase,
                                                  constant_duals=constant, compared=compared,
                                                  original_s=elapsed[0], optimized_s=elapsed[1]))
    for lcg in (None, 0.):
        a, b = old.Model(problem, lcg_target=lcg), new.Model(problem, lcg_target=lcg)
        for _ in range(2):
            value = rng.uniform(0., 1000., len(a.boxes))
            for pages in a.types.values():
                for k in pages[:2]:
                    for total, boxes in a.price(k, value).values():
                        assert a.add(k, boxes) == b.add(k, boxes)
        assert a.columns == [old.Pattern(c.page, c.boxes, c.cost) for c in b.columns]
        for phase in (1, 3):
            for reduced in (False, True):
                boxes, stacks = set(a.boxes), set(a.stacks)
                if reduced and a.columns:
                    fixed = a.columns[len(a.columns)//2]
                    boxes -= set(fixed.boxes)
                    stacks -= set(a.pages[fixed.page].stacks)
                a.banned = b.banned = set(range(0, len(a.columns), 13))
                arguments = dict(active_boxes=boxes, active_stacks=stacks, hold_full=False, phase=phase)
                t = monotonic(); expected = a.solve_lp(**arguments); old_s = monotonic()-t
                t = monotonic(); actual = b.solve_lp(**arguments); new_s = monotonic()-t
                assert abs(expected[0]-actual[0]) <= 1e-7*max(1., abs(expected[0]))
                assert abs(expected[4]-actual[4]) < 1e-7
                # Duals can differ on the same degenerate LP, so compare primal results only.
                report['master'].append(dict(lcg=lcg, phase=phase, reduced=reduced, columns=len(a.columns),
                                             objective=actual[0], uncovered=actual[4], original_s=old_s,
                                             optimized_s=new_s))
    from stowage.checker.rules import placement_violations, _geometry_index, _usable_ship
    from stowage.checker.rules_special import special_violations
    from stowage.model.ship import Placement
    heat_path = ROOT/'experiments/mvp/strategy/blockrule/maps/train/medium/feeder_1700/simulated_yard/seed-185100210.json'
    heat_problem = Problem(load_instance(heat_path))
    t = monotonic()
    heat_model = new.Model(heat_problem)
    report['static'] = {'optimized_model_s': monotonic()-t, 'probes': []}
    T, instance, ship = heat_problem.engine.T, heat_problem.instance, heat_problem.instance.onboard
    heat_boxes = [b for b in heat_model.boxes if T.heat[b]]
    pairs = [(b, int(s)) for b in heat_boxes for s in np.flatnonzero(T.static[b])]
    _, _, usable_cells, _ = _geometry_index(instance)
    valid_ids = {box.id for box in instance.containers}
    for index in np.linspace(0, len(pairs)-1, min(10, len(pairs)), dtype=int):
        b, slot_index = pairs[index]
        box, slot = heat_problem.boxes[T.box_ids[b]], T.slot_ids[slot_index]
        t = monotonic()
        expected = [v.model_dump() for v in placement_violations(instance, ship, box, slot)
                    if 'away_from_heat' in str(v.rule_id)]
        original_s = monotonic()-t
        candidate = ship.model_copy(update={'placements': [Placement.model_construct(container_id=box.id, slot=slot)]})
        candidate = _usable_ship(candidate, valid_ids, usable_cells)
        t = monotonic()
        actual = [v.model_dump() for v in special_violations(instance, candidate, call_index=instance.call_index,
                  focus_container_id=box.id) if 'away_from_heat' in str(v.rule_id)]
        optimized_s = monotonic()-t
        assert expected == actual, (box.id, slot, expected, actual)
        report['static']['probes'].append(dict(box=box.id, slot=slot, original_s=original_s,
                                              optimized_s=optimized_s, violations=len(actual)))
    report['static']['heat_boxes'] = len(heat_boxes)
    report['static']['eligible_pairs'] = len(pairs)
    report['passed'] = True
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'passed': True, 'pricing_cases': len(report['pricing']),
                      'master_cases': len(report['master']), 'output': str(output)}, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=HERE/'results/verify-model.json')
    verify(parser.parse_args().out)
