"""Task1.4: frozen proof-backed refusal examples and safe one-shot explanation."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import time

from experiments.mvp.reader.run_brief import ROOT, HERE, write, digest, admission
from experiments.mvp.reader.schema import context_from_instance, answer_envelope, validate_answer, canonicalize
from experiments.mvp.reader.refusal_evidence import build_refusal_evidence

RUN=HERE/'runs/refusals'
PURPOSE='reader-d222-followup'
BUDGET=Decimal('1.90')


def setup():
    """Freeze demo requests before reader/explanation calls; no new plans."""
    from stowage.io import load_instance
    cohort=json.loads((HERE/'runs/brief/cohort.json').read_text())['cases']
    by_id={c['case_id']:c for c in cohort}
    impossible=by_id['brief-07']
    alternative=by_id['brief-02']
    # Curated demonstration, not another unselected measurement. Select the
    # first zone/cap-zero proof on this fixed map that has a historical witness
    # for some same-family relaxed cap. The finite criterion is recorded.
    instance=load_instance(ROOT/alternative['input'])
    candidates=[]
    for zone in ('hold','deck'):
        body={'schema_version':'stowlm.reader/2','constraint':dict(type='max_tiers',bay=None,zone=zone,max_tiers=0)}
        proof=build_refusal_evidence(instance,body,stored_witness=ROOT/alternative['baseline_file'])
        candidates.append(dict(zone=zone,proven_impossible=proof['proof']['proven_impossible'],
                               historical_witness_alternatives=sum(a['certificate']=='stored_checked_witness' for a in proof['alternatives'])))
        if proof['proof']['proven_impossible'] and any(a['certificate']=='stored_checked_witness' for a in proof['alternatives']):
            chosen_zone=zone
            break
    else:
        raise ValueError('Fixed alternative demo has no proof/witness; do not invent a result')
    cases=[
      dict(case_id='impossible-bay-2',source_case='brief-07',input=impossible['input'],input_sha256=impossible['input_sha256'],
           sentence='Bay 2 is closed.',expected=impossible['expected'],baseline_file=impossible['baseline_file']),
      dict(case_id='loosening-dg',source_case='brief-07',input=impossible['input'],input_sha256=impossible['input_sha256'],
           sentence='Ignore the dangerous-goods segregation rules.',expected_refusal='loosening',
           rule_details=dict(id='imdg.segregation.7_2_4',name='General dangerous-goods segregation table',
                             description='Dangerous-goods class combinations must satisfy the checker\'s applicable segregation requirements.'),
           rule_source='src/stowage/rules/catalogue.toml'),
      dict(case_id='unclear-distance',source_case='brief-07',input=impossible['input'],input_sha256=impossible['input_sha256'],
           sentence='Keep the dangerous cargo farther away.',expected_refusal='ambiguous',
           clarification_question='Which IMDG class, which bay, and how many 20-foot bays of clearance do you mean?'),
      dict(case_id='impossible-tier-with-alternative',source_case='brief-02',input=alternative['input'],input_sha256=alternative['input_sha256'],
           sentence=f'Across the vessel, use at most zero occupied tiers per {chosen_zone} stack.',
           expected={'schema_version':'stowlm.reader/2','constraint':dict(type='max_tiers',bay=None,zone=chosen_zone,max_tiers=0)},
           baseline_file=alternative['baseline_file']),
    ]
    sources={str((HERE/name).relative_to(ROOT)):digest(HERE/name) for name in
             ('refusal_demo.py','refusal_evidence.py','refusal_explainer.py','pipeline.py','schema.py')}
    value=dict(schema_version='reader-refusal-demo/1',selection='curated demo examples, not an unselected accuracy cohort',
               created_utc=datetime.now(timezone.utc).isoformat(),cases=cases,
               alternative_selection_candidates=candidates,source_sha256=sources,
               max_new_spark_usd=str(BUDGET),purpose=PURPOSE,shared_with='D222 second-cohort parsing',
               first_cohort_sha256=digest(HERE/'runs/brief/cohort.json'),no_planner_calls=True)
    path=RUN/'manifest.json'
    if path.exists():
        prior=json.loads(path.read_text())
        for key in ('cases','source_sha256','first_cohort_sha256'):
            if prior[key]!=value[key]: raise ValueError('Refusal freeze changed: '+key)
    else: write(path,value)
    witness=next(c for c in cases if c['case_id']=='impossible-tier-with-alternative')
    request=RUN/'witness-request.json'
    if not request.exists(): write(request,witness)
    return value


def verify():
    manifest=json.loads((RUN/'manifest.json').read_text())
    for name,sha in manifest['source_sha256'].items():
        if digest(ROOT/name)!=sha: raise ValueError('Refusal source changed: '+name)
    for case in manifest['cases']:
        if digest(ROOT/case['input'])!=case['input_sha256']: raise ValueError('Refusal input changed')
    return manifest


def audit_pending_witness():
    """Run one fresh full check inside the shared two-worker pool slot."""
    target=RUN/'witness-audit.json'
    if target.exists(): return
    import fcntl
    RUN.mkdir(parents=True,exist_ok=True)
    with (RUN/'witness.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        if target.exists():return
        manifest=verify()
        case=json.loads((RUN/'witness-request.json').read_text())
        admitted=admission()
        started=time.perf_counter()
        from stowage.io import load_instance
        from stowage.model import MovePlan
        from stowage.checker import check
        from experiments.mvp.reader.pipeline import intrinsic_violations
        row=dict(case_id=case['case_id'],pid=os.getpid(),admission=admitted,passed=False)
        try:
            instance=load_instance(ROOT/case['input'])
            baseline=json.loads((ROOT/case['baseline_file']).read_text())
            plan=MovePlan.model_validate(baseline['plan'])
            checked=check(instance,plan)
            proof=build_refusal_evidence(instance,case['expected'],stored_witness=ROOT/case['baseline_file'])
            row.update(passed=bool(checked.passed),plan_sha256=hashlib.sha256(plan.model_dump_json().encode()).hexdigest(),
                       weighted=float(checked.count.weighted_nonproductive),baseline_sha256=digest(ROOT/case['baseline_file']),
                       alternative_violations={a['id']:intrinsic_violations(instance,validate_answer(json.dumps(a['constraint']),context_from_instance(instance)),plan)
                                               for a in proof['alternatives']})
        except Exception as error:row['error_type']=type(error).__name__
        row['wall_s']=time.perf_counter()-started
        write(target,row)


def explain():
    from stowage.io import load_instance
    from llm.instructions.schema import Refusal
    from experiments.mvp.reader.pipeline import SparkParser
    from experiments.mvp.reader.refusal_explainer import SparkRefusalExplainer
    manifest=verify()
    parser=SparkParser(purpose=PURPOSE,allocation_usd=BUDGET)
    explainer=SparkRefusalExplainer(purpose=PURPOSE,allocation_usd=BUDGET)
    audit_path=RUN/'witness-audit.json'
    for case in manifest['cases']:
        target=RUN/'examples'/f"{case['case_id']}.json"
        if target.exists():continue
        # Wait for the queued fresh checker before advertising any witnessed alternative.
        if case['case_id']=='impossible-tier-with-alternative' and not audit_path.exists():
            print('Alternative explanation awaits its admitted fresh witness audit.',flush=True)
            continue
        instance=load_instance(ROOT/case['input'])
        parse_path=RUN/'parses'/f"{case['case_id']}.json"
        if parse_path.exists():
            parsed=json.loads(parse_path.read_text())
            answer=validate_answer(json.dumps(parsed['parsed']),context_from_instance(instance))
        else:
            answer,provider=parser.parse(case['sentence'],context_from_instance(instance))
            parsed=dict(case_id=case['case_id'],sentence=case['sentence'],parsed=answer_envelope(answer),parser=provider)
            write(parse_path,parsed)
        agreement=(isinstance(answer,Refusal) and answer.code==case.get('expected_refusal')) if 'expected_refusal' in case else canonicalize(answer)==canonicalize(case['expected'])
        if not agreement:
            write(target,dict(case_id=case['case_id'],status='reader_mismatch',passed=False,parsed=parsed))
            continue
        proof=build_refusal_evidence(instance,answer,stored_witness=ROOT/case['baseline_file'] if case.get('baseline_file') else None,
                                     rule_details=case.get('rule_details'),clarification_question=case.get('clarification_question'))
        if case['case_id']=='impossible-tier-with-alternative':
            audited=json.loads(audit_path.read_text())
            for alternative in proof['alternatives']:
                if audited['passed'] and not audited['alternative_violations'].get(alternative['id'],['not audited']):
                    alternative['fresh_audit']=dict(passed=True,plan_sha256=audited['plan_sha256'],audit_file=str(audit_path.relative_to(ROOT)),audit_sha256=digest(audit_path))
                    alternative['text']=alternative['text'].replace('A stored full-checked witness satisfies this alternative.',
                                         'A stored plan passed a fresh full check and satisfies this alternative.')
            proof['nearest_alternative_id']=next((a['id'] for a in proof['alternatives'] if a.get('fresh_audit')),None)
        text,provider=explainer.explain(proof)
        write(target,dict(case_id=case['case_id'],sentence=case['sentence'],passed=True,parsed=parsed,
                         proof=proof,explanation=text,explainer=provider,
                         no_planner_called=True,original_instruction_changed=False))
        print(json.dumps(dict(case_id=case['case_id'],passed=True,status=proof['status'],cost_usd=provider['cost_usd'])),flush=True)


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('mode',choices=('prepare','explain'))
    args=cli.parse_args()
    setup() if args.mode=='prepare' else explain()
