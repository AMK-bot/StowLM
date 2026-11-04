"""One-core C2-off warm-prefix cost probe; no generated cache qualification.

Synthetic plain cargo occupies real feeder geometry. This is a timing workload,
not a physically certified loading plan; all hull/bridge/fixed cargo are retained.
Run with one BLAS thread: uv run python tests/feasibility/w2_performance_probe.py.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import signal
from time import perf_counter

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.conventions import make_container_id
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.generate.random_voyage import build_voyage_spec
from stowage.model import Instance, Placement
from stowage.model.fixtures import build_instance
from stowage.model.port import PortCall, Rotation
from stowage.model.ship import ShipState
from stowage.model.yard import YardState, YardStack
from stowage.ports import load_port
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet import load_vessel
from stowage.vessel.fleet_conditions import departure_tank_masses
from stowage.vessel.fleet_slices import slice_preset


def make_performance_instance(rung: str, *, onboard_fraction: float = .5,
                              crane_h: int = 8) -> Instance:
    """A deterministic medium/full feeder500 prefix with half its cargo aboard.

    All columns have a bottom-up prefix and each paired column has one length.
    Independent placements are ordered hold-before-deck, then far-to-near rows;
    no feasibility/expander API chooses them. Yard boxes are singleton row-one
    stacks, so yard crossing and digging cannot dominate the ship-side probe.
    """
    if rung not in ('medium', 'full') or not 0 < onboard_fraction < 1:
        raise ValueError('expected medium/full and a strict interior onboard fraction')
    vessel = slice_preset(load_vessel('feeder_500'), rung)
    port = load_port('laem_chabang_like')
    port.max_crane_height_tiers = port.quay_crane.max_on_deck_tiers = crane_h
    rotation = Rotation(service='feasibility-cost-probe', calls=[
        PortCall(locode=port.locode, archetype=port.archetype, index=i) for i in (0, 1)])
    plain = next(b for b in build_instance().containers if b.length_ft == 20 and not b.dg and not b.oog)
    groups = defaultdict(list)
    for stack in vessel.stacks:
        groups[(stack.on_deck, stack.row, (stack.bay-1)//4)].append(stack)
    boxes, placements, pending = [], [], []
    serial = 0
    # Port berths starboard in this profile: descending positive/negative row
    # magnitude is a deterministic far-to-near ordering within each bay.
    for key, group in sorted(groups.items(), key=lambda item: (item[0][0], -item[0][1], item[0][2])):
        group.sort(key=lambda stack: stack.bay)
        long = len(group) == 2 and group[1].bay == group[0].bay+2 and key[2] % 2 == 0
        columns = [group] if long else [[stack] for stack in group]
        for supports in columns:
            n = min(len(stack.cell_ids) for stack in supports)
            # Leave at least one decision cell above this planned cargo prefix.
            total = max(2, n-1) if n >= 2 else 1
            aboard = max(1, int(total*onboard_fraction))
            for tier in range(total):
                serial += 1
                length = 40 if long else 20
                mass = float((9000 if long else 6000)-500*tier)
                box = plain.model_copy(update=dict(id=make_container_id('QZWU',10000+serial),
                    iso_type='42G1' if long else '22G1', length_ft=length,
                    gross_mass_kg=mass, max_gross_mass_kg=30480., tare_kg=3800. if long else 2200.,
                    height_m=2.591, dg=None, oog=None, special_stowage=[],
                    reefer=False, reefer_setpoint_c=None, direct_delivery=False,
                    pol=port.locode, pod=port.locode))
                boxes.append(box)
                cell = supports[0].cell_ids[tier]
                slot = f'{supports[0].bay+1:03d}{supports[0].row:02d}{cell[-2:]}' if long else cell
                if tier < aboard:
                    placements.append(Placement(container_id=box.id, slot=slot))
                else:
                    pending.append(box)
    block = port.yard.blocks[0].model_copy(update={'n_bays': 3*len(pending)+3, 'n_rows': 1,
                                                 'reefer_rows': [1]})
    port.yard = port.yard.model_copy(update={'blocks': [block]})
    yard = YardState(yard_id=port.yard.id, stacks=[
        YardStack(key=f'{block.id}-{3*i+1:03d}-01', length_ft=box.length_ft, boxes=[box.id])
        for i, box in enumerate(pending)])
    return Instance(id=f'w2-cost-feeder500-{rung}',stage='A',size_class=rung,seed=1028,
        vessel=vessel,port=port,rotation=rotation,call_index=0,containers=boxes,
        load_list=[box.id for box in pending],yard=yard,
        onboard=ShipState(vessel_id=vessel.id,placements=placements,
                          tank_masses_kg=departure_tank_masses(vessel)),
        voyage=build_voyage_spec(rotation,0,port,seed=1028))


def probe(rung: str, *, active: bool = True, repeats: int = 2, timeout: int = 45,
          lazy: bool = False) -> dict:
    """Time uncached next-prefix evaluation after one warm prefix, C2 off."""
    row = dict(rung=rung, active=active, c2=False, lazy=lazy, status='ok')
    class ProbeTimeout(BaseException):
        """A stop cap must survive checker exception-to-verdict boundaries."""
    def expired(signum, frame):
        raise ProbeTimeout(f'prefix exceeded {timeout}s stop cap')
    previous = signal.signal(signal.SIGALRM, expired)
    try:
        with rule_freeze_v1(enabled=active):
            instance = make_performance_instance(rung)
            row.update(cells=len(instance.vessel.cells), boxes=len(instance.containers),
                       onboard=len(instance.onboard.placements), pending=len(instance.load_list))
            times, builds, box_times, slot_times, full_times = [], [], [], [], []
            for repeat in range(repeats+1):
                signal.alarm(timeout)
                start = perf_counter()
                row['phase'] = 'build'
                engine = make_feasibility_engine(instance, get_action_space('boxslot'),
                                                config=FeasibilityConfig(extra={'global_screen': 0}))
                builds.append(perf_counter()-start)
                row['last_build_s'] = builds[-1]
                start = perf_counter()
                if lazy:
                    row['phase'] = 'legal_box_ids'
                    box_ids = engine.legal_box_ids()
                    box_elapsed = perf_counter()-start
                    chosen = box_ids[0] if box_ids else None
                    start = perf_counter()
                    row['phase'] = 'legal_slots_for_box'
                    slots = engine.legal_slots_for_box(chosen) if chosen else ()
                    slot_elapsed = perf_counter()-start
                    elapsed = box_elapsed+slot_elapsed
                    row.update(legal_boxes=len(box_ids), chosen_box=chosen, chosen_slots=len(slots),
                               last_box_s=box_elapsed, last_slots_s=slot_elapsed)
                    if repeat:
                        box_times.append(box_elapsed)
                        slot_times.append(slot_elapsed)
                        times.append(elapsed)
                        row.update(box_s=box_times, slots_s=slot_times, step_s=times,
                                   warm_mean_s=sum(times)/len(times))
                    # Comparison follows the timed lazy domain queries; report
                    # its separate incremental cost rather than mixing it into
                    # the decoder's box-token + chosen-slot-token latency.
                    signal.alarm(timeout)
                    row['phase'] = 'full_mask_consistency'
                    start = perf_counter()
                    actions = engine.legal_actions()
                    full_elapsed = perf_counter()-start
                    assert set(box_ids) == {a.box for a in actions if isinstance(a, Place)}
                    assert set(slots) == {a.slot for a in actions if isinstance(a, Place) and a.box == chosen}
                    row['consistency'] = 'equal'
                    if repeat:
                        full_times.append(full_elapsed)
                        row['full_after_lazy_s'] = full_times
                else:
                    row['phase'] = 'legal_actions'
                    actions = engine.legal_actions()
                    elapsed = perf_counter()-start
                    if repeat:
                        times.append(elapsed)
                signal.alarm(0)
                row.update(offered=len(actions), diagnostics=engine.diagnostics)
            row.update(build_s=builds[1:], step_s=times, warm_mean_s=sum(times)/len(times))
    except ProbeTimeout as exc:
        row.update(status='stop_cap', reason=str(exc), exceeded_s=timeout)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rung', choices=('medium','full','both'), default='both')
    parser.add_argument('--legacy', action='store_true')
    parser.add_argument('--lazy', action='store_true', help='Time box domain plus chosen-slot domain, then verify full mask.')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=45)
    args = parser.parse_args()
    for rung in ('medium','full') if args.rung == 'both' else (args.rung,):
        print(json.dumps(probe(rung,active=not args.legacy,repeats=args.repeats,
                               timeout=args.timeout,lazy=args.lazy),default=str),flush=True)


if __name__ == '__main__':
    main()
