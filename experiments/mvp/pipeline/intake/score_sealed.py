"""Task 3.1 only: one invocation, one attempt per case, frozen code and inputs."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import sha256_file
from . import _documents, parser_baseline, read_documents
from .admission import wait_for_slot
from . import campaign_v7 as c7, campaign_v8 as c8
from .evaluate_v2 import dependency_fingerprints, runtime_dependencies
from .interpretation import prompt
from .record_v8 import record_case
from .spark_reader import RecordedClient

HERE=Path(__file__).resolve().parent
COHORT=HERE/'fixtures/sealed_intake_v1'
POLICY={**c8.POLICY,'case_count':6,'invocation_limit':1,
        'case_inclusion':'all six sealed cases; no substitution, retry, exclusion or resume',
        'evaluation_split':'sealed_intake_v1'}


def utc():
    return datetime.now(timezone.utc).isoformat()


def code_pins():
    return {'source_files':{p.name:sha256_file(p) for p in sorted(HERE.glob('*.py'))},
            'launcher_sha256':sha256_file(HERE/'score_sealed.sh'),
            'dependency_files':dependency_fingerprints(),
            'runtime_dependencies':runtime_dependencies(),
            'prompt_sha256':hashlib.sha256(prompt().encode()).hexdigest()}


def verify_inputs(cohort=COHORT):
    """Only the manifest is decoded; document/truth bytes are hashed, not read."""
    cohort=Path(cohort)
    path=cohort/'blind_freeze.json'
    if sha256_file(path)!=(cohort/'blind_freeze.sha256').read_text().split()[0]:
        raise ValueError('Sealed manifest changed')
    manifest=json.loads(path.read_text())
    cases=manifest.get('cases',[])
    if (len(cases)!=POLICY['case_count'] or len({c['split'] for c in cases})!=len(cases)
            or not manifest.get('frozen_before_scoring') or not manifest.get('frozen_before_tuning')):
        raise ValueError('Expected six independently frozen cases')
    for case in cases:
        split=case['split']
        if Path(split).name!=split or not split.startswith('sealed_') or case['rows']!=POLICY['boxes_per_case']:
            raise ValueError('Invalid sealed case identity or denominator')
        if not {'documents.json','expected.json','base_instance.json'}<=set(case['files']):
            raise ValueError('Missing frozen case inputs')
        for name,digest in case['files'].items():
            if Path(name).name!=name or sha256_file(cohort/split/name)!=digest:
                raise ValueError('Sealed case input changed')
    return manifest


def freeze(cohort=COHORT):
    """Hash-only source seal; this function never opens a case document/truth."""
    cohort=Path(cohort)
    manifest=verify_inputs(cohort)
    if (cohort/'RUN_CLAIM.json').exists() or any(
        (cohort/c['split']/n).exists() for c in manifest['cases']
        for n in ('spark_request.json','spark_response.json','spark_raw.json','metrics.json')):
        raise ValueError('Cannot seal after any reading or score attempt')
    value={'schema_version':'stowlm.intake-sealed-source/1','frozen_utc':utc(),
           'input_sha256':sha256_file(cohort/'blind_freeze.json'),'policy':POLICY,**code_pins()}
    c7.save_once(cohort/'SOURCE_FREEZE.json',value)
    return {'input_sha256':value['input_sha256'],'source_sha256':sha256_file(cohort/'SOURCE_FREEZE.json')}


def verify_frozen(cohort=COHORT):
    cohort=Path(cohort)
    seal=json.loads((cohort/'SOURCE_FREEZE.json').read_text())
    if seal.get('schema_version')!='stowlm.intake-sealed-source/1' or seal.get('policy')!=POLICY:
        raise ValueError('Changed sealed scoring policy')
    if seal.get('input_sha256')!=sha256_file(cohort/'blind_freeze.json'):
        raise ValueError('Different sealed inputs')
    if any(seal.get(k)!=v for k,v in code_pins().items()):
        raise ValueError('Frozen code, prompt, dependencies or runtime changed; scoring forbidden')
    return verify_inputs(cohort)


def claim_run(cohort=COHORT):
    """Atomic O_EXCL publication; a crash also consumes the single invocation."""
    cohort=Path(cohort)
    if (cohort/'RUN_CLAIM.json').exists():
        raise ValueError('Sealed scorer was already invoked; second run/resume forbidden')
    manifest=verify_frozen(cohort)
    c7.save_once(cohort/'RUN_CLAIM.json',{'started_utc':utc(),
        'source_sha256':sha256_file(cohort/'SOURCE_FREEZE.json'),
        'input_sha256':sha256_file(cohort/'blind_freeze.json'),'attempt_limit':1})
    return manifest


def score_case(case):
    """No expected answer enters extraction or source authority."""
    documents=c8._input(case)
    raw,captured,status=c8.authenticated_capture(case,documents)
    base=json.loads((case/'base_instance.json').read_text())
    expected=json.loads((case/'expected.json').read_text())
    if len(expected['rows'])!=POLICY['boxes_per_case']:
        raise ValueError('Changed case denominator')
    started=time.monotonic()
    errors={}
    try:
        spark=(read_documents(documents,base_instance=base,client=RecordedClient(case/'spark_raw.json'))
               if raw is not None else c8.rejection_result(expected,status))
    except Exception as error:
        status='validation_rejected'
        errors['spark']={'type':type(error).__name__,'message':str(error)}
        spark=c8.rejection_result(expected,status)
    spark_s=time.monotonic()-started
    started=time.monotonic()
    try:
        parser=parser_baseline(documents,base_instance=base)
        parser_status='completed'
    except Exception as error:
        parser_status='baseline_rejected'
        errors['parser']={'type':type(error).__name__,'message':str(error)}
        parser=c8.rejection_result(expected,parser_status)
    parser_s=time.monotonic()-started
    cost=captured.get('cost_usd')
    known=isinstance(cost,(int,float)) and not isinstance(cost,bool)
    result={'case':case.name,'reading_status':status,'baseline_status':parser_status,
        'caption':f'{len(expected["rows"])} synthetic boxes; sealed test, not a whole ship',
        'scored_utc':utc(),'errors':errors,'cost_unknown':not known,
        'methods':{c8.SPARK:c8.metrics(spark,expected,name=c8.SPARK,raw=c8._raw_tally(captured),documents=documents,
                                      wall_s=float(captured.get('wall_s',0))+spark_s,cost_usd=cost if known else 0.),
                   c8.BASELINE:c8.metrics(parser,expected,name=c8.BASELINE,documents=documents,wall_s=parser_s)}}
    for name,value in [('reading.json',spark.model_dump(mode='json')),
                       ('baseline_reading.json',parser.model_dump(mode='json')),('metrics.json',result)]:
        c7.save_once(case/name,value)
    names=('spark_request.json','spark_response.json','spark_raw.json','spark_rejection.json',
           'reading.json','baseline_reading.json','metrics.json')
    c7.save_once(case/'metrics.seal.json',{'files':{n:sha256_file(case/n) for n in names if (case/n).exists()}})
    return result


def run(cohort=COHORT):
    cohort=Path(cohort)
    manifest=claim_run(cohort)
    scores=[]
    for item in manifest['cases']:
        verify_frozen(cohort)
        case=cohort/item['split']
        documents=_documents(json.loads((case/'documents.json').read_text()))
        allowed={(case/name).resolve() for name in item['files']}
        if any(Path(d['path']).resolve() not in allowed for d in documents if d.get('path')):
            raise ValueError('Document references an unfrozen path')
        wait_for_slot()
        verify_frozen(cohort)
        try:
            record_case(case)
        except Exception:
            # Persisted transport/schema failures retain their costs and zero
            # accepted facts; never retry, repair, exclude or replace a case.
            if not (case/'spark_response.json').exists():
                raise
        verify_frozen(cohort)
        wait_for_slot()
        verify_frozen(cohort)
        scores.append(score_case(case))
        print(json.dumps({'completed':case.name,'reading_status':scores[-1]['reading_status']}),flush=True)
    if len(scores)!=POLICY['case_count']:
        raise ValueError('Every sealed case must be included')
    output=c7.aggregate(scores)
    output.update(evaluation_split='sealed_intake_v1',preregistered_policy=POLICY,
        input_sha256=sha256_file(cohort/'blind_freeze.json'),
        source_sha256=sha256_file(cohort/'SOURCE_FREEZE.json'),
        unknown_cost_cases=[s['case'] for s in scores if s['cost_unknown']],
        accounting_note='Reported dollars only; unknown billing listed separately.')
    verify_frozen(cohort)
    c7.save_once(cohort/'metrics.json',output)
    c7.save_once(cohort/'metrics.seal.json',{'metrics_sha256':sha256_file(cohort/'metrics.json'),
        'cases':{s['case']:sha256_file(cohort/s['case']/'metrics.seal.json') for s in scores}})
    return output


if __name__=='__main__':
    print(json.dumps(run(),indent=2,ensure_ascii=False),flush=True)
