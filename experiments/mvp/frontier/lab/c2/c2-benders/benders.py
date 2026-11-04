"""Restricted page master with certified logic-based Benders cuts.

Imports Cycle 1 pages, without changing it. A column is one *whole* pure
20-foot stack or whole paired long stack. An infeasible securing end-chain
can exclude its exact whole-page patterns: every other chosen page uses
disjoint physical stacks, so cannot change that chain or its supporting
geometry. Other fixed-stow certificates give only a whole-stow no-good.
Unknown local ballast LPs never create feasibility cuts.

The master is restricted to generated columns. Exhausting it says nothing
about the full pure-stack family, mixed stacks, or the map. Replay legality
and the unchanged full checker are reported as separate gates.
"""
from __future__ import annotations

from collections import defaultdict
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
from time import monotonic
import warnings

sys.dont_write_bytecode = True
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
              "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "STOWAGE_IN_PORT_WORKERS"):
    os.environ[_name] = "1"

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
ROOT = LAB.parents[3]
for _path in (ROOT / "src", LAB.parent, LAB):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def _module(name, relative):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, LAB / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


pages = _module("_c2_benders_c1_pages", "c1/fx-scout/pages.py")
bound_module = _module("_c2_benders_c1_securing", "c1/fx-finish/early_bound.py")
e9fix = _module("_c2_benders_c1_e9fix", "c1/leader/e9fix.py")

from alns import Problem, Sequencer  # noqa: E402
import numpy as np  # noqa: E402
from ortools.sat.python import cp_model  # noqa: E402
from scipy.optimize import linprog, OptimizeWarning  # noqa: E402
from scipy.sparse import csc_matrix, vstack  # noqa: E402
from stowage.checker import check  # noqa: E402
from stowage.moves import apply_plan  # noqa: E402


class Model(pages.Model):
    """Cycle 1 master LP with explicit one-thread HiGHS and time budget.

    This copies only solve_lp's sparse row construction. All page pricing,
    legal-pattern checks, diving and load ordering remain the imported code.
    """

    def solve_lp(self, *, active_boxes, active_stacks, hold_full, phase):
        boxes = sorted(active_boxes, key=self.row.get)
        rows = {b: k for k, b in enumerate(boxes)}
        stacks = sorted(active_stacks)
        srow = {j: len(boxes)+k for k, j in enumerate(stacks)}
        use = [k for k, c in enumerate(self.columns)
               if k not in self.banned and all(b in rows for b in c.boxes)
               and all(j in srow for j in self.pages[c.page].stacks)]
        data, ri, ci, cost = [], [], [], []
        for col, k in enumerate(use):
            c = self.columns[k]
            for b in c.boxes:
                ri.append(rows[b]); ci.append(col); data.append(1.)
            for j in self.pages[c.page].stacks:
                ri.append(srow[j]); ci.append(col); data.append(1.)
            cost.append(self.column_cost(c, phase))
        col = len(use)
        for b in boxes:
            ri.append(rows[b]); ci.append(col); data.append(1.)
            cost.append(pages.UNCOVERED)
            col += 1
        if self.lcg_target is not None:
            cost.append(1.)
            col += 1
        matrix = csc_matrix((data, (ri, ci)), shape=(len(boxes)+len(stacks), col))
        upper, rhs = matrix[len(boxes):], np.ones(len(stacks))
        if self.lcg_target is not None:
            row = np.zeros(col)
            for index, k in enumerate(use):
                row[index] = self.lcg_moment(self.columns[k])
            row[-1] = -1.
            upper = vstack([upper, csc_matrix(row)], format="csc")
            rhs = np.append(rhs, -self.fixed_moment)
        remaining = max(.001, getattr(self, "deadline", monotonic()+30.)-monotonic())
        with warnings.catch_warnings():
            # SciPy forwards this native HiGHS option despite not listing it.
            warnings.simplefilter("ignore", OptimizeWarning)
            result = linprog(np.asarray(cost), A_eq=matrix[:len(boxes)],
                b_eq=np.ones(len(boxes)), A_ub=upper, b_ub=rhs,
                bounds=(0., None), method="highs",
                options={"threads": 1, "time_limit": remaining})
        if result.status != 0:
            raise RuntimeError(f"restricted master LP {result.status}: {result.message}")
        box_price = np.zeros(len(self.boxes))
        for b, k in rows.items():
            box_price[self.row[b]] = result.eqlin.marginals[k]
        stack_price = {j: float(result.ineqlin.marginals[k]) for k, j in enumerate(stacks)}
        self.lcg_price = float(result.ineqlin.marginals[-1]) if self.lcg_target is not None else 0.
        x = {use[k]: float(result.x[k]) for k in range(len(use)) if result.x[k] > 1e-7}
        tail = result.x[len(use):len(use)+len(boxes)]
        self.short = {b: float(v) for b, v in zip(boxes, tail) if v > 1e-6}
        return float(result.fun), x, box_price, stack_price, float(tail.sum())


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return repr(value)


def _assignment(instance, plan):
    replay = apply_plan(instance, plan)
    if not replay.legal:
        raise ValueError("input stow replay is illegal")
    wanted = set(instance.load_list)
    assignment = {q.container_id: q.slot for q in replay.final_state.ship.placements
                  if q.container_id in wanted}
    if set(assignment) != wanted:
        raise ValueError("input plan is not a complete stow")
    return assignment, replay


def _seed_patterns(model, assignment):
    """Represent an assignment by exact contiguous, pure whole-page patterns."""
    T = model.T
    by_slot = {T.slot_index[s]: T.box_index[b] for b, s in assignment.items()}
    covered, occupied, chosen = set(), set(), []
    for k, page in sorted(enumerate(model.pages), key=lambda item: item[1].kind == "20"):
        found = [(tier, by_slot[s]) for tier, s in enumerate(page.slots) if s in by_slot]
        if not found:
            continue
        if any(b in covered for _, b in found):
            continue
        if [tier for tier, _ in found] != list(range(len(found))):
            raise ValueError("seed is not a bottom-contiguous pure-page stow")
        if set(page.stacks) & occupied:
            raise ValueError("seed mixes long and 20-foot page footprints")
        boxes = tuple(b for _, b in found)
        key = (k, boxes)
        # The archived replay may violate a static mask restriction. Such a
        # seed remains available to the exact replay/checker; it cannot make a
        # pass. All newly generated columns use Model.add's ordinary checks.
        if key not in model.pool:
            model.pool[key] = len(model.columns)
            model.columns.append(pages.Pattern(k, boxes, 2.*model.restows(boxes)))
        chosen.append(model.pool[key])
        covered.update(boxes)
        occupied.update(page.stacks)
    if covered != set(model.boxes):
        raise ValueError("seed has boxes outside the pure-page family")
    return chosen


def _score_assignment(problem, assignment):
    if set(assignment) != set(problem.instance.load_list) or problem.mixes_hold_stack(assignment):
        return None
    ordered = Sequencer(problem).order(assignment)
    return problem.score(ordered) if ordered is not None else None


def _initial(model, *, seed, deadline):
    """Cycle 1 plain LP/dive constructor, without its expensive physics ladder."""
    started = monotonic()
    model.deadline = deadline
    root = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
        phase=1, deadline=started+.45*max(0., deadline-started), max_rounds=60)
    if root["uncovered"] > 3. or monotonic() >= deadline:
        return None, {"uncovered": root["uncovered"], "status": "no_initial_stow"}
    model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
        phase=3, deadline=deadline, max_rounds=20)
    chosen, info = model.dive(deadline=deadline, rng=random.Random(seed), rounds=5)
    if chosen is None:
        return None, {"uncovered": root["uncovered"], **info}
    chosen, heel = model.balance(chosen, random.Random(seed))
    assignment = model.clear_oog(model.assignment(chosen))
    scored = _score_assignment(model.p, assignment)
    return scored, {"uncovered": root["uncovered"], "heel_tm": heel, **info}


def _alternatives(model, chosen, *, deadline, max_pairs=1200, focus=()):
    """Add whole-page relocations and paired single-box swaps to the pool.

    Pair swaps add both replacement patterns together, so the restricted
    master has immediate alternatives to a rejected page without needing a
    fresh whole-map dive. Feasibility is still decided by exact replay.
    """
    initial_columns = len(model.columns)
    focus = set(focus)
    chosen = sorted(chosen, key=lambda k: k not in focus)
    for k in chosen:
        c = model.columns[k]
        page = model.pages[c.page]
        for target, other_page in enumerate(model.pages):
            if monotonic() >= deadline:
                return len(model.columns)-initial_columns
            if page.kind == other_page.kind and len(c.boxes) <= len(other_page.slots):
                model.add(target, c.boxes)
    pairs = 0
    for ia, a in enumerate(chosen):
        ca = model.columns[a]
        pa = model.pages[ca.page]
        for b in chosen[ia+1:]:
            cb = model.columns[b]
            pb = model.pages[cb.page]
            if pa.kind != pb.kind:
                continue
            for i in range(len(ca.boxes)):
                for j in range(len(cb.boxes)):
                    if monotonic() >= deadline or pairs >= max_pairs:
                        return len(model.columns)-initial_columns
                    pairs += 1
                    one, two = list(ca.boxes), list(cb.boxes)
                    one[i], two[j] = two[j], one[i]
                    if model.legal(pa, one) and model.legal(pb, two):
                        model.add(ca.page, tuple(one))
                        model.add(cb.page, tuple(two))
            # A tall failing chain can also give a box to a shorter page.
            # Both replacement patterns enter the same exact-cover pool.
            for source, target in ((ca, cb), (cb, ca)):
                if len(source.boxes) <= 1 or len(target.boxes) >= len(model.pages[target.page].slots):
                    continue
                for i, moved in enumerate(source.boxes):
                    for j in range(len(target.boxes)+1):
                        if monotonic() >= deadline:
                            return len(model.columns)-initial_columns
                        one = source.boxes[:i]+source.boxes[i+1:]
                        two = target.boxes[:j]+(moved,)+target.boxes[j:]
                        if model.legal(model.pages[source.page], one) and model.legal(model.pages[target.page], two):
                            model.add(source.page, one)
                            model.add(target.page, two)
    return len(model.columns)-initial_columns


def _master(model, cuts, *, seconds, seed, hint=()):
    """Set partitioning: one column per box, at most one per physical stack."""
    started = monotonic()
    master = cp_model.CpModel()
    x = [master.NewBoolVar(f"p{k}") for k in range(len(model.columns))]
    cover, stack_use = defaultdict(list), defaultdict(list)
    for k, c in enumerate(model.columns):
        for b in c.boxes:
            cover[b].append(x[k])
        for stack in model.pages[c.page].stacks:
            stack_use[stack].append(x[k])
    for b in model.boxes:
        if not cover[b]:
            return None, {"status": "missing_column", "box": model.T.box_ids[b]}
        master.AddExactlyOne(cover[b])
    for variables in stack_use.values():
        master.AddAtMostOne(variables)
    for cut in cuts:
        master.Add(sum(x[k] for k in cut["columns"]) <= cut["rhs"])
    # Search guidance only: retain most pages, flatten roofs, then reduce
    # restows. These costs are not reported as checked lift counts.
    hints = set(hint)
    terms = []
    for k, c in enumerate(model.columns):
        page = model.pages[c.page]
        roof = len(c.boxes)**2 if page.deck else 0
        cost = (0 if k in hints else 10000)+10*roof+int(c.cost)
        terms.append(cost*x[k])
        if k in hints:
            master.AddHint(x[k], 1)
    master.Minimize(sum(terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(.001, seconds)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = seed
    status = solver.Solve(master)
    result = {"status": solver.StatusName(status), "s": monotonic()-started,
              "columns": len(x), "cuts": len(cuts),
              "scope": "generated restricted pool only"}
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, result
    return [k for k in range(len(x)) if solver.Value(x[k])], result


def _cut(model, chosen, bound):
    """Lift a reviewed rod certificate only across disjoint pure pages.

    Entire patterns on every physical support stack touched by the chain
    are fixed. This includes both footprints of every long box and every
    other tier on those pages. No mass/GM/local-linearization cut is inferred.
    """
    T = model.T
    assignment = model.assignment(chosen)
    slots = bound.get("witnesses", {}).get("max_lashing_ratio", [])
    detail = bound.get("witness_details", {}).get("max_lashing_ratio", {})
    ratio = bound.get("lower_ratios", {}).get("max_lashing_ratio", 0.)
    selected = []
    if ratio > 1.+1e-8 and detail and slots and set(slots) <= set(assignment.values()):
        touched = {k for k in chosen if any(
            T.slot_ids[model.pages[model.columns[k].page].slots[tier]] in slots
            for tier in range(len(model.columns[k].boxes)))}
        # The representation was checked by _seed_patterns: each pattern
        # occupies complete support stacks; other selected pages are disjoint.
        if touched:
            selected = sorted(touched)
    kind = "securing_chain_no_good" if selected else "certified_stow_no_good"
    selected = selected or list(chosen)
    supports = []
    for k in selected:
        column = model.columns[k]
        page = model.pages[column.page]
        supports.append({"column": k, "page": column.page,
            "physical_stacks": [str(T.stack_ids[j]) for j in page.stacks],
            "box_slots": [[T.box_ids[b], T.slot_ids[page.slots[tier]]]
                          for tier, b in enumerate(column.boxes)]})
    return {"kind": kind, "certified": True, "columns": selected,
        "rhs": len(selected)-1, "supports": supports,
        "proof": bound["proof"], "witness_slots": slots if kind == "securing_chain_no_good" else [],
        "scope": ("Fixed instance/background and whole pure-page patterns on the stated physical stacks; "
                  "every ballast condition. Other stows and mixed-page families are not excluded.")}


def run(instance, initial_plan=None, *, problem=None, seed=0, budget_s=120.,
        iterations=4, iteration_s=30., oracle=None, check_survivors=True,
        initial_s=30.):
    """Run a bounded restricted-master/subproblem loop; return JSON-safe evidence.

    oracle(instance, plan) may return a candidate under ``candidate_plan`` or
    ``plan``. A physical certificate needs ``outward=True``, ``proof`` and
    ``status='infeasible_proven'`` (the ballast.py API). Local LP failure never
    supplies such a certificate. Every candidate is independently checked.
    An unproved rejection stops this loop rather than generating a false cut.
    """
    e9fix.install()
    if oracle is None:
        # This import is local so module inspection does not do any physics.
        ballast_spec = importlib.util.spec_from_file_location("_c2_benders_ballast", HERE / "ballast.py")
        ballast = importlib.util.module_from_spec(ballast_spec)
        ballast_spec.loader.exec_module(ballast)
        oracle = lambda inst, plan: ballast.solve_ballast(inst, plan,
            budget_s=10., exact_recheck=False, max_iterations=3)
    started = monotonic()
    deadline = started+max(0., budget_s)
    problem = problem or Problem(instance)
    model = Model(problem)
    result = {"instance_id": instance.id, "iterations": [], "cuts": [],
        "complete_stows": 0, "full_checker_passes": 0, "best_plan": None,
        "last_complete_plan": None, "gate": "legal replay + every load-list box; full checker separately",
        "family": "generated pure-stack page-pattern pool; restricted-master exhaustion is not map infeasibility"}
    if instance.onboard.placements:
        result.update(status="unsupported_nonempty_arrival", wall_s=monotonic()-started)
        return result
    try:
        if initial_plan is None:
            scored, initial_info = _initial(model, seed=seed,
                deadline=min(deadline, monotonic()+initial_s))
            result["initial"] = initial_info
            if scored is None:
                result.update(status="no_initial_stow", wall_s=monotonic()-started)
                return _jsonable(result)
            initial_plan = scored.plan
        elif isinstance(initial_plan, dict):
            from stowage.model import MovePlan
            initial_plan = MovePlan.model_validate(initial_plan)
        assignment, _ = _assignment(instance, initial_plan)
        chosen = _seed_patterns(model, assignment)
    except (ValueError, RuntimeError) as error:
        result.update(status="initial_error", error=str(error), wall_s=monotonic()-started)
        return _jsonable(result)
    seen = set()
    best_lifts = float("inf")
    for iteration in range(iterations):
        if monotonic() >= deadline:
            result["status"] = "budget"
            break
        round_start = monotonic()
        assignment = model.assignment(chosen)
        scored = _score_assignment(problem, assignment)
        row = {"iteration": iteration, "chosen": list(chosen), "columns": len(model.columns)}
        if scored is None:
            # The exact cargo replay is a discrete necessary condition; it is
            # safe to exclude this exact selected combination, but no ballast
            # infeasibility is claimed for the assignment.
            cut = {"kind": "replay_combination_no_good", "certified": False,
                "search_exclusion": True, "certified_physics": False,
                "columns": list(chosen), "rhs": len(chosen)-1,
                "scope": "this deterministic expansion and cargo ordering only"}
            result["cuts"].append(cut)
            row.update(complete_replay_legal=False, status="replay_rejected")
        else:
            assignment, replay = _assignment(instance, scored.plan)
            identity = tuple(sorted(assignment.items()))
            if identity not in seen:
                result["complete_stows"] += 1
                seen.add(identity)
            result["last_complete_plan"] = scored.plan
            row.update(complete_replay_legal=True, lifts=scored.objective,
                       assignment=assignment, plan=scored.plan)
            bound_start = monotonic()
            bound = bound_module.securing_bound(instance, replay.final_state.ship)
            row.update(securing_bound=bound, securing_bound_s=monotonic()-bound_start)
            if bound.get("proof"):
                result["cuts"].append(_cut(model, chosen, bound))
                row["status"] = "proved_ballast_infeasible"
            else:
                subproblem = oracle(instance, scored.plan) if oracle is not None else {}
                row["ballast_subproblem"] = subproblem
                candidate = subproblem.get("candidate_plan", subproblem.get("plan", scored.plan))
                if isinstance(candidate, dict):
                    from stowage.model import MovePlan
                    candidate = MovePlan.model_validate(candidate)
                if candidate is None:
                    candidate = scored.plan
                if check_survivors:
                    before_check = monotonic()
                    report = check(instance, candidate)
                    row.update(full_checker_passed=report.passed,
                        full_checker_s=monotonic()-before_check,
                        rule_ids=sorted({v.rule_id for v in report.violations}))
                    if report.passed:
                        result["full_checker_passes"] += 1
                        lifts = float(report.count.weighted_nonproductive)
                        if lifts < best_lifts:
                            result["best_plan"], best_lifts = candidate, lifts
                            result["best_lifts"] = lifts
                        row["status"] = "full_checker_pass"
                    else:
                        row["status"] = "unknown_ballast_feasibility"
                else:
                    row["status"] = "unknown_ballast_feasibility"
                physical_proof = (subproblem.get("status") == "infeasible_proven"
                    and subproblem.get("outward") is True and subproblem.get("proof"))
                if physical_proof:
                    if row.get("full_checker_passed"):
                        raise ValueError("contradiction: full checker passed but ballast oracle supplied a physical proof")
                    result["cuts"].append({"kind": "certified_stow_no_good",
                        "certified": True, "columns": list(chosen), "rhs": len(chosen)-1,
                        "scope": "this exact cargo assignment, every ballast setting",
                        "certificate": subproblem["proof"]})
                    row["status"] = "proved_ballast_infeasible"
        row["s"] = monotonic()-round_start
        result["iterations"].append(row)
        if row["status"] in ("full_checker_pass", "unknown_ballast_feasibility"):
            result["status"] = row["status"]
            break
        if iteration+1 >= iterations or monotonic() >= deadline:
            result["status"] = "iteration_limit" if iteration+1 >= iterations else "budget"
            break
        round_deadline = min(deadline, monotonic()+iteration_s)
        row["added_alternatives"] = _alternatives(model, chosen,
            deadline=min(round_deadline, monotonic()+min(3., iteration_s*.2)),
            focus=result["cuts"][-1]["columns"])
        chosen, master = _master(model, result["cuts"],
            seconds=max(.001, round_deadline-monotonic()), seed=seed+iteration, hint=chosen)
        row["next_master"] = master
        if chosen is None:
            result["status"] = "restricted_pool_exhausted" if master["status"] == "INFEASIBLE" else "master_unknown"
            break
    result.setdefault("status", "no_iterations")
    result.update(wall_s=monotonic()-started, columns=len(model.columns))
    return _jsonable(result)


def main():
    import argparse
    from stowage.model import Instance
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maps", type=int, default=4)
    parser.add_argument("--budget-s", type=float, default=120.)
    parser.add_argument("--iteration-s", type=float, default=30.)
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--initial-s", type=float, default=30.)
    parser.add_argument("--input", type=str, help="one path from the frozen medium12 list")
    parser.add_argument("--initial", type=Path, help="saved raw MovePlan JSON or a row with a plan field")
    parser.add_argument("--initial-dir", type=Path, default=HERE / "results/audit",
                        help="reuse <instance_id>-rplain-plan.json when available")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=HERE / "results/benders")
    args = parser.parse_args()
    # Positive provenance: only the committed medium12 list is accepted here.
    inputs = [q.strip() for q in (LAB / "sets/medium12.txt").read_text().splitlines()
              if q.strip() and not q.lstrip().startswith("#")]
    if args.input:
        if args.input not in inputs:
            parser.error("--input must be a path in the frozen medium12 list")
        inputs = [args.input]
    else:
        inputs = inputs[:args.maps]
    if args.initial and len(inputs) != 1:
        parser.error("--initial requires one --input or --maps 1")
    initial_plan = None
    if args.initial:
        raw = json.loads(args.initial.read_text())
        initial_plan = raw.get("plan", raw)
        if initial_plan is None:
            parser.error("--initial contains a null plan")
    out = args.out.resolve()
    if not out.is_relative_to(HERE):
        parser.error("output must stay inside c2/c2-benders")
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for relative in inputs:
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT / "experiments/mvp/strategy/blockrule/maps/train/medium"):
            raise ValueError("unexpected path in frozen medium12 list")
        instance = Instance.model_validate_json(path.read_text())
        saved = args.initial_dir / f"{instance.id}-rplain-plan.json"
        this_initial = initial_plan
        initial_source = str(args.initial) if args.initial else None
        if this_initial is None and saved.exists():
            raw = json.loads(saved.read_text())
            this_initial = raw.get("plan", raw)
            initial_source = str(saved)
        row = run(instance, this_initial, seed=args.seed, budget_s=args.budget_s,
            iteration_s=args.iteration_s, iterations=args.iterations, initial_s=args.initial_s)
        row["input"] = relative
        row["initial_source"] = initial_source or "plain page LP/dive reconstruction"
        (out / f"{instance.id}.json").write_text(json.dumps(row, indent=2, sort_keys=True)+"\n")
        rows.append({k: v for k, v in row.items() if k not in ("iterations", "best_plan", "last_complete_plan", "cuts")})
        print(json.dumps(rows[-1]), flush=True)
    (out / "summary.json").write_text(json.dumps({"maps": rows,
        "maps_complete_replay_legal": sum(r["complete_stows"] > 0 for r in rows),
        "maps_full_checker_pass": sum(r["full_checker_passes"] > 0 for r in rows)}, indent=2, sort_keys=True)+"\n")


if __name__ == "__main__":
    main()
