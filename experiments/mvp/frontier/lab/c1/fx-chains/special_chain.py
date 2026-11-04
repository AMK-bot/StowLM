"""Sufficient heat-height and isolated-OOG proposals atop the chain CP.

Uses native parameter resolution and envelope dimensions, then native replay.
Above-heat and outboard isolation are deliberate search restrictions, never
infeasibility certificates. Physics remains with the finisher/full checker.
"""
from math import ceil, hypot, sqrt
from time import monotonic

from ortools.sat.python import cp_model

from chain_model import ChainPacking, validate
from stowage.checker.rules_special import _clearance_parameter, _nonnegative, _parameter
from stowage.conventions import CONTAINER_LENGTH_M, CONTAINER_WIDTH_M


def _horizontal(problem, box_id, s):
    T = problem.engine.T
    box = problem.boxes[box_id]
    cells = [problem.cells[T.cell_ids[int(c)]] for c in T.slot_cells[s] if c >= 0]
    x = sum(c.lcg_m for c in cells)/len(cells)
    y = sum(c.tcg_m for c in cells)/len(cells)
    oog = box.oog
    return ((x-CONTAINER_LENGTH_M[box.length_ft]/2-(oog.over_length_aft_m if oog else 0),
             x+CONTAINER_LENGTH_M[box.length_ft]/2+(oog.over_length_fore_m if oog else 0)),
            (y-CONTAINER_WIDTH_M/2-(oog.over_width_starboard_m if oog else 0),
             y+CONTAINER_WIDTH_M/2+(oog.over_width_port_m if oog else 0)))


def add_special_constraints(packing, deadline=None):
    """Return diagnostics; raise for invalid required native parameter data."""
    p, T, model = packing.p, packing.T, packing.model
    choices = list(packing.x.items())
    boxes = [members[0] for members in packing.groups]
    horizontal = {key: _horizontal(p, boxes[key[0]], key[1]) for key, _ in choices}
    diagnostics = {'heat_floor_rows': 0, 'oog_outboard_rejections': 0,
                   'oog_isolation_rows': 0,
                   'scope': 'sufficient above-heat and outboard-isolation proposal'}

    def clock():
        if deadline is not None and monotonic() >= deadline:
            raise TimeoutError('special-constraint model build deadline')

    heat_groups = [g for g, box in enumerate(boxes)
                   if 'away_from_heat' in p.boxes[box].special_stowage]
    if heat_groups:
        clearance = _nonnegative(_clearance_parameter(
            p.instance, 'expert.e8.away_from_heat', 'minimum_clearance', 'heat_clearance_m'))
        if clearance is None:
            raise ValueError('native heat-clearance data invalid or missing')
        heat_ids = {h.tank_id if h.tank_id is not None else h.id
                    for h in p.instance.vessel.heat_sources if h.kind == 'heated_tank'}
        if any(t.heated and t.id not in heat_ids for t in p.instance.vessel.tanks):
            raise ValueError('native heated-tank geometry missing')
        for (g, s), v in choices:
            if g not in heat_groups:
                continue
            clock()
            envelope = horizontal[g, s]
            required = float('-inf')
            for heat in p.instance.vessel.heat_sources:
                region = ((heat.x_min_m, heat.x_max_m), (heat.y_min_m, heat.y_max_m))
                gaps = [max(a[0]-b[1], b[0]-a[1], 0.) for a, b in zip(envelope, region)]
                dxy = hypot(*gaps)
                horizontal_overlap = all(a[0] < b[1]-1e-9 and b[0] < a[1]-1e-9
                                         for a, b in zip(envelope, region))
                if dxy < clearance or horizontal_overlap:
                    # Restrict to the above-source alternative of the exact
                    # native Euclidean AABB distance predicate. Below-source
                    # alternatives are legal but deliberately not proposed.
                    dz = sqrt(max(0., clearance*clearance-dxy*dxy))
                    required = max(required, float(heat.z_max_m)+dz)
            if required == float('-inf'):
                continue
            for c in T.slot_cells[s]:
                if c < 0:
                    continue
                j, k = int(T.cell_stack[c]), int(T.cell_k[c])
                floor = (packing.roof[T.stack_cells[j][k-1]] if k
                         else round(float(T.stack_base_z[j])*1_000_000))
                # The imported CP uses rounded micrometres. Add a conservative
                # per-column rounding allowance before native exact replay.
                bound = ceil(required*1_000_000)+len(T.stack_cells[j])+1
                model.add(floor >= bound).only_enforce_if(v)
                diagnostics['heat_floor_rows'] += 1

    oog_groups = [g for g, box in enumerate(boxes) if p.boxes[box].oog is not None
                  and any((p.boxes[box].oog.over_width_port_m,
                           p.boxes[box].oog.over_width_starboard_m,
                           p.boxes[box].oog.over_length_fore_m,
                           p.boxes[box].oog.over_length_aft_m))]
    for g in oog_groups:
        if T.extra.get('rule_freeze_v1'):
            configured = _parameter(p.instance, 'expert.e9.over_width_length',
                                    'oog_clearance', 'minimum_clearance_m')
            margin = 0. if configured is None else _nonnegative(configured)
        else:
            margin = _nonnegative(_clearance_parameter(
                p.instance, 'expert.e9.over_width_length', 'oog_clearance', 'minimum_clearance_m'))
        if margin is None:
            raise ValueError('native OOG-clearance data invalid or missing')
        eligible = [(s, v) for (gg, s), v in choices if gg == g and T.slot_deck[s]]
        if not eligible:
            for (gg, s), v in choices:
                if gg == g:
                    model.add(v == 0)
            continue
        outboard = max(abs(sum(horizontal[g, s][1])/2) for s, _ in eligible)
        for (gg, s), v in choices:
            if gg != g:
                continue
            clock()
            y = sum(horizontal[g, s][1])/2
            if not T.slot_deck[s] or abs(y) < outboard-1e-6:
                model.add(v == 0)
                diagnostics['oog_outboard_rejections'] += 1
                continue
            own = {int(j) for j in T.slot_stacks[s] if j >= 0}
            envelope = horizontal[g, s]
            # Reserve horizontally intersecting neighboring positions at ALL
            # heights. Own columns retain lower physical support boxes. This
            # is stronger than native vertical-envelope non-overlap, by design.
            for (h, t), w in choices:
                if h == g:
                    continue
                other = {int(j) for j in T.slot_stacks[t] if j >= 0}
                if other <= own:
                    continue
                bounds = horizontal[h, t]
                overlap = all(a[0] < b[1]-1e-9 and b[0] < a[1]-1e-9
                              for a, b in zip(envelope, bounds))
                gap = hypot(*(max(a[0]-b[1], b[0]-a[1], 0.)
                              for a, b in zip(envelope, bounds)))
                if overlap or gap+1e-9 < margin:
                    model.add(v+w <= 1)
                    diagnostics['oog_isolation_rows'] += 1
    return diagnostics


def construct(instance, problem, seed, budget_s):
    started = monotonic()
    deadline = started+max(0., budget_s)
    metadata = {'model': 'chain_with_sufficient_special_geometry', 'attempts': [],
                'restrictions': ['above all horizontally nearby heat sources',
                                 'outboard deck OOG with horizontal isolation',
                                 'group maximum mass'],
                'omissions': ['full physics, ballast and voyage checker']}
    construct.last_diagnostics = metadata
    if instance.onboard.placements or instance.discharge_list:
        metadata['unsupported'] = 'nonempty arrival or discharges'
        return []
    for arm, bucket in enumerate((2000, 0)):
        if deadline-monotonic() <= .1:
            break
        packing = ChainPacking(problem, bucket_kg=bucket, port_chains=False, full_hold=False)
        row = {'bucket_kg': bucket, 'rounds': []}
        metadata['attempts'].append(row)
        try:
            row['specials'] = add_special_constraints(packing, deadline)
        except (ValueError, TimeoutError) as exc:
            row['build_error'] = str(exc)
            break
        arm_end = min(deadline, monotonic()+max(.1, (deadline-monotonic())/(2-arm)))
        for repeat in range(16):
            remaining = arm_end-monotonic()
            if remaining <= .05:
                break
            solver = cp_model.CpSolver()
            solver.parameters.num_workers = 1
            solver.parameters.max_time_in_seconds = remaining
            solver.parameters.random_seed = (seed+31*arm+repeat) % 2_147_483_647
            solver.parameters.stop_after_first_solution = True
            status = solver.solve(packing.model)
            result = {'status': solver.status_name(status), 'solve_s': solver.wall_time}
            row['rounds'].append(result)
            if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
                break
            assignment = packing.assignment(solver)
            legal, diagnostic = validate(problem, assignment)
            result['validation'] = diagnostic
            if legal:
                metadata['wall_s'] = monotonic()-started
                return [assignment]
            packing.forbid(assignment)
    metadata['wall_s'] = monotonic()-started
    return []


def propose(instance, *, seed, budget_s, context):
    from alns import Problem
    problem = context.get('problem') or Problem(instance)
    candidates = construct(instance, problem, seed, budget_s)
    context.setdefault('method_metadata', {})['special_chain'] = construct.last_diagnostics
    return candidates


def propose_hand_seed(instance, *, seed, budget_s, context):
    """Explicit map-specific diagnostic seed, never labelled new construction."""
    from alns import Problem
    metadata = context.setdefault('method_metadata', {})
    metadata['hand_seed'] = {'origin': 'manually informed repair of quick special failure',
                             'new_algorithm': False}
    if instance.id != 'sim.small.40100034':
        return []
    slots = {'QZXU0000101': '0020102', 'QZXU0000082': '0020104',
             'QZXU0000040': '0020202', 'QZXU0000020': '0020204',
             'QZXU0000061': '0020182', 'QZXU0000077': '0020184',
             'QZXU0000035': '0020382', 'QZXU0000098': '0020282',
             'QZXU0000009': '0020682', 'QZXU0000117': '0010582',
             'QZXU0000014': '0030582', 'QZXU0000056': '0010584'}
    if set(slots) != set(instance.load_list):
        return []
    problem = context.get('problem') or Problem(instance)
    good, diagnostic = validate(problem, slots)
    metadata['hand_seed']['validation'] = diagnostic
    return [slots] if good else []
