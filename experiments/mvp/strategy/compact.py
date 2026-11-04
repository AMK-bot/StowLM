"""D174 "plan on paper": treat a StowLM plan as a draft. Keep each box's final slot, drop the intermediate placements, and let
software pick a clean load order; then the move engine, the V4 finisher and the full checker, as for any plan.

The order is a topological sort of two_step's necessary precedences (support below first, hold before deck under a hatch),
tried with three tie-breaks: yard-top first, finish a hatch before the next, and the model's own order. The expanded plan
with the fewest weighted moves is the compacted plan. It replaces the original only if it passes and costs less (or the
original failed): no repair, no slot changes, same stow. A plan with discharges keeps them first, in the model's order; a
plan that moves a box other than a load-list box is left alone.
Library: compact_plan(), finish_and_check(). Usage: compact.py train [workers=5] | compact.py retry [workers=4] | compact.py folder <run_dir> <out> [workers]
The finisher budget is COMPACT_FINISHER_S (default 120).
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ.setdefault(name, '1')

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEPLOYMENT = ROOT/'experiments/mvp/exit/deployment-d137'
BUDGET_S = float(os.environ.get('COMPACT_FINISHER_S', 120))


def parts(count):
    get = lambda f, k: getattr(f, k) if not isinstance(f, dict) else f[k]
    return dict(relocations=count.relocations, restow_now=count.restow_lifts, hatch_now=count.hatch_cover_lifts,
                restow_later=sum(get(f, 'restow_lifts') for f in count.future),
                hatch_later=sum(get(f, 'hatch_cover_lifts') for f in count.future), total=count.weighted_nonproductive)


def topological(n, edges, key):
    """Kahn's algorithm, always taking the ready item with the smallest key(item, chosen so far)."""
    needs = {i: set() for i in range(n)}
    for before, after in edges:
        needs[after].add(before)
    chosen, ready = [], {i for i in range(n) if not needs[i]}
    while ready:
        pick = min(ready, key=lambda i: key(i, chosen))
        chosen.append(pick)
        ready.remove(pick)
        for i, required in needs.items():
            if i not in chosen and i not in ready and required <= set(chosen):
                ready.add(i)
    if len(chosen) != n:
        raise ValueError('precedences have a cycle')
    return chosen


def compact_plan(instance, plan):
    """(compacted MovePlan or None, info). info has the original count, the order used and why nothing was produced."""
    from stowage.actions import get_action_space
    from stowage.actions.space import Discharge, Place
    from stowage.conventions import cells_of_slot
    from stowage.moves import apply_plan, count_moves
    from stowage.solver.twostep_terminal import _precedences

    applied = apply_plan(instance, plan)
    info = dict(before=parts(applied.count), n_actions=len(plan.actions or []), n_loads=len(instance.load_list))
    if not applied.legal:
        return None, dict(info, reason='original plan is illegal')
    space = get_action_space('boxslot')
    actions = list(space.from_plan(instance, plan))
    loads = set(instance.load_list)
    if any(isinstance(a, Place) and a.box not in loads for a in actions):
        return None, dict(info, reason='plan moves a box that is not on the load list')
    discharges = [a for a in actions if isinstance(a, Discharge)]
    final = {p.container_id: p.slot for p in applied.final_state.ship.placements if p.container_id in loads}
    if set(final) != loads:
        return None, dict(info, reason='a load-list box did not end aboard')
    boxes = sorted(final)
    slots = [SimpleNamespace(slot=final[box]) for box in boxes]
    edges = _precedences(instance, slots)
    last = {a.box: k for k, a in enumerate(actions) if isinstance(a, Place)}
    position = {box: (stack.id if hasattr(stack, 'id') else index, at, len(stack.boxes))
                for index, stack in enumerate(instance.yard.stacks) for at, box in enumerate(stack.boxes)}
    stacks = {s.id: s for s in instance.vessel.stacks}
    cells = {c.id: c for c in instance.vessel.cells}

    def above(i, chosen):
        """Load-list and other boxes still above this one in its yard stack."""
        gone = {boxes[k] for k in chosen}
        where = position.get(boxes[i])
        if where is None:
            return 0
        stack = next(s for k, s in enumerate(instance.yard.stacks) if (s.id if hasattr(s, 'id') else k) == where[0])
        return sum(b not in gone for b in stack.boxes[where[1]+1:])

    def hatch(i):
        cell = cells_of_slot(slots[i].slot)[0]
        return stacks[cells[cell].stack_id].hatch_cover_id

    def by_hatch(i, chosen):
        same = not chosen or hatch(chosen[-1]) == hatch(i)
        return (not same, above(i, chosen), last[boxes[i]] if boxes[i] in last else i)

    orders = dict(yard_top=lambda i, chosen: (above(i, chosen), last.get(boxes[i], i)),
                  hatch_then_yard=by_hatch,
                  model_order=lambda i, chosen: last.get(boxes[i], i))
    best, best_cost, best_name, errors = None, None, None, {}
    for name, key in orders.items():
        try:
            order = topological(len(boxes), edges, key)
            candidate = space.expand(instance, [*discharges, *(Place(boxes[i], slots[i].slot) for i in order)])
        except Exception as error:
            errors[name] = f'{type(error).__name__}: {error}'
            continue
        cost = count_moves(instance, candidate).weighted_nonproductive
        if best_cost is None or cost < best_cost:
            best, best_cost, best_name = candidate, cost, name
    info.update(order=best_name, order_errors=errors)
    if best is None:
        return None, dict(info, reason='no order expanded')
    return best, dict(info, compact_raw=parts(count_moves(instance, best)))


def finish_and_check(instance, plan, budget_s=BUDGET_S, seed=None):
    """The V4 finisher scoped to finalize, then the full checker; no repair. Returns (row, finished result, report). ``seed`` is
    the finisher's seed, the map's own by default (rev-k1b P2: every arm must pass the same one, and the row records it)."""
    from stowage.checker import check
    from stowage.model import PlannerResult
    from stowage.planner import finalize
    started = time.perf_counter()
    result = PlannerResult(status='ok', plan=plan.model_copy(update={'ballast': None}), planner='strategy.compact', planner_version='1',
                           seed=instance.seed if seed is None else seed, provenance=plan.provenance)
    os.environ['STOWAGE_BALLAST_V4'] = '1'
    try:
        result = finalize(instance, result, time_limit_s=budget_s)
    finally:
        os.environ.pop('STOWAGE_BALLAST_V4', None)
    row = dict(finisher_s=round(time.perf_counter()-started, 2), finalize_status=result.status, message=result.message,
               finisher_seed=instance.seed if seed is None else seed)
    if result.status != 'ok' or result.plan is None:
        return dict(row, passed=False, timed_out=row['finisher_s'] >= .9*budget_s), None, None
    report = check(instance, result.plan)
    row.update(passed=report.passed is True, check_s=round(time.perf_counter()-started-row['finisher_s'], 2),
               rules=[v.rule_id for v in report.violations][:5])
    if report.passed:
        row.update(moves=report.count.weighted_nonproductive, after=parts(report.count))
    return row, result, report


def mask_ok(instance, plan):
    from llm.exit.selection import mask_replay
    try:
        details = mask_replay(instance, plan)
    except Exception as error:
        return f'{type(error).__name__}: {error}'
    return details.get('passed') is True


def resolve(row, info, before_passed, before_moves):
    """D174's keep rule: the compacted plan only if it passes and is cheaper, or if the original failed."""
    after = row.get('moves') if row and row.get('passed') else None
    if after is not None and (not before_passed or after < before_moves):
        return dict(kept='compact', final_passed=True, final_moves=after)
    return dict(kept='original', final_passed=before_passed, final_moves=before_moves if before_passed else None)


def allowed_input(rel):
    if not rel.startswith('experiments/mvp/corpus/') or '/test/' in rel or '/showcase/' in rel:
        raise ValueError(f'input not allowed here: {rel}')


def admit_mask(check_row, replay):
    """rev-compact P2: only a literal True admits the plan; False or an exception string rejects it, whatever the checker said."""
    check_row['mask_replay'] = replay
    if replay is not True:
        check_row['passed'] = False
    return check_row['passed']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def run_identity():
    """Mask configuration and outcome epoch of this process; a snapshot takes 6-10 s, so once per worker."""
    from llm.exit import outcome_epoch
    from llm.stowlm.mask import MASK_VERSION, MaskConfig
    return dict(mask=MASK_VERSION, mask_config=repr(MaskConfig()), outcome_epoch=outcome_epoch.epoch_id(outcome_epoch.snapshot()))


def job_binding(job, identity):
    """What a stored row must match to be reused: the job, the input and source-plan bytes, the verdict it started from, the
    finisher budget, the mask and the outcome epoch."""
    return dict(key=job['key'], input=job['input'], input_sha256=sha(ROOT/job['input']), plan_sha256=sha(job['plan']), before_passed=job['before_passed'],
                before_moves=job['before_moves'], verdict_sha256=job['verdict_sha256'], finisher_s=BUDGET_S, **identity)


def usable_row(target, binding):
    """A stored row, only if bound to exactly this job and its kept compact plan still exists. Unbound rows run again."""
    target = Path(target)
    if not target.exists():
        return None
    row = json.loads(target.read_text())
    if row.get('binding') != binding:
        return None
    if row.get('kept') == 'compact' and not (target.parent.parent/'plans'/f"{binding['key']}.json").exists():
        return None
    return row


def one_candidate(job):
    from stowage.io import load_instance
    from stowage.model import MovePlan
    target = Path(job['target'])
    allowed_input(job['input'])
    binding = job_binding(job, run_identity())
    stored = usable_row(target, binding)
    if stored is not None:
        return stored
    instance = load_instance(ROOT/job['input'])
    plan = MovePlan.model_validate(json.loads(Path(job['plan']).read_text()))
    started = time.perf_counter()
    compacted, info = compact_plan(instance, plan)
    row = dict({k: job[k] for k in ('key', 'input', 'rung', 'source', 'before_passed', 'before_moves')}, binding=binding, instance_id=instance.id, **info)
    check_row = None
    cheaper = compacted is not None and (not job['before_passed'] or info['compact_raw']['total'] < job['before_moves'])
    if cheaper:
        check_row, finished, _ = finish_and_check(instance, compacted)
        if check_row.get('passed'):
            if admit_mask(check_row, mask_ok(instance, finished.plan)):
                (target.parent.parent/'plans').mkdir(exist_ok=True)
                (target.parent.parent/'plans'/f"{job['key']}.json").write_text(finished.model_dump_json())
    row.update(resolve(check_row, info, job['before_passed'], job['before_moves']), attempted=cheaper, check=check_row,
               wall_s=round(time.perf_counter()-started, 2))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(row, indent=1, sort_keys=True))
    print(json.dumps({k: row.get(k) for k in ('key', 'before_moves', 'final_moves', 'kept', 'wall_s')}), flush=True)
    return row


def text_sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def train_jobs(out):
    jobs = []
    for path in sorted(DEPLOYMENT.glob('gen-00*/cases/*/selection.json')):
        selection = json.loads(path.read_text())
        rel = str(Path(selection['instance_path']).resolve().relative_to(ROOT))
        if '/corpus/train/' not in rel:
            raise SystemExit(f'not a train map: {rel}')
        by_id = {v['candidate_id']: v for v in selection['verifications']}
        for candidate in selection['candidates']:
            if candidate['source'] != 'model':
                continue
            verdict = by_id.get(candidate['candidate_id'], {})
            passed = verdict.get('passed') is True
            key = f"{path.parts[-4]}-{path.parts[-2]}-{candidate['candidate_id']}"
            jobs.append(dict(key=key, input=rel, rung=rel.split('/')[4], plan=candidate['plan_path'], source=candidate['metadata'].get('adapter'),
                             before_passed=passed, before_moves=verdict.get('weighted_moves') if passed else None,
                             verdict_sha256=text_sha(json.dumps(verdict, sort_keys=True, default=str)), target=str(out/'rows'/f'{key}.json')))
    return jobs


def folder_jobs(run_dir, out):
    """Stored finished plans from a run_c1 run (validation only; the corpus guard in one_candidate refuses test)."""
    jobs = []
    for path in sorted(Path(run_dir).glob('instances/*.json')):
        rec = json.loads(path.read_text())
        folder = path.parent/rec['instance_id']
        result = json.loads((folder/'result.json').read_text())
        report = json.loads((folder/'report.json').read_text()) or {}
        if result.get('plan') is None:
            continue
        plan = out/'source-plans'/f"{rec['instance_id']}.json"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(json.dumps(result['plan']))
        verdict = text_sha((folder/'report.json').read_text()+(folder/'outcome.json').read_text())
        passed = report.get('passed') is True and json.loads((folder/'outcome.json').read_text())['outcome'] == 'own pass'
        jobs.append(dict(key=rec['instance_id'], input=rec['input'], rung=rec['rung'], plan=str(plan), source='8.2 greedy', before_passed=passed,
                         before_moves=(report.get('count') or {}).get('weighted_nonproductive') if passed else None, verdict_sha256=verdict,
                         target=str(out/'rows'/f"{rec['instance_id']}.json")))
    return jobs


def retry_jobs(out):
    """Train plans whose compacted form only hit the finisher limit, again at COMPACT_FINISHER_S."""
    timed_out = {r['key'] for r in map(json.loads, (p.read_text() for p in (HERE/'compact-train/rows').glob('*.json')))
                 if r['attempted'] and (r['check'] or {}).get('timed_out')}
    return [j for j in train_jobs(out) if j['key'] in timed_out]


def main():
    if sys.argv[1] == 'train':
        out, workers = HERE/'compact-train', int(sys.argv[2]) if len(sys.argv) > 2 else 5
        jobs = train_jobs(out)
    elif sys.argv[1] == 'retry':
        out, workers = HERE/'compact-train-retry', int(sys.argv[2]) if len(sys.argv) > 2 else 4
        jobs = retry_jobs(out)
    else:
        out, workers = Path(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 5
        jobs = folder_jobs(sys.argv[2], out)
    (out/'rows').mkdir(parents=True, exist_ok=True)
    print(len(jobs), 'candidates', flush=True)
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(one_candidate, jobs))
    print(len(rows), 'done', flush=True)


if __name__ == '__main__':
    main()
