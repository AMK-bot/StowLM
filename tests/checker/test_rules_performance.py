"""Reproducible static-rule timings, with no flaky wall-clock pytest assertion.

Run directly with ``uv run python tests/checker/test_rules_performance.py``.
Synthetic benchmark cargo is not a checker-approved plan. Vessel construction
and cold imports are excluded from warm timings. No hull/physics is constructed.
Use ``--repeats 1 --e6-boxes 1`` for a small admission probe first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
from time import perf_counter


def condition(kind, *, e6_boxes=16, dg_boxes=64):
    """Full 1,604-cell feeder; E6 uses only deck cargo and empty holds.

    ASSUMPTION: dry benchmark boxes have 12t VGM (22t for deck H); their
    synthetic 100,000kg plate ratings are workload data, not real certification.
    DG workload uses UN1202/III with explicit synthetic packing premises.
    No regulatory limit is overridden and emitted violations are retained.
    """
    from stowage.checker.rules_imdg import STOWAGE
    from stowage.conventions import make_container_id
    from stowage.model import Placement, ShipState
    from stowage.model.cargo import DangerousGoods
    from stowage.model.fixtures import build_instance
    from stowage.rules.imdg.dgl import lookup_dgl
    from stowage.vessel.fleet import load_vessel

    vessel = load_vessel('feeder_1700', with_hull=False)
    vessel.fixed_loads = []  # Isolate explicit placements from background evidence.
    instance = build_instance()
    template = instance.containers[1]
    stacks = {stack.id: stack for stack in vessel.stacks}
    cells = sorted(vessel.cells, key=lambda cell: cell.id)
    if kind == 'e6':
        cells = [cell for cell in cells if stacks[cell.stack_id].on_deck]
    entry = lookup_dgl(1202, 'III')
    dg = DangerousGoods(un_number='1202', imdg_class=entry.hazard_class,
        subsidiary_risks=list(entry.subsidiary_risks), packing_group='III',
        stowage_category=entry.stowage_category, stowage_codes=list(entry.stowage_codes),
        segregation_codes=list(entry.segregation_codes),
        packing_form='packaged_goods_in_closed_freight_containers', ctu_closed=True,
        clear_living_quarters=True, protected_from_heat=True, handling_compliant=True)
    containers, placements = [], []
    for serial, cell in enumerate(cells, 1):
        box = template.model_copy(update=dict(id=make_container_id('QZXU', serial),
            gross_mass_kg=22000 if kind == 'e6' and serial <= e6_boxes else 12000,
            max_gross_mass_kg=100000, pod='THBKK', special_stowage=[], oog=None,
            dg=dg if kind == 'dg' and serial <= dg_boxes else None))
        containers.append(box)
        placements.append(Placement(slot=cell.id, container_id=box.id))
    ship = ShipState(vessel_id=vessel.id, placements=placements)
    instance = instance.model_copy(update=dict(vessel=vessel, containers=containers,
        load_list=[], discharge_list=[], onboard=ship.model_copy(update={'placements': []}),
        rule_overrides={STOWAGE: {}}))
    return instance, ship


def benchmark(kind, *, repeats=3, e6_boxes=16, dg_boxes=64):
    from stowage.checker.rules import check_static_rules

    setup = perf_counter()
    instance, ship = condition(kind, e6_boxes=e6_boxes, dg_boxes=dg_boxes)
    setup = perf_counter() - setup
    check_static_rules(instance, ship, call_index=0)  # warm imports/catalogue
    durations = []
    for _ in range(repeats):
        start = perf_counter()
        report = check_static_rules(instance, ship, call_index=0)
        durations.append(perf_counter() - start)
    return dict(kind=kind, boxes=len(ship.placements),
        dg_boxes=sum(box.dg is not None for box in instance.containers),
        deck_heavy_boxes=sum(box.gross_mass_kg >= 20000 for box in instance.containers),
        setup_seconds=setup, warm_seconds=durations,
        median_seconds=statistics.median(durations), violations=len(report.violations),
        rule_ids=sorted({v.rule_id for v in report.violations}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kinds', nargs='+', choices=('dry', 'dg', 'e6'), default=['dry', 'dg', 'e6'])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--e6-boxes', type=int, default=16)
    parser.add_argument('--dg-boxes', type=int, default=64)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sources = [*sorted((root / 'src/stowage/checker').glob('rules*.py')),
               root / 'params/fleet/feeder_1700.toml', Path(__file__).resolve()]
    before = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    results = [benchmark(kind, repeats=args.repeats, e6_boxes=args.e6_boxes,
                         dg_boxes=args.dg_boxes) for kind in args.kinds]
    after = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    print(json.dumps(dict(results=results, source_sha256=before,
        sources_stable_during_calls=before == after,
        threads={name: os.environ.get(name) for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS')}), indent=2))
