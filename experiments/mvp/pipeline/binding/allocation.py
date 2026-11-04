"""Fast necessary capacity allocation for the strict physical hatch instruction.

The small CP-SAT model allocates covers to PODs and load boxes to *aggregate
footprint signatures*. It does not pack physical slots or certify a stow plan.
The existing strict compiler supplies the final per-slot mask; Frontier and the
unchanged full checker remain responsible for actual packing and physics.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from time import perf_counter

from ortools.sat.python import cp_model

from experiments.mvp.reader.constraints import (
    CompiledHatchConstraint, InfeasibleInstruction, compile_one_port_per_hatch,
)
from experiments.mvp.reader.select_binding import physical_slots_for_box
from stowage.conventions import cells_of_slot

from .admission import wait_for_capacity

SOLVE_LIMIT_S = 3.0


class HatchInstructionConstraint(CompiledHatchConstraint):
    """The strict hatch rule, independent of the engine's cargo-completion tier.

    The historical reader compiler also reports omitted load-list boxes from
    ``violations``. Completion is T3 and may be explicitly relaxed by Frontier;
    hatch occupancy itself remains mandatory on that partial plan. Keep the
    compiler's exact placement, cover/POD, replay and fixed-retained semantics,
    leaving missing-load accounting exclusively to the original full checker.
    """

    @classmethod
    def from_compiled(cls, compiled):
        return cls(compiled.instance, dict(compiled.cover_ports),
                   dict(compiled.cell_covers), dict(compiled.box_ports), compiled.base)

    def violations(self, plan):
        from stowage.moves import apply_plan
        errors = []
        for move in plan.moves:
            if move.kind in {'load', 'restow_on', 'restow_shift'} and hasattr(move, 'container_id'):
                if reason := self.forbids(move.container_id, move.dst):
                    errors.append(reason)
        try:
            replay = apply_plan(self.instance, plan)
        except (ValueError, KeyError, TypeError):
            return errors + ['instruction audit: invalid plan replay']
        if not replay.legal:
            return errors + ['instruction audit: illegal plan replay']
        positions = {p.container_id: p.slot for p in replay.final_state.ship.placements}
        errors.extend(self.audit_positions(positions))
        discharged = set(self.instance.discharge_list)
        for placement in self.instance.onboard.placements:
            if (placement.container_id not in discharged
                    and positions.get(placement.container_id) != placement.slot):
                errors.append(f'retained cargo changed final slot: {placement.container_id}')
        return errors


def _signature(footprint, cell_covers):
    counts = Counter(cell_covers[cell] for cell in footprint)
    return tuple(sorted(counts.items(), key=lambda item: (item[0] is not None, item[0] or '')))


def derive_hatch_allocation(instance, base=None, workers=1, seed=0):
    """Return a strict compiled mask and explicitly non-certifying evidence.

    Every actual final packing respecting fixed retained slots has a feasible
    signature assignment in this relaxation. Its INFEASIBLE result therefore
    screens out instruction completion; FEASIBLE only chooses a conservative
    cover mapping for the real engine to attempt. UNKNOWN is unavailable, never
    represented as a proven impossibility. Exactly one numerical worker runs.

    ``base`` restricts allocation choices for new loads only. It is deliberately
    not included in the returned strict hatch mask: the caller's intersection
    must retain and independently audit those ordinary instructions. In
    particular, a loading-only bay closure does not forbid unmoved retained
    cargo that was already in that bay.
    """
    began = perf_counter()
    if workers != 1:
        raise ValueError('aggregate hatch allocation uses exactly one CPU worker')
    if base is not None and base.instance.id != instance.id:
        raise ValueError('base constraints belong to another instance')
    boxes = {box.id: box for box in instance.containers}
    discharged = set(instance.discharge_list)
    retained = {p.container_id: p.slot for p in instance.onboard.placements
                if p.container_id not in discharged}
    loads = list(dict.fromkeys(bid for bid in instance.load_list if bid not in retained))
    active = [*loads, *retained]
    if not active:
        raise InfeasibleInstruction('no cargo from which to derive a hatch allocation')
    if missing := set(active)-boxes.keys():
        raise InfeasibleInstruction(f'unknown cargo in hatch allocation: {sorted(missing)}')
    order = {str(call.locode): call.index for call in instance.rotation.calls}
    ports = sorted({str(boxes[bid].pod) for bid in active},
                   key=lambda port: (order.get(port, float('inf')), port))
    covers = sorted(cover.id for cover in instance.vessel.hatch_covers)
    # Reuse the authoritative compiler's physical membership validation without
    # making an arbitrary provisional allocation constrain retained cargo.
    empty = instance.model_copy(update={'onboard': instance.onboard.model_copy(update={'placements': []})})
    geometry = compile_one_port_per_hatch(empty, {cover: ports[0] for cover in covers})
    cell_covers = geometry.cell_covers
    capacities = Counter(cell_covers.values())
    occupied = set()
    fixed_cover = {}
    for bid, slot in retained.items():
        try:
            footprint = cells_of_slot(slot)
        except ValueError as error:
            raise InfeasibleInstruction('retained cargo has an invalid physical footprint') from error
        if any(cell not in cell_covers for cell in footprint) or occupied.intersection(footprint):
            raise InfeasibleInstruction('retained cargo has unknown or overlapping physical cells')
        occupied.update(footprint)
        pod = str(boxes[bid].pod)
        for cell in footprint:
            cover = cell_covers[cell]
            capacities[cover] -= 1
            if cover is not None:
                if cover in fixed_cover and fixed_cover[cover] != pod:
                    raise InfeasibleInstruction('retained cargo mixes PODs on a physical hatch')
                fixed_cover[cover] = pod

    model = cp_model.CpModel()
    selectors = {(cover, pod): model.NewBoolVar(f'cover_{i}_pod_{j}')
                 for i, cover in enumerate(covers) for j, pod in enumerate(ports)}
    for cover in covers:
        model.AddExactlyOne(selectors[cover, pod] for pod in ports)
        if cover in fixed_cover:
            model.Add(selectors[cover, fixed_cover[cover]] == 1)

    capacity_terms = defaultdict(list)
    choices = {}
    signature_counts = {}
    physical_slot_counts = {}
    for index, bid in enumerate(loads):
        signatures = set()
        allowed_slots = 0
        for slot in physical_slots_for_box(instance, boxes[bid]):
            if base is not None and base.forbids(bid, slot):
                continue
            footprint = cells_of_slot(slot)
            if occupied.intersection(footprint):
                continue
            allowed_slots += 1
            signatures.add(_signature(footprint, cell_covers))
        if not signatures:
            raise InfeasibleInstruction(f'{bid} has no instruction-allowed unoccupied physical footprint')
        signatures = sorted(signatures, key=repr)
        signature_counts[bid] = len(signatures)
        physical_slot_counts[bid] = allowed_slots
        variables = []
        pod = str(boxes[bid].pod)
        for number, signature in enumerate(signatures):
            var = model.NewBoolVar(f'load_{index}_signature_{number}')
            variables.append(var)
            choices[bid, signature] = var
            for cover, count in signature:
                capacity_terms[cover].append(count*var)
                # A long footprint crossing two physical covers must agree
                # with BOTH selectors, not merely its addressed even bay.
                if cover is not None:
                    model.AddImplication(var, selectors[cover, pod])
        model.AddExactlyOne(variables)
    for cover, terms in capacity_terms.items():
        model.Add(sum(terms) <= capacities[cover])

    admission = wait_for_capacity()
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = seed
    solver.parameters.max_time_in_seconds = SOLVE_LIMIT_S
    solve_began = perf_counter()
    status = solver.Solve(model)
    solve_s = perf_counter()-solve_began
    if status == cp_model.INFEASIBLE:
        raise InfeasibleInstruction('strict physical-hatch aggregate capacity: INFEASIBLE '
                                    '(necessary instruction-completion screen)')
    if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        raise InfeasibleInstruction('strict physical-hatch aggregate allocation unavailable: '
                                    f'{solver.StatusName(status)}; no infeasibility proof')
    allocation = {cover: next(pod for pod in ports if solver.BooleanValue(selectors[cover, pod]))
                  for cover in covers}
    compiled = HatchInstructionConstraint.from_compiled(
        compile_one_port_per_hatch(instance, allocation))
    witness = [{
        'box': bid, 'pod': str(boxes[bid].pod),
        'cover_cell_counts': [{'cover': cover, 'cells': count} for cover, count in signature],
    } for (bid, signature), var in choices.items() if solver.BooleanValue(var)]
    metadata = dict(
        method='physical footprint cover/POD selectors with aggregate cell-capacity relaxation',
        status=solver.StatusName(status), cover_ports=allocation,
        retained_cover_ports=fixed_cover,
        available_cells=[{'cover': cover, 'cells': count} for cover, count in
                         sorted(capacities.items(), key=lambda item: (item[0] is not None, item[0] or ''))],
        aggregate_witness=witness, signature_counts=signature_counts,
        physical_slot_counts=physical_slot_counts, necessary_relaxation=True,
        aggregate_capacity_witness=True, static_packing_witness=False,
        checker_certificate=False, shared_covers=False, workers=1, seed=seed,
        base_scope='load allocation choices only; caller separately retains and audits base instructions',
        instruction_audit_scope='placement, physical cover/POD occupancy, replay and fixed retained cargo; '
                                'load completion is independently checked at T3',
        solve_limit_s=SOLVE_LIMIT_S, solve_s=solve_s, wall_s=perf_counter()-began,
        admission=admission,
        limitations='Signatures do not select disjoint slots or solve support, mass, balance, DG, '
                    'reefer, OOG or full physics. A feasible aggregate witness may have no actual '
                    'packing. The fixed mapping is conservative; Frontier and the original-instance '
                    'full checker decide whether a plan can be released.')
    return compiled, metadata
