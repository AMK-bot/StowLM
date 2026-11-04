"""Joint binary cargo assignment and continuous ballast first-incumbent model.

A binary x[box,slot] is one exactly when that box occupies that slot. Summing
these choices gives cell occupancy, stack mass, height and weight-class ranks;
this is linearisation: expressing discrete choices as sums and bounded
implications. SCIP solves the mixed integer linear model. The 3.7 physics
adapter supplies conservatively margined rows at all D57 states.

Exact checker witnesses supply lazy constraints: a DG-incompatible selected
pair is forbidden; a rejected whole stow receives a no-good excluding just
that assignment. A lazy constraint is a rule added when a candidate reveals
its violation, avoiding enumeration of every possible pair in advance.
Acceptance always requires the full checker and production-mask replay.

APPROXIMATION: the objective uses tier/POD and original-yard retrieval order
as cheap move proxies, not the D11 move count. Physics and securing proxies
are labelled in construct_physics; none can justify infeasibility. The separate
certificate LP deliberately omits physics, securing, support, stack and special
rules: infeasibility of this weaker exact capacity relaxation proves that even
an unrestricted assignment cannot load all cargo. A feasible relaxation proves
nothing about feasibility of the full problem.

Sources: Pacino, Delgado, Jensen & Bebbington (2011), Fast generation of
near-optimal plans for eco-efficient stowage of large container vessels;
https://developers.google.com/optimization/mip/mip_example and
https://developers.google.com/optimization/cp/cp_solver .
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import math
from time import monotonic
from typing import Any

from ortools.linear_solver import pywraplp

from stowage.actions import get_action_space
from stowage.actions.space import Discharge, Place
from stowage.conventions import cells_of_slot, parse_slot
from stowage.frozen import current_provenance
from stowage.model import CheckReport, Instance, MovePlan, Placement, PlannerResult

VERSION = "0.1.0"


@dataclass
class ConstructResult:
    status: str
    actions: tuple = ()
    plan: MovePlan | None = None
    report: CheckReport | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    certificate: str | None = None


@dataclass
class AssignmentModel:
    solver: Any
    x: dict
    tables: Any
    occupied: dict
    physics: Any = None


def _build_assignment_model(instance, *, seed=0, num_workers=1, physics=True, deadline=None):
    """Exact static assignment rows; nonlinear/local rules are separated lazily."""
    import numpy as np
    from stowage.feasibility.tables import build_tables

    def clock():
        if deadline is not None and monotonic() >= deadline:
            raise TimeoutError("construct model build budget exhausted")
    clock()
    t = build_tables(instance)
    clock()
    s = pywraplp.Solver.CreateSolver("SCIP")
    if s is None:
        raise RuntimeError("OR-Tools SCIP backend unavailable")
    s.SetNumThreads(num_workers)
    s.SetSolverSpecificParametersAsString(
        f"randomization/randomseedshift = {int(seed) % 2147483647}\n"
        "limits/solutions = 1\n")
    boxes = {b.id: b for b in instance.containers}
    load = set(instance.load_list)
    fixed = [p for p in instance.onboard.placements if p.container_id not in instance.discharge_list]
    fixed_ids = {p.container_id for p in fixed}
    load -= fixed_ids
    x = {(cid, t.slot_ids[si]): s.BoolVar(f"x_{bi}_{si}")
         for bi, cid in enumerate(t.box_ids) if cid in load
         for si in np.flatnonzero(t.static[bi])}
    by_box, by_cell = defaultdict(list), defaultdict(list)
    for (cid, slot), var in x.items():
        clock()
        by_box[cid].append(var)
        for cell in cells_of_slot(slot):
            by_cell[cell].append((cid, slot, var))
    for cid in sorted(load):
        s.Add(s.Sum(by_box[cid]) == 1, f"load_{cid}")
    fixed_cell = {}
    for p in fixed:
        for cell in cells_of_slot(p.slot):
            if cell in fixed_cell:
                s.Add(s.NumVar(0, 0, "invalid_fixed") == 1)
            fixed_cell[cell] = p
    occ, mass, height, ranks, heavy, long, close = {}, {}, {}, {}, {}, {}, {}
    for cell in t.cell_ids:
        clock()
        records = by_cell[cell]
        f = fixed_cell.get(cell)
        fb = boxes[f.container_id] if f else None
        occ[cell] = s.BoolVar(f"occ_{cell}")
        s.Add(occ[cell] == s.Sum(v for _, _, v in records) + int(f is not None))
        mass[cell] = s.Sum(boxes[c].gross_mass_kg / len(cells_of_slot(sl)) * v for c, sl, v in records)
        height[cell] = s.Sum((boxes[c].height_m + (boxes[c].oog.over_height_m if boxes[c].oog else 0)) * v for c, _, v in records)
        ranks[cell] = s.Sum(max(0, int(t.rank[t.box_index[c]])) * v for c, _, v in records)
        heavy[cell] = s.Sum(int(t.e1_threshold is not None and boxes[c].gross_mass_kg >= t.e1_threshold) * v for c, _, v in records)
        long[cell] = s.Sum(int(boxes[c].length_ft != 20) * v for c, _, v in records)
        close[cell] = s.Sum(int(t.closes_stack[t.box_index[c]]) * v for c, _, v in records)
        if fb:
            mass[cell] += fb.gross_mass_kg / len(cells_of_slot(f.slot))
            height[cell] += fb.height_m + (fb.oog.over_height_m if fb.oog else 0)
            ranks[cell] += max(0, int(t.rank[t.box_index[fb.id]]))
            heavy[cell] += int(t.e1_threshold is not None and fb.gross_mass_kg >= t.e1_threshold)
            long[cell] += int(fb.length_ft != 20)
            close[cell] += int(t.closes_stack[t.box_index[fb.id]])
    floors = {}
    for j, sid in enumerate(t.stack_ids):
        ids = [t.cell_ids[int(ci)] for ci in t.stack_cells[j]]
        for k, cell in enumerate(ids):
            floors[cell] = float(t.stack_base_z[j]) + s.Sum(height[below] for below in ids[:k])
            if k:
                lower = ids[k-1]
                s.Add(occ[cell] <= occ[lower])
                for below in (ids[:k] if t.e2_tol else [lower]):
                    s.Add(ranks[cell] <= ranks[below] + t.e2_tol + 3 * (1-occ[cell]))
                s.Add(heavy[cell] <= heavy[lower] + 1-occ[cell])
                s.Add(long[cell] >= long[lower] - (1-occ[cell]))
                s.Add(occ[cell] + close[lower] <= 1)
                if not t.stack_deck[j]:
                    # Exact securing geometry: no mixed 20/long hold columns.
                    s.Add(long[cell] <= long[lower] + 1-occ[cell])
        total_mass = s.Sum(mass[c] for c in ids)
        total_height = s.Sum(height[c] for c in ids)
        use20 = s.BoolVar(f"use20_{j}")
        tw = [occ[c]-long[c] for c in ids]
        for v in tw:
            s.Add(use20 >= v)
        s.Add(use20 <= s.Sum(tw))
        s.Add(total_mass <= float(t.stack_lim_40[j]) +
              (float(t.stack_lim_20[j])-float(t.stack_lim_40[j]))*use20)
        s.Add(total_height <= float(t.stack_hmax[j]))
        cap = float(t.extra['stack_visibility_top'][j])
        if math.isfinite(cap):
            s.Add(total_height + float(t.stack_base_z[j]) <= cap)
    for (cid, slot), var in x.items():
        footprint = cells_of_slot(slot)
        if len(footprint) == 2:
            delta = floors[footprint[0]] - floors[footprint[1]]
            j1, j2 = (int(t.cell_stack[t.cell_index[c]]) for c in footprint)
            # Valid column-height bounds give a tight implication constant;
            # summing heights of the whole manifest makes this LP far weaker.
            plus = max(0., float(t.stack_base_z[j1]+t.stack_hmax[j1]-t.stack_base_z[j2]))
            minus = max(0., float(t.stack_base_z[j2]+t.stack_hmax[j2]-t.stack_base_z[j1]))
            s.Add(delta <= 1e-6 + plus*(1-var))
            s.Add(delta >= -1e-6 - minus*(1-var))
    # APPROXIMATION: independent linear move surrogate. Larger PODs prefer
    # lower tiers; original yard depth prefers later loading/higher tiers.
    depth = {cid: len(st.boxes)-k-1 for st in instance.yard.stacks for k, cid in enumerate(st.boxes)}
    maxpod = max((call.index for call in instance.rotation.calls), default=1)
    for (cid, slot), var in x.items():
        b = boxes[cid]
        pod = min((c.index for c in instance.rotation.calls if c.index > instance.call_index and c.locode == b.pod), default=maxpod)
        si = t.slot_index[slot]
        tier = max(int(k) for k in t.slot_k[si]) + 1
        cost = 100*int(t.slot_deck[si]) + tier*(1+pod) - .05*tier*depth.get(cid, 0)
        s.Objective().SetCoefficient(var, cost)
    s.Objective().SetMinimization()
    model = AssignmentModel(s, x, t, occ)
    if physics:
        from stowage.solver.construct_physics import add_construct_physics
        model.physics = add_construct_physics(s, instance, x, mode="conservative", deadline=deadline)
    return model


def _checker_mass_rating():
    """Read the checker's unary rating, declining proof if its source changes.

    This limit is currently a checker-owned synthetic constant, not an instance
    parameter. Recognise its exact unconditional per-load guard rather than
    duplicate a numeric rating here. The returned source digest pins evidence.
    """
    import ast
    import hashlib
    import inspect
    from stowage.checker.lashing import _check_lashing
    source = inspect.getsource(_check_lashing)
    tree = ast.parse(source)
    function = tree.body[0]
    helper = next((node for node in function.body
                   if isinstance(node, ast.FunctionDef) and node.name == 'problem'), None)
    if not (helper is not None and len(helper.body) == 1
            and isinstance(helper.body[0], ast.Return)
            and isinstance(helper.body[0].value, ast.Call)
            and isinstance(helper.body[0].value.func, ast.Name)
            and helper.body[0].value.func.id == 'Violation'):
        return None
    severity = next((kw.value for kw in helper.body[0].value.keywords
                     if kw.arg == 'severity'), None)
    if not (isinstance(severity, ast.Constant) and severity.value == 'hard'):
        return None
    for loop in function.body:
        if not (isinstance(loop, ast.For) and isinstance(loop.target, ast.Name)
                and loop.target.id == 'q' and ast.unparse(loop.iter) == 'loads.values()'):
            continue
        for guard in loop.body:
            if not (isinstance(guard, ast.If) and isinstance(guard.test, ast.Compare)
                    and ast.unparse(guard.test.left) == 'q.container.gross_mass_kg'
                    and len(guard.test.ops) == 1 and isinstance(guard.test.ops[0], ast.Gt)
                    and len(guard.test.comparators) == 1
                    and isinstance(guard.test.comparators[0], ast.Constant)):
                continue
            limit = guard.test.comparators[0].value
            if not isinstance(limit, (int,float)) or not math.isfinite(limit) or limit <= 0:
                continue
            if not (len(guard.body) == 1 and isinstance(guard.body[0], ast.Expr)
                    and isinstance(guard.body[0].value, ast.Call)
                    and ast.unparse(guard.body[0].value.func) == 'violations.append'
                    and len(guard.body[0].value.args) == 1):
                continue
            for call in guard.body[0].value.args:
                if not (isinstance(call,ast.Call) and isinstance(call.func,ast.Name)
                        and call.func.id == 'problem' and call.args
                        and isinstance(call.args[0],ast.Constant)
                        and isinstance(call.args[0].value,str)
                        and 'mass rating' in call.args[0].value
                        and len(call.args) == 2 and ast.unparse(call.args[1]) == 'RULE'):
                    continue
                declared = {kw.arg:kw.value for kw in call.keywords}
                if ('limit' in declared and 'measured' in declared and isinstance(declared['limit'],ast.Constant)
                        and declared['limit'].value == limit
                        and ast.unparse(declared.get('measured')) == 'q.container.gross_mass_kg'):
                    return float(limit), hashlib.sha256(source.encode()).hexdigest()
    return None


def _unary_certificate(instance):
    """Exact D11/checker-model contradiction, independent of ballast or slots."""
    try:
        rating = _checker_mass_rating()
    except (OSError, TypeError, SyntaxError, ValueError):
        return None
    if rating is None:
        return None
    limit, digest = rating
    required = set(instance.load_list) | {
        p.container_id for p in instance.onboard.placements
        if p.container_id not in instance.discharge_list}
    violations = [(b.id,float(b.gross_mass_kg)) for b in instance.containers
                  if b.id in required and b.gross_mass_kg > limit]
    if not violations:
        return None
    return ("Instance infeasible under the current synthetic checker model: D11 requires all manifest cargo, "
            "but checker.lashing._check_lashing applies its unconditional per-container mass rating "
            f"{limit:g} kg to every load in its completed cargo graph. Required cargo exceeding it: "
            + ', '.join(f'{cid}={mass:g} kg' for cid,mass in violations)
            + ". Changing slot, stack, ballast or D57 consumables cannot change gross mass. "
            "All other constraints relaxed; no empirical physics or securing proxy used. "
            f"Checker source SHA256={digest}. This is a synthetic-model instance defect, not a claim of physical impossibility.")


def _capacity_certificate(instance, *, time_limit_s=2.0):
    """Sound weak LP, independent of empirical physics margins.

    Retain exact footprint/length acceptance, reefer plugs and cell capacity.
    Ignore every onboard box, and relax assignments to [0,1]. These changes
    enlarge feasible space, never shrink it.
    """
    from stowage.feasibility.tables import build_tables
    t = build_tables(instance)
    solver = pywraplp.Solver.CreateSolver("GLOP")
    solver.SetTimeLimit(max(1, int(time_limit_s*1000)))
    cells = {c.id: c for c in instance.vessel.cells}
    boxes = {b.id:b for b in instance.containers}
    variables, occupied = defaultdict(list), defaultdict(list)
    for cid in instance.load_list:
        b = boxes[cid]
        for slot in t.slot_ids:
            footprint = cells_of_slot(slot)
            if ((b.length_ft == 20) != (len(footprint) == 1)
                or not all(getattr(cells[c], f'accepts_{b.length_ft}') for c in footprint)
                or (b.reefer and not any(cells[c].reefer_plug for c in footprint))):
                continue
            v = solver.NumVar(0, 1, f"r_{cid}_{slot}")
            variables[cid].append(v)
            for cell in footprint:
                occupied[cell].append(v)
        solver.Add(solver.Sum(variables[cid]) == 1)
    for vs in occupied.values():
        solver.Add(solver.Sum(vs) <= 1)
    if solver.Solve() != pywraplp.Solver.INFEASIBLE:
        return None
    return ("Instance infeasible: exact footprint/20-40-45 acceptance, reefer-plug and physical-cell-capacity LP is infeasible. "
            "Relaxed: all integrality, all retained/background onboard occupancy, support geometry, stack mass/height, "
            "weight order, crane/port limits, special/OOG rules, IMDG, E6, visibility, securing, "
            "all stability/strength and D57 physics, yard/move sequencing and instructions. No D49 empirical row used.")


def _selected(model):
    return {cid:slot for (cid,slot),v in model.x.items() if v.solution_value() > .5}


def _nogood(model, assignment, *, container_ids=None):
    keys = [(cid, slot) for cid, slot in assignment.items()
            if container_ids is None or cid in container_ids]
    if not keys:
        return False
    model.solver.Add(model.solver.Sum(model.x[k] for k in keys) <= len(keys)-1)
    return True


class SequenceFailure(ValueError):
    """Retain a concrete rejected prefix for model-gap attribution."""
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def _sequence(instance, assignment, deadline, *, replay_mask=True):
    """Ready placements obey physical support; yard-accessible boxes break ties."""
    from stowage.actions.expander import Expander
    from stowage.feasibility import make_feasibility_engine
    space = get_action_space("boxslot")
    ex = Expander(instance)
    engine = make_feasibility_engine(instance, space) if replay_mask else None
    actions = []
    for cid in sorted(instance.discharge_list, key=lambda c: ex.where.get(c, ('',''))[1], reverse=True):
        a = Discharge(cid)
        if engine:
            engine.push(a)
        ex.step(a)
        actions.append(a)
    pending = dict(assignment)
    retained = {p.container_id:p.slot for p in instance.onboard.placements if p.container_id not in instance.discharge_list}
    for cid, slot in retained.items():
        if ex.where.get(cid, (None,))[0] == 'quay':
            pending[cid] = slot
    while pending:
        if monotonic() >= deadline:
            raise TimeoutError("construct mask replay budget exhausted")
        candidates = []
        for cid, slot in pending.items():
            footprint = cells_of_slot(slot)
            if any(c in ex.occ for c in footprint):
                continue
            if any(any(c not in ex.occ for c in ex.stacks[ex.cells[cell].stack_id].cell_ids[:ex.cell_pos[cell][1]]) for cell in footprint):
                continue
            loc, key = ex.where.get(cid, (None,None))
            blockers = len(ex.yard[key])-ex.yard[key].index(cid)-1 if loc == 'yard' else 0
            a = parse_slot(slot)
            candidates.append(((a.on_deck, blockers, a.tier, slot, cid), Place(cid,slot)))
        for _, action in sorted(candidates):
            if engine is not None and not engine.is_legal(action):
                continue
            trial = ex.copy()
            try:
                trial.step(action)
            except ValueError:
                continue
            if engine is not None:
                engine.push(action)
            ex = trial
            for cid, slot in retained.items():
                if ex.where.get(cid, (None,))[0] == 'quay':
                    pending.setdefault(cid, slot)
            actions.append(action)
            del pending[action.box]
            break
        else:
            details = {'prefix_actions':[space.encode(a) for a in actions],
                       'pending':pending, 'ready':[space.encode(a) for _,a in sorted(candidates)]}
            if engine is not None:
                completion = getattr(engine, 'completion_check', None)
                if callable(completion):
                    details['completion'] = completion()
            raise SequenceFailure(
                f"assignment has no {'production-mask-compatible ' if engine else ''}ready placement "
                f"after {len(actions)} actions ({len(pending)} pending)", details)
    if engine is not None and not engine.is_complete():
        raise ValueError("construct mask replay incomplete")
    plan = space.expand(instance, actions)
    return tuple(actions), plan


def construct(instance: Instance, *, time_limit_s=60.0, seed=0, num_workers=1,
              max_rounds=12, replay_mask=True) -> ConstructResult:
    """Return checker-passing complete incumbents, or explicit non-proof."""
    from stowage.checker import check
    from stowage.checker.rules import check_static_rules
    from stowage.solver.ballast import finish_ballast
    if not math.isfinite(time_limit_s) or time_limit_s < 0:
        raise ValueError("construct time_limit_s must be finite and nonnegative")
    started = monotonic()
    deadline = started + time_limit_s
    stats = {'rounds':0, 'cuts':0, 'checker_passed':False,
             'mask_replay_passed':False, 'finisher_found':False, 'num_workers':num_workers, 'rejections':[],
             'approximations':[], 'certificate_eligible':False}
    if num_workers < 1 or num_workers > 8:
        raise ValueError("construct num_workers must be in 1..8")
    def result(status, **kw):
        stats['construct_time_s'] = monotonic()-started
        return ConstructResult(status=status, stats=stats, **kw)
    if time_limit_s <= 0:
        return result('timeout')
    certificate = _unary_certificate(instance)
    if certificate:
        stats['infeasibility_certificate'] = certificate
        stats['certificate_kind'] = 'exact_checker_unary_mass'
        stats['attribution'] = 'instance infeasible under synthetic checker model'
        return result('infeasible', certificate=certificate)
    try:
        model = _build_assignment_model(instance, seed=seed, num_workers=num_workers, deadline=deadline)
    except (ValueError, RuntimeError, KeyError, TimeoutError) as error:
        stats['model_error'] = str(error)
        if isinstance(error, TimeoutError):
            stats['attribution'] = 'budget; model construction deadline'
            return result('timeout')
        if any(word in str(error).lower() for word in ('qualif', 'policy epoch', 'linear policy')):
            stats['attribution'] = 'required linear qualification unavailable'
        else:
            stats['attribution'] = 'required model data unavailable'
        return result('not_found')
    stats.update(binary_variables=len(model.x), constraints=model.solver.NumConstraints(),
                 model_build_s=monotonic()-started,
                 physics_rows=list(getattr(model.physics, 'row_names', ())),
                 approximations=list(getattr(model.physics, 'approximations', ())),
                 certificate_blockers=list(getattr(model.physics, 'certificate_blockers', ())),
                 certificate_eligible=False)
    last_report = None
    for iteration in range(max_rounds):
        left = deadline-monotonic()
        if left <= .02:
            break
        stats['rounds'] += 1
        model.solver.SetTimeLimit(max(1, int(left*.65*1000)))
        solve_started = monotonic()
        status = model.solver.Solve()
        stats['solve_time_s'] = stats.get('solve_time_s',0) + monotonic()-solve_started
        stats['mip_status'] = status
        if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
            if status == pywraplp.Solver.INFEASIBLE:
                certificate = _capacity_certificate(instance, time_limit_s=min(2, max(.01, deadline-monotonic())))
                if certificate:
                    stats['infeasibility_certificate'] = certificate
                    return result('infeasible', certificate=certificate)
                stats['attribution'] = 'conservative model gap; exact capacity relaxation not proved infeasible'
            else:
                stats['attribution'] = 'budget; no complete MIP incumbent'
            break
        assignment = _selected(model)
        fixed = [p for p in instance.onboard.placements if p.container_id not in instance.discharge_list]
        ship = instance.onboard.model_copy(update={'placements':fixed+[Placement(container_id=c,slot=s) for c,s in assignment.items()]})
        static = check_static_rules(instance, ship, call_index=instance.call_index)
        violations = [v for v in static.violations if v.severity == 'hard']
        if violations:
            stats['static_rejections'] = stats.get('static_rejections',0)+1
            stats['last_violations'] = sorted({v.rule_id for v in violations})
            stats['rejections'].append({'round':iteration, 'phase':'static',
                'violations':[v.model_dump(mode='json') for v in violations]})
            paircuts = 0
            for violation in violations:
                if violation.rule_id.startswith('imdg.') and len(violation.containers) == 2:
                    paircuts += int(_nogood(model, assignment, container_ids=set(violation.containers)))
            stats['cuts'] += paircuts or int(_nogood(model, assignment))
            continue
        try:
            actions, plan = _sequence(instance, assignment, deadline, replay_mask=replay_mask)
        except (ValueError, TimeoutError) as error:
            stats['sequence_rejections'] = stats.get('sequence_rejections',0)+1
            stats['last_sequence_error'] = str(error)
            stats['rejections'].append({'round':iteration, 'phase':'mask_sequence', 'error':str(error),
                'details':getattr(error,'details',{})})
            stats['cuts'] += int(_nogood(model, assignment))
            continue
        ballast = model.physics.ballast_condition()
        candidate = plan.model_copy(update={'ballast':ballast})
        report = check(instance, candidate)
        last_report = report
        if not report.passed and deadline-monotonic() > .1:
            outcome = finish_ballast(instance, plan, time_limit_s=max(.01,min(30.,(deadline-monotonic())*.7)), seed=seed)
            stats['finisher_status'] = outcome.status
            if outcome.plan is not None:
                candidate = outcome.plan
                report = check(instance, candidate)
                last_report = report
                stats['finisher_found'] = True
        elif report.passed:
            stats['finisher_found'] = True
            stats['finisher_status'] = 'model_ballast_passed'
        if report.passed:
            stats['checker_passed'] = True
            stats['mask_replay_passed'] = bool(replay_mask)
            stats['weighted_nonproductive'] = report.count.weighted_nonproductive if report.count else None
            return result('ok', actions=actions, plan=candidate, report=report)
        stats['last_violations'] = sorted({v.rule_id for v in report.violations if v.severity == 'hard'})
        stats['checker_rejections'] = stats.get('checker_rejections',0)+1
        stats['rejections'].append({'round':iteration, 'phase':'full_checker',
            'violations':[v.model_dump(mode='json') for v in report.violations if v.severity=='hard']})
        stats['cuts'] += int(_nogood(model, assignment))
    stats.setdefault('attribution', 'budget' if monotonic() >= deadline else 'lazy round limit/model gap')
    return result('timeout' if monotonic() >= deadline else 'not_found', report=last_report)


class ConstructivePlanner:
    """Planner-protocol wrapper; run_planner retains the final shipping gate."""
    name = "solver.construct"
    version = VERSION
    sets_ballast = True
    deterministic = False

    def __init__(self, *, num_workers=1, max_rounds=12):
        self.num_workers = num_workers
        self.max_rounds = max_rounds

    def plan(self, instance, *, seed=0, time_limit_s=None):
        outcome = construct(instance, time_limit_s=60. if time_limit_s is None else time_limit_s,
                            seed=seed, num_workers=self.num_workers, max_rounds=self.max_rounds)
        provenance = current_provenance(created_by=self.name, seed=seed).merge(instance.provenance)
        return PlannerResult(status={'not_found':'dead_end'}.get(outcome.status,outcome.status),
            plan=outcome.plan, planner=self.name, planner_version=self.version, seed=seed,
            wall_time_s=outcome.stats['construct_time_s'], metrics=outcome.stats,
            message=outcome.certificate or outcome.stats.get('attribution') or outcome.stats.get('model_error'),
            provenance=provenance)
