"""Persist complete graphs for the two examples explained in MODEL.md."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from alns import Problem
from exact import solve_relaxation, witness_actions
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.io import load_instance
from stowage.model import MovePlan
from stowage.moves import apply_plan

HERE = Path(__file__).resolve().parent
LAB = HERE.parent.parent
ROOT = LAB.parents[3]


def plain(value):
    if isinstance(value, dict):
        return {k:plain(v) for k,v in value.items()}
    if isinstance(value, (set,frozenset)):
        return sorted(value)
    if isinstance(value,(tuple,list)):
        return [plain(v) for v in value]
    return value


def one(rel, plan_data, evidence, out):
    path = ROOT/rel
    instance = load_instance(path)
    p = Problem(instance)
    plan = MovePlan.model_validate(plan_data)
    actions = tuple(get_action_space('boxslot').from_plan(instance, plan))
    if not all(isinstance(a,Place) for a in actions):
        raise ValueError('example requires a monotone stow')
    assignment = {a.box:a.slot for a in actions}
    bound = solve_relaxation(p, assignment, time_limit_s=2.)
    scored = p.score(witness_actions(assignment,bound))
    witness = apply_plan(instance,scored.plan) if scored else None
    original = apply_plan(instance,plan)
    row = dict(instance_id=instance.id,input=rel,input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
               evidence=evidence,assignment=assignment,vertices=sorted(assignment),
               yard_stacks=[s.model_dump(mode='json') for s in instance.yard.stacks],
               bound=asdict(bound),original_replay_legal=original.legal,
               original_replay_count=original.count.model_dump(mode='json'),
               witness_replay_legal=witness.legal if witness else False,
               witness_replay_count=witness.count.model_dump(mode='json') if witness else None,
               fresh_full_checker=False,
               scope='operational replay; upper-bound full-checker evidence is linked, not rerun here')
    out.write_text(json.dumps(plain(row),indent=2)+'\n')
    print(instance.id,bound.lower_bound,witness.count.relocations if witness else None)


if __name__ == '__main__':
    baseline = json.loads((LAB/'baseline.json').read_text())['maps']
    small = baseline['random.small.40100011.0.8']
    one(small['input'],small['plan'],'results/order-quick/random.small.40100011.0.8.json',
        HERE/'results/model-small.json')
    rel = 'experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_500/f60-high/seed-192200000.json'
    source = 'experiments/mvp/scale/runs/v6/maps/medium-feeder_500-f60-high-s192200000/finished.json'
    one(rel,json.loads((ROOT/source).read_text())['plan'],source,HERE/'results/model-medium.json')
