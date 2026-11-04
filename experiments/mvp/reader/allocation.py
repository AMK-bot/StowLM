"""Instance-only strict physical-hatch POD allocation.

Reuses Frontier's static master, not its search or checker. The resulting
packing is an allocation screen, never a checked stow plan. No port sharing or
capacity relaxation is permitted when the strict model is infeasible.
"""
from __future__ import annotations

from collections import defaultdict
from time import monotonic

from ortools.sat.python import cp_model

from experiments.mvp.frontier.alns import Problem
from experiments.mvp.frontier.master import MasterModel
from experiments.mvp.reader.constraints import (
    InfeasibleInstruction, compile_one_port_per_hatch, wrap_engine,
)
from stowage.conventions import cells_of_slot


def derive_hatch_allocation(instance, base=None, workers=1, seed=0):
    """Return ``(compiled_constraint, metadata)`` without language-side parameters.

    Cover selectors apply to *every* footprint cell in both hold and deck.
    Retained boxes fix their physical covers and their slots in the static
    packing model. Frontier's original mean-mass static approximations remain;
    the caller must freshly replay and fully check its actual constrained plan.
    """
    started = monotonic()
    if workers not in (1, 2):
        raise ValueError("allocation permits at most two CPU workers")
    boxes = {box.id: box for box in instance.containers}
    discharged = set(instance.discharge_list)
    retained = {p.container_id: p.slot for p in instance.onboard.placements
                if p.container_id not in discharged}
    active = list(dict.fromkeys([*instance.load_list, *retained]))
    if not active:
        raise InfeasibleInstruction("no cargo from which to derive a hatch allocation")
    port_order = {str(call.locode): call.index for call in instance.rotation.calls}
    ports = sorted({str(boxes[bid].pod) for bid in active},
                   key=lambda p: (port_order.get(p, float('inf')), p))
    # This preliminary compilation only obtains authoritative geometry. Retained
    # PODs are inspected before compiling their eventual strict assignment.
    geometry_instance = instance.model_copy(update={"onboard": instance.onboard.model_copy(update={"placements": []})})
    geometry = compile_one_port_per_hatch(geometry_instance,
        {cover.id: ports[0] for cover in instance.vessel.hatch_covers})
    fixed_cover = {}
    for bid, slot in retained.items():
        if base is not None and base.forbids(bid, slot):
            raise InfeasibleInstruction("retained cargo violates existing instruction")
        for cell in cells_of_slot(slot):
            cover = geometry.cell_covers.get(cell)
            if cover is None:
                continue
            pod = str(boxes[bid].pod)
            if cover in fixed_cover and fixed_cover[cover] != pod:
                raise InfeasibleInstruction("retained cargo mixes PODs on a physical hatch")
            fixed_cover[cover] = pod
    problem = Problem(instance)
    if base is not None:
        problem.engine = wrap_engine(problem.engine, base)
    # Reusing Frontier's static master retains its support, mixed-length,
    # paired-cell, group-static compatibility and balance objective logic.
    master = MasterModel(problem, boxes=active, keep_heavy_in_hold=False)
    master.port_rank = {port_order[pod]: i + 1 for i, pod in enumerate(ports)}
    master.K = len(ports)
    model, x, scale = master._build()
    T = master.T
    covers = sorted(cover.id for cover in instance.vessel.hatch_covers)
    selectors = {(cover, pod): model.NewBoolVar(f'reader_cover_{i}_{j}')
                 for i, cover in enumerate(covers) for j, pod in enumerate(ports)}
    for cover in covers:
        model.AddExactlyOne(selectors[cover, pod] for pod in ports)
        if cover in fixed_cover:
            model.Add(selectors[cover, fixed_cover[cover]] == 1)
    for (group, slot), var in x.items():
        pod = str(boxes[master.groups[group][0]].pod)
        for cell in cells_of_slot(T.slot_ids[slot]):
            cover = geometry.cell_covers.get(cell)
            if cover is not None:
                model.AddImplication(var, selectors[cover, pod])
    group_for_box = {bid: group for group, members in enumerate(master.groups) for bid in members}
    slot_index = {slot: i for i, slot in enumerate(T.slot_ids)}
    for bid, slot in retained.items():
        key = (group_for_box[bid], slot_index.get(slot))
        if key not in x:
            raise InfeasibleInstruction("retained cargo has no compatible static slot")
        model.Add(x[key] == 1)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = workers
    solver.parameters.random_seed = seed
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise InfeasibleInstruction(f"strict physical-hatch allocation: {solver.StatusName(status)}")
    allocation = {cover: next(pod for pod in ports if solver.BooleanValue(selectors[cover, pod]))
                  for cover in covers}
    compiled = compile_one_port_per_hatch(instance, allocation, base=base)
    chosen = defaultdict(list)
    for (group, slot), var in x.items():
        if solver.BooleanValue(var):
            chosen[group].append(T.slot_ids[slot])
    witness = dict(retained)
    for group, slots in chosen.items():
        used = {retained[bid] for bid in master.groups[group] if bid in retained}
        members = sorted((bid for bid in master.groups[group] if bid not in retained),
                         key=lambda bid: (-boxes[bid].gross_mass_kg, bid))
        for bid, slot in zip(members, sorted(set(slots) - used)):
            witness[bid] = slot
    errors = compiled.audit_positions(witness)
    if errors or set(witness) != set(active):
        raise InfeasibleInstruction(f"allocation witness audit failed: {errors}")
    metadata = dict(method="strict physical-cover selectors on unchanged Frontier static master",
                    status=solver.StatusName(status), cover_ports=allocation,
                    retained_cover_ports=fixed_cover, packing_witness=witness,
                    static_objective=solver.ObjectiveValue()/scale,
                    static_packing_witness=True, checker_certificate=False,
                    shared_covers=False, workers=workers, seed=seed,
                    wall_s=monotonic()-started,
                    limitations="Frontier group mean mass; no DG-pair/full-physics certificate. Actual plan needs full checker.")
    return compiled, metadata
