"""Check one explicitly selected rejected V4 proposal, without changing search."""
import argparse
import time
import run
from stowage.io import load_instance
from stowage.model import PlannerResult, BallastCondition
from stowage.checker import check
from stowage.rules.catalogue import rule_freeze_v1

parser=argparse.ArgumentParser();parser.add_argument('name');parser.add_argument('--line',type=int);args=parser.parse_args()
task=next(t for t in run.read(run.HERE/'tasks.json') if t['name']==args.name)
folder=run.HERE/'runs'/args.name
assert (folder/'complete.json').exists(), 'Do not add a second heavy worker during the direct run'
if (folder/'representative-summary.json').exists():
    assert (folder/'representative-full-report.json').exists() and (folder/'representative-plan.json').exists()
    print(task['map_id'],'reuse completed representative full check')
    raise SystemExit(0)
rows=[run.json.loads(s) for s in (folder/'evaluations.jsonl').read_text().splitlines()]
# Prefer full-proposal evaluations that reached the furthest physics stage.
stages={'stability':0,'strength':1,'visibility':2,'lashing':3}
def rank(row):
    # A cache hit leaves diagnostics.stage unchanged; identify the actual
    # rejecting part from returned keys, not the potentially stale stage.
    stage=max((name for name in stages if any(k.startswith(name+'/') or '/'+name+'/' in k
        for k in row['negative_margins'])),key=stages.get,default='stability')
    return (-stages[stage],sum(min(v,0.)**2 for v in row['negative_margins'].values()))
eligible=[(i,r) for i,r in enumerate(rows) if r['selected_keys'] is None and r['error'] is None]
index,row=(args.line,rows[args.line]) if args.line is not None else min(eligible,key=lambda pair:rank(pair[1]))
cohort=run.read(run.ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json')
job=next(j for j in cohort['maps'] if j['map_id']==task['map_id'])
instance=load_instance(run.ROOT/job['input']);raw=PlannerResult.model_validate(run.read(run.ROOT/task['raw_result']))
tanks=sorted((t for t in instance.vessel.tanks if t.adjustable),key=lambda t:t.id)
masses={t.id:f*t.capacity_m3*t.content_density_kg_m3 for t,f in zip(tanks,row['fractions'])}
plan=raw.plan.model_copy(update={'ballast':BallastCondition(tank_masses_kg=masses,source='finisher')})
run.save(folder/'representative-plan.json',raw.model_copy(update={'plan':plan}).model_dump(mode='json'))
run.save(folder/'representative-admission.json',dict(pid=run.os.getpid(),unix=time.time(),
    evaluation_line_zero_based=index,raw_sha256=run.sha(run.ROOT/task['raw_result'])))
started=time.monotonic()
with rule_freeze_v1(enabled=False):report=check(instance,plan)
run.save(folder/'representative-full-report.json',report.model_dump(mode='json'))
run.save(folder/'representative-summary.json',dict(evaluation_line_zero_based=index,search_row=row,
    selection='deepest failing part represented in full-proposal returned negative margins; tie by partial margin squared deficit; not globally best',
    seconds=time.monotonic()-started,checker=run.summary(report)))
print(task['map_id'],report.passed,run.summary(report)['rules'],time.monotonic()-started)
