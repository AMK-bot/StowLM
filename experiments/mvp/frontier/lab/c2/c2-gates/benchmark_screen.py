"""Profiles and differential evidence on manifest-approved stored plans."""
from __future__ import annotations
import argparse
from collections import Counter
import cProfile
import hashlib
import json
from pathlib import Path
import pstats
import random
from time import monotonic

from stowage.checker import check
from stowage.io import load_instance
from stowage.model import MovePlan
from screen import screen, diagnose, ladder_evidence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
LAB = HERE.parents[1]


def stored_cases():
    """Only fixed-set inputs, validated against the frozen manifest hashes."""
    manifest = json.loads((LAB/'sets/MANIFEST.json').read_text())['maps']
    baseline = json.loads((LAB/'baseline.json').read_text())['maps']
    for identity, row in baseline.items():
        if row.get('plan'):
            approved = manifest[identity]
            path = ROOT/approved['input']
            assert hashlib.sha256(path.read_bytes()).hexdigest() == approved['input_sha256']
            yield load_instance(path), MovePlan.model_validate(row['plan']), 'baseline'
    for identity in ('D192.medium-feeder_500-f60-high-s192200000',
                     'D192.medium-feeder_800-f60-low-s192200101'):
        approved = manifest[identity]
        path = ROOT/approved['input']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == approved['input_sha256']
        row = json.loads((LAB/'c1/fx-scout/results/quick-3'/f'{identity}.json').read_text())
        yield load_instance(path), MovePlan.model_validate(row['plan']), 'scout-quick3'


def learning(violations):
    rules = {v.rule_id for v in violations}
    return dict(visibility=any('visibility' in r for r in rules),
                securing=any('securing' in r for r in rules),
                e6=any('e6' in r for r in rules),
                trim_draft=any('trim' in r or 'draft' in r for r in rules),
                bow_down=any(('trim' in v.rule_id and v.measured is not None and v.measured < 0)
                             or ('draft' in v.rule_id and 'fwd' in v.message) for v in violations),
                all_iscode=all(r.startswith('iscode') for r in rules))


def profile(out):
    rows=[]
    for instance, plan, source in stored_cases():
        if len(instance.load_list) not in (88, 162):
            continue
        print('profile start', instance.id, flush=True)
        profiler=cProfile.Profile()
        started=monotonic()
        report=profiler.runcall(check, instance, plan)
        full_s=monotonic()-started
        target=out/f'screen-profile-{len(instance.load_list)}'
        profiler.dump_stats(str(target.with_suffix('.pstats')))
        with target.with_suffix('.txt').open('w') as stream:
            pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(65)
        started=monotonic()
        partial=diagnose(instance, plan, complete_learning=True)
        partial_s=monotonic()-started
        rows.append(dict(instance=instance.id, boxes=len(instance.load_list), source=source,
                         full_s=full_s, full_passed=report.passed,
                         full_rules=sorted({v.rule_id for v in report.violations}),
                         partial_s=partial_s, partial_status=partial.status,
                         partial_rules=partial.rules, part_timings=partial.timings,
                         learning_equal=learning(report.violations)==learning(partial.violations)))
        (out/'screen-profile.json').write_text(json.dumps(rows, indent=2)+'\n')
        print(rows[-1], flush=True)


def perturbed(cases, count):
    """Deterministic plan-only diagnostic edits; original input is unchanged."""
    rng=random.Random(2601)
    # Include every stored small/medium once; perturb only tiny stored plans to
    # keep the full-check differential under a documented twenty-minute run.
    tiny=[row for row in cases if row[0].size_class == 'tiny']
    for index in range(count):
        instance, original, source=(cases[index] if index < len(cases)
                                    else tiny[(index//24) % len(tiny)])
        if index < len(cases):
            yield instance, original, source+'/stored'
            continue
        plan=original.model_copy(deep=True)
        kind=index % 7
        if kind == 0:
            plan.moves=[]
        elif kind == 1:
            plan.moves=plan.moves[:rng.randrange(len(plan.moves))]
        elif kind == 2:
            # Duplicate a load: replay must stop at its second occurrence.
            load=next(m for m in plan.moves if m.kind == 'load')
            plan.moves.insert(rng.randrange(len(plan.moves)+1),load.model_copy(deep=True))
        elif kind == 3:
            plan.moves=list(reversed(plan.moves))
        elif kind == 4:
            loads=[m for m in plan.moves if m.kind == 'load']
            if len(loads)>1:
                loads[1].dst=loads[0].dst
        elif kind == 5:
            # Same legal cargo stow; altered ballast probes physics gates.
            if plan.ballast is not None:
                plan.ballast.tank_masses_kg={}
            else:
                plan.moves=plan.moves[:-1]
        else:
            # Open final hatch rejects watertight departure condition.
            plan.moves=plan.moves[:-1]
        yield instance,plan,f'{source}/mutation-{kind}'


def differential(out, count):
    target=out/'screen-differential.json'
    rows=json.loads(target.read_text())['rows'] if target.exists() else []
    cases=list(stored_cases())
    # Cycling tiny/small plans keeps 200 independent full checks affordable;
    # medium samples remain present once each among the stored cases.
    for index,(instance,plan,source) in enumerate(perturbed(cases,count)):
        if index < len(rows):
            continue
        started=monotonic()
        verdict=screen(instance,plan)
        screen_s=monotonic()-started
        started=monotonic()
        report=check(instance,plan)
        full_s=monotonic()-started
        row=dict(index=index,instance=instance.id,source=source,screen=verdict,
                 full_passed=report.passed,screen_s=screen_s,full_s=full_s,
                 false_fail=verdict=='fail' and report.passed)
        rows.append(row)
        target.write_text(json.dumps(dict(
            count=len(rows),false_fails=sum(r['false_fail'] for r in rows),rows=rows),indent=2)+'\n')
        print(index,instance.id,verdict,report.passed,round(screen_s,3),round(full_s,3),flush=True)
        assert not row['false_fail'], row


def ladder(out):
    """AB/BA pairs on the identical actual rung plans (not wall-time searches)."""
    manifest=json.loads((LAB/'sets/MANIFEST.json').read_text())['maps']
    rows=[]
    for path in sorted((out/'ladder-transcripts/reference').glob('*/*.json')):
        record=json.loads(path.read_text())
        plan=MovePlan.model_validate(record['plan'])
        approved=manifest[plan.instance_id]
        source=ROOT/approved['input']
        assert hashlib.sha256(source.read_bytes()).hexdigest()==approved['input_sha256']
        instance=load_instance(source)
        if len(instance.load_list) not in (88,162):
            continue
        measurements=[]
        reports=[]
        partials=[]
        for order in [('full','partial'),('partial','full')]:
            for name in order:
                began=monotonic()
                report=check(instance,plan) if name=='full' else ladder_evidence(instance,plan)
                measurements.append(dict(name=name,seconds=monotonic()-began))
                if name=='full': reports.append(report)
                else: partials.append(report)
        flags=learning(reports[-1].violations)
        equal=all(learning(p.violations)==flags and (p.status!='fail')==reports[-1].passed
                  for p in partials)
        row=dict(instance=instance.id,rung=path.stem,learning_equal=equal,
                 full_passed=reports[-1].passed,full_rules=sorted({v.rule_id for v in reports[-1].violations}),
                 partial_rules=partials[-1].rules,used_full_check=partials[-1].used_full_check,
                 part_timings=partials[-1].timings,measurements=measurements,
                 full_s=sum(m['seconds'] for m in measurements if m['name']=='full')/2,
                 partial_s=sum(m['seconds'] for m in measurements if m['name']=='partial')/2)
        rows.append(row)
        result=dict(rows=rows,cases=len(rows),flag_mismatches=sum(not r['learning_equal'] for r in rows),
                    speedup=sum(r['full_s'] for r in rows)/sum(r['partial_s'] for r in rows),
                    screen_sha256=hashlib.sha256((HERE/'screen.py').read_bytes()).hexdigest())
        (out/'screen-ladder-paired.json').write_text(json.dumps(result,indent=2)+'\n')
        print(instance.id,path.stem,equal,measurements,flush=True)
        assert equal,row


def inventory(out,count):
    """Disclose duplicate diagnostic inputs and actual measured gate costs."""
    cases=list(stored_cases())
    identities=[]
    sizes=Counter()
    kinds=Counter()
    for instance,plan,source in perturbed(cases,count):
        digest=hashlib.sha256(json.dumps(plan.model_dump(mode='json'),sort_keys=True,
                                       separators=(',',':')).encode()).hexdigest()
        identities.append((instance.id,digest))
        sizes[instance.size_class]+=1
        kinds[source.split('/')[-1]]+=1
    evidence=json.loads((out/'screen-differential.json').read_text())
    rows=evidence['rows']
    result=dict(cases=len(rows),distinct_inputs=len(set(identities[:len(rows)])),
                size_counts=dict(sizes),case_kinds=dict(kinds),
                false_fails=sum(r['false_fail'] for r in rows),
                screen_fails=sum(r['screen']=='fail' for r in rows),
                full_passes=sum(r['full_passed'] for r in rows),
                total_screen_s=sum(r['screen_s'] for r in rows),
                total_full_s=sum(r['full_s'] for r in rows),
                screened_pipeline_s=sum(r['screen_s']+(r['full_s'] if r['screen']=='unknown' else 0.) for r in rows))
    result['gate_only_speedup']=result['total_full_s']/result['total_screen_s']
    result['screened_pipeline_speedup']=result['total_full_s']/result['screened_pipeline_s']
    (out/'screen-differential-summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['profile','differential','ladder','inventory'],default='profile')
    parser.add_argument('--count',type=int,default=200)
    parser.add_argument('--out',type=Path,default=HERE/'results')
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    if args.mode=='profile': profile(args.out)
    elif args.mode=='ladder': ladder(args.out)
    elif args.mode=='inventory': inventory(args.out,args.count)
    else: differential(args.out,args.count)
