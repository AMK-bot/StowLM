"""D174 step 5, report only: would a mask that forbids same-call restows dead-end? Replays stored train plans through the
production engine and, before each action, splits the legal actions into restow-free ones (a yard box into a free slot) and
restows. A state with no restow-free action is one a ban could only escape by backtracking. This follows the model's own path, so
it says nothing about where a banned model would have gone instead. CPU only, train maps only.
Usage: restow_probe.py [workers=1]
"""
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import sys

import compact

HERE = Path(__file__).resolve().parent


def probe(job):
    from llm.lookahead.mask_filter import wrap_engine as lookahead
    from llm.securing.mask_filter import wrap_engine as securing
    from stowage.actions import get_action_space
    from stowage.actions.space import Place
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance
    from stowage.model import MovePlan
    instance = load_instance(compact.ROOT/job['input'])
    plan = MovePlan.model_validate(json.loads(Path(job['plan']).read_text()))
    space = get_action_space('boxslot')
    engine = lookahead(securing(make_feasibility_engine(instance, space, config=FeasibilityConfig(extra={'global_screen': 0}))))
    actions = list(space.from_plan(instance, plan))
    aboard, states, first_restow, stuck_at = {}, 0, None, None
    for index, action in enumerate(actions):
        legal = list(engine.legal_actions())
        occupied = set(aboard.values())
        free = [a for a in legal if isinstance(a, Place) and a.box not in aboard and a.slot not in occupied]
        is_restow = isinstance(action, Place) and (action.box in aboard or action.slot in occupied)
        if is_restow and first_restow is None:
            first_restow = index
        if is_restow and not free and stuck_at is None:
            stuck_at = index
        states += 1
        engine.push(action)
        if isinstance(action, Place):
            aboard[action.box] = action.slot
    return dict(key=job['key'], rung=job['rung'], actions=len(actions), first_restow=first_restow, stuck_at=stuck_at, boxes=len(instance.load_list))


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    rows = compact.train_jobs(HERE/'compact-train')
    done = {json.loads(p.read_text())['key']: json.loads(p.read_text()) for p in (HERE/'compact-train/rows').glob('*.json')}
    jobs = [j for j in rows if j['key'] in done and done[j['key']]['before']['restow_now'] > 0]
    with ProcessPoolExecutor(workers) as pool:
        out = list(pool.map(probe, jobs))
    (HERE/'restow-probe.json').write_text(json.dumps(out, indent=1))
    print(len(out), 'plans with same-call restows;', sum(r['first_restow'] is None for r in out), 'with no Place-level restow;',
          sum(r['stuck_at'] is not None for r in out), 'hit a state where the restow had no restow-free alternative')


if __name__ == '__main__':
    main()
