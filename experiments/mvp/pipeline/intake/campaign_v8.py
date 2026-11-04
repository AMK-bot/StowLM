"""Preregistered v8 one-attempt authority intake versus its named parser.

Five independently frozen twelve-box cases are all retained, including malformed
model output. A rejected schema receives zero accepted fields, full workload and
no reason-specific trap credit. Literal tally transcription is reported separately.
No rejected output is renamed, dropped, repaired or retried for this experiment.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import IntakeResult, Question, sha256_file, sha256_json
from . import _documents, parser_baseline, read_documents
from .admission import wait_for_slot
from .evaluate_v2 import dependency_fingerprints, runtime_dependencies, write
from .interpretation import content, prompt, proposal, SchemaRejection
from .spark_reader import RecordedClient
from . import campaign_v7 as c7
from .campaign_v7 import business_fields, _get, _overlap, _questions, _blocked, _trap_questions, _MISSING

HERE = Path(__file__).resolve().parent
COHORT = HERE / 'fixtures' / 'authority_v8'
SOURCE_FREEZE = 'SOURCE_FREEZE.json'
BASELINE = c7.BASELINE
SPARK = c7.SPARK
save_once = c7.save_once
POLICY = {
    'policy_version': 8, 'case_count': 5, 'boxes_per_case': 12, 'attempt_limit': 1,
    'schema_failure': 'zero accepted fields; every business field questioned; full workload; no specific trap credit',
    'transport_failure': 'same failure scoring; reported dollars retained, unreported dollars explicitly unknown',
    'validator_failure': 'zero accepted fields and full workload, with exact exception recorded for either method',
    'tally': 'literal raw transcription scored separately, never accepted cargo evidence',
    'derived_business_evidence': ['closed_world_declaration', 'derived_iso_tare'],
    'case_inclusion': 'all five frozen cases, including failures; no substitution, retries or exclusions',
    'after_answers': 'synthetic demo interaction only; never accuracy',
}


def _utc():
    return datetime.now(timezone.utc).isoformat()


def verify_inputs(cohort=None):
    """Hash-only access to blind input bytes before source freeze."""
    cohort=Path(cohort or COHORT).resolve()
    path=cohort/'blind_freeze.json'
    if sha256_file(path)!=(cohort/'blind_freeze.sha256').read_text().split()[0]:
        raise ValueError('The blind input manifest changed')
    manifest=json.loads(path.read_text())
    if not manifest.get('frozen_before_tuning') or not manifest.get('frozen_before_scoring'):
        raise ValueError('Blind fixture freeze must precede tuning and scoring')
    cases=manifest.get('cases',[])
    if len(cases)!=POLICY['case_count'] or any(c.get('rows')!=POLICY['boxes_per_case'] for c in cases):
        raise ValueError('The preregistered cohort is five twelve-box cases')
    splits=[c['split'] for c in cases]
    if len(set(splits))!=len(splits) or any(Path(s).name!=s or not s.startswith('blind_') for s in splits):
        raise ValueError('Invalid or duplicate blind case identity')
    for case in cases:
        if not {'documents.json','expected.json','base_instance.json'} <= set(case['files']):
            raise ValueError('Blind case input manifest must pin documents, truth and base instance')
        for name,expected in case['files'].items():
            if Path(name).name!=name or sha256_file(cohort/case['split']/name)!=expected:
                raise ValueError('A frozen blind input changed')
    return manifest


def freeze(cohort=None):
    cohort=Path(cohort or COHORT).resolve()
    target=cohort/SOURCE_FREEZE
    if target.exists():
        raise ValueError('The v8 implementation is already frozen')
    manifest=verify_inputs(cohort)
    outputs=('spark_request.json','spark_response.json','spark_raw.json','spark_rejection.json',
             'score_attempt.json','metrics.json','reading.json','baseline_reading.json')
    if any((cohort/case['split']/name).exists() for case in manifest['cases'] for name in outputs):
        raise ValueError('Cannot freeze after a blind model read or score')
    if (cohort/'metrics.json').exists() or (cohort/'metrics.seal.json').exists():
        raise ValueError('Cannot freeze after an aggregate blind score')
    value={'schema_version':'stowlm.intake-source-freeze/8','frozen_utc':_utc(),
        'frozen_before_scoring':True,'evaluation_split':'authority_v8_blind',
        'input_freeze_sha256':sha256_file(cohort/'blind_freeze.json'),
        'source_files':{p.name:sha256_file(p) for p in sorted(HERE.glob('*.py'))},
        'prompt_sha256':hashlib.sha256(prompt().encode()).hexdigest(),
        'dependency_files':dependency_fingerprints(),'runtime_dependencies':runtime_dependencies(),
        'preregistered_policy':POLICY,'committed_before_v8_development_usd':'2.59796775',
        'committed_before_campaign_usd':'2.85153200',
        'task_cap_usd':'10','notes':__doc__}
    save_once(target,value)
    return {'source_freeze_sha256':sha256_file(target),'input_freeze_sha256':value['input_freeze_sha256']}


def verify_frozen(cohort=None):
    cohort=Path(cohort or COHORT).resolve()
    source=json.loads((cohort/SOURCE_FREEZE).read_text())
    if source.get('schema_version')!='stowlm.intake-source-freeze/8' or source.get('preregistered_policy')!=POLICY:
        raise ValueError('Unsupported source freeze or changed failure policy')
    if sha256_file(cohort/'blind_freeze.json')!=source['input_freeze_sha256']:
        raise ValueError('The source freeze names different blind inputs')
    if {p.name:sha256_file(p) for p in sorted(HERE.glob('*.py'))}!=source['source_files']:
        raise ValueError('The frozen intake source changed')
    if hashlib.sha256(prompt().encode()).hexdigest()!=source['prompt_sha256']:
        raise ValueError('The effective interpretation prompt changed')
    if dependency_fingerprints()!=source['dependency_files'] or runtime_dependencies()!=source['runtime_dependencies']:
        raise ValueError('The frozen dependencies changed')
    return verify_inputs(cohort)


def _blind(case):
    return Path(case).name.startswith('blind_')


def _input(case):
    case=Path(case).resolve()
    frozen=None
    if _blind(case):
        manifest=verify_frozen(case.parent)
        frozen=next((c for c in manifest['cases'] if c['split']==case.name),None)
        if frozen is None:
            raise ValueError('Case is absent from the blind freeze')
    documents=_documents(json.loads((case/'documents.json').read_text()))
    if frozen:
        allowed={(case/n).resolve() for n in frozen['files']}
        if any(Path(d['path']).resolve() not in allowed for d in documents if d.get('path')):
            raise ValueError('Document references an unfrozen input path')
    return documents


def _decode(text):
    text=text.strip()
    if text.startswith('```') and text.endswith('```'):
        _,separator,rest=text.partition('\n')
        if not separator:
            raise ValueError('Malformed fenced interpretation')
        text=rest.rsplit('```',1)[0].strip()
    return json.loads(text)


def _raw_tally(captured):
    """Read literal cells without repairing a malformed enclosing proposal."""
    try:
        emitted=_decode(captured.get('text',''))
    except (ValueError,TypeError,AttributeError):
        return {'observations':[]}
    if not isinstance(emitted,dict) or not isinstance(emitted.get('observations'),list):
        return {'observations':[]}
    return {'observations':[o for o in emitted['observations'] if isinstance(o,dict)
                            and isinstance(o.get('row_id'),str) and isinstance(o.get('field'),str)
                            and isinstance(o.get('document'),str) and 'value' in o]}


def authenticated_capture(case,documents):
    """Authenticate immutable request, response, proposal or rejection together."""
    case=Path(case).resolve()
    if _blind(case):
        verify_frozen(case.parent)
    request=json.loads((case/'spark_request.json').read_text())
    captured=json.loads((case/'spark_response.json').read_text())
    wire=content(documents)
    digest=sha256_json(wire)
    prompt_digest=hashlib.sha256(prompt().encode()).hexdigest()
    if (request.get('schema_version')!='stowlm.intake-request/8' or request.get('policy_version')!=8
        or request.get('attempt_limit')!=1 or request.get('wire')!=wire
        or request.get('max_output_tokens')!=24576 or request.get('reasoning_effort')!='low'
        or not isinstance(request.get('started_utc'),str) or not request['started_utc']
        or request.get('documents_sha256')!=digest or request.get('prompt_sha256')!=prompt_digest):
        raise ValueError('Request does not bind the frozen document wire and prompt')
    if captured.get('policy_version')!=8 or captured.get('documents_sha256')!=digest or captured.get('prompt_sha256')!=prompt_digest:
        raise ValueError('Captured response used a different request')
    if captured.get('started_utc')!=request.get('started_utc'):
        raise ValueError('Response is not bound to the saved request attempt')
    raw_path,rejected_path=case/'spark_raw.json',case/'spark_rejection.json'
    if captured.get('status')=='transport_or_client_error':
        if raw_path.exists() or rejected_path.exists():
            raise ValueError('Transport failure cannot have an accepted or rejected proposal')
        return None,captured,'transport_failed'
    if raw_path.exists()==rejected_path.exists():
        raise ValueError('Exactly one immutable raw proposal or schema rejection is required')
    if not captured.get('request_id') or not isinstance(captured.get('text'),str):
        raise ValueError('Captured model response lacks its provider receipt')
    artifact=json.loads((raw_path if raw_path.exists() else rejected_path).read_text())
    meta=artifact.get('metadata',{})
    if (meta.get('policy_version')!=8 or meta.get('request_id')!=captured['request_id']
        or meta.get('raw_text')!=captured['text'] or meta.get('usage')!=captured.get('usage')
        or meta.get('cost_usd')!=captured.get('cost_usd')
        or meta.get('documents_sha256')!=digest or meta.get('prompt_sha256')!=prompt_digest):
        raise ValueError('Recording accounting or provenance differs from captured response')
    attempts=meta.get('attempts',[])
    if len(attempts)!=1 or attempts[0].get('wire')!=wire or attempts[0].get('raw_text')!=captured['text']:
        raise ValueError('Recording is not one captured frozen attempt')
    if raw_path.exists():
        emitted=proposal(_decode(captured['text']),documents)
        if emitted!={k:v for k,v in artifact.items() if k!='metadata'}:
            raise ValueError('Recording proposal changed after capture')
        return artifact,captured,'accepted_schema'
    if artifact.get('status')!='schema_rejected' or artifact.get('attempts')!=attempts:
        raise ValueError('Malformed schema rejection receipt')
    try:
        proposal(_decode(captured['text']),documents)
    except (ValueError,TypeError):
        return None,captured,'schema_rejected'
    raise ValueError('Rejection receipt contains an acceptable proposal')


def authenticated_reading(case,documents):
    raw,_,status=authenticated_capture(case,documents)
    if raw is None:
        raise ValueError(f'No accepted recording: {status}')
    return raw


def _accepted(result,rid,field,questions):
    if _blocked(questions,field):
        return False
    return any(s.get('row_id')==rid and s.get('accepted')
               and (not s.get('derived') or s.get('evidence') in POLICY['derived_business_evidence'])
               and (s.get('field')==field or field.startswith(str(s.get('field'))+'.'))
               for s in result.field_sources)


def tally_accuracy(raw,documents,expected):
    return c7.tally_accuracy(raw,documents,expected)


def metrics(result,expected,*,name,raw=None,documents=(),wall_s=0.,cost_usd=0.):
    result = IntakeResult.model_validate(result)
    rows = {row.get('id'):row for row in result.rows}
    correct = accepted = total = silent = exact = 0
    silent_details = []
    document_questions = [q for q in result.questions if q.row is None]
    stopped = {r['id'] for r in expected['rows']} if document_questions else set()
    for target in expected['rows']:
        rid = target['id']
        row = rows.get(rid,{})
        questions = _questions(result,rid)
        if questions or not row:
            stopped.add(rid)
        row_good = not document_questions and not questions and bool(row)
        wanted = business_fields(target)
        for field,value in wanted.items():
            total += 1
            got = _get(row,field)
            is_accepted = got is not _MISSING and _accepted(result,rid,field,questions)
            accepted += is_accepted
            good = is_accepted and got == value
            correct += good
            row_good = row_good and good
            if got is not _MISSING and not _blocked(questions,field) and got != value:
                silent += 1
                silent_details.append({'row_id':rid,'field':field,'value':got,'expected':value})
        # Extra accepted DG entries/declarations are errors too; a sibling
        # question must never turn them into uncounted defaults.
        for field,value in business_fields(row).items():
            if field not in wanted and not _blocked(questions,field):
                silent += 1
                silent_details.append({'row_id':rid,'field':field,'value':value,'expected':'not declared by ground truth'})
                row_good = False
        exact += row_good
    expected_ids = {r['id'] for r in expected['rows']}
    for rid,row in rows.items():
        if rid not in expected_ids and not _blocked(_questions(result,rid),'id'):
            silent += 1
            silent_details.append({'row_id':rid,'field':'id','value':rid,'expected':'not in ground truth'})
    traps = []
    for trap in expected.get('traps',[]):
        matches = _trap_questions(result,trap)
        questions = _questions(result,trap['row_id'])
        field = trap.get('field')
        field_wrong = [item for item in silent_details if item['row_id'] == trap['row_id'] and _overlap(item['field'],field)]
        # A plausible exception left completely unstopped is a silent business
        # error even when the retained old value happens to match the old key.
        unstopped = not matches and not any(_overlap(q.field,field) for q in questions)
        traps.append({'trap':trap,'caught':bool(matches),'questions':matches,
                      'silent_wrong':bool(field_wrong) or bool(trap.get('plausible') and unstopped),
                      'unrelated_question_only':bool(questions) and not matches})
    clean_ids = set(expected.get('clean_control_ids',[]))
    clean = [r for r in expected['rows'] if r['id'] in clean_ids]
    cargo_complete = lambda r: r['id'] in rows and not _questions(result,r['id']) and all(
        _accepted(result,r['id'],f,[]) and _get(rows[r['id']],f) == v for f,v in business_fields(r).items())
    plausible = [t for t in traps if t['trap'].get('plausible')]
    return {'method':name,'label':'measured','fields_correct':int(correct),'fields_total':total,
            'fields_accepted':int(accepted),'field_accuracy':correct/total if total else 0.,
            'rows_exact':int(exact),'rows_total':len(expected_ids),'row_exact_accuracy':exact/len(expected_ids) if expected_ids else 0.,
            'boxes_stopped':len(stopped),'planner_workload':len(stopped)/len(expected_ids) if expected_ids else 0.,
            'stopped_box_ids':sorted(stopped),'document_questions':len(document_questions),'silent_wrong_values':int(silent),'silent_wrong_details':silent_details,
            'planted_problems_stopped':sum(t['caught'] for t in traps),'planted_problems_total':len(traps),
            'plausible_traps_stopped':sum(t['caught'] for t in plausible),'plausible_traps_total':len(plausible),
            'silent_wrong_plausible_traps':sum(t['silent_wrong'] for t in plausible),'traps':traps,
            'clean_dg_accepted':sum(cargo_complete(r) and not document_questions for r in clean if r.get('dg')),
            'clean_dg_cargo_accepted':sum(cargo_complete(r) for r in clean if r.get('dg')),
            'clean_dg_total':sum(bool(r.get('dg')) for r in clean),
            'clean_reefers_accepted':sum(cargo_complete(r) and not document_questions for r in clean if r.get('reefer')),
            'clean_reefers_cargo_accepted':sum(cargo_complete(r) for r in clean if r.get('reefer')),
            'clean_reefers_total':sum(bool(r.get('reefer')) for r in clean),
            'tally':tally_accuracy(raw,documents,expected),'wall_s':wall_s,'cost_usd':cost_usd}



def rejection_result(expected,reason='schema_rejected'):
    rows=[{'id':r['id']} for r in expected['rows']]
    questions=[Question(id=f'{r["id"]}:{field}:{reason}',row=i,field=field,reason=reason,
                        prompt='This one-attempt reading was rejected; review the original declaration.')
               for i,r in enumerate(expected['rows']) for field in business_fields(r)]
    return IntakeResult(status='needs_answers',rows=rows,questions=questions,field_sources=[])


def _record_hashes(case):
    names=('spark_request.json','spark_response.json','spark_raw.json','spark_rejection.json')
    return {name:sha256_file(case/name) for name in names if (case/name).exists()}


def score_case(case,*,save=True):
    case=Path(case).resolve()
    blind_case=_blind(case)
    if blind_case and (not save or (case/'metrics.json').exists() or (case/'score_attempt.json').exists()):
        raise ValueError('Blind scores are one-attempt/save-once; dry rescoring is forbidden')
    documents=_input(case)
    raw,captured,status=authenticated_capture(case,documents)
    expected=json.loads((case/'expected.json').read_text())
    base=json.loads((case/'base_instance.json').read_text())
    if blind_case and len(expected['rows'])!=POLICY['boxes_per_case']:
        raise ValueError('Expected row count differs from preregistration')
    attempt={'schema_version':'stowlm.intake-score-attempt/8','started_utc':_utc(),
             'case':case.name,'recordings':_record_hashes(case),'policy':POLICY}
    if blind_case:
        save_once(case/'score_attempt.json',attempt)
    elif save:
        write(case/'score_attempt.json',attempt)
    started=time.monotonic()
    validation_error=None
    if raw is None:
        spark=rejection_result(expected,status)
    else:
        try:
            spark=read_documents(documents,base_instance=base,client=RecordedClient(case/'spark_raw.json'))
        except (SchemaRejection,ValueError,TypeError,KeyError,IndexError) as error:
            status='validation_rejected'
            validation_error={'type':type(error).__name__,'message':str(error)}
            spark=rejection_result(expected,status)
    spark_s=time.monotonic()-started
    started=time.monotonic()
    try:
        baseline=parser_baseline(documents,base_instance=base)
        baseline_status='completed'
    except (ValueError,TypeError,KeyError,IndexError) as error:
        baseline_status='baseline_rejected'
        baseline=rejection_result(expected,baseline_status)
        baseline_error={'type':type(error).__name__,'message':str(error)}
    baseline_s=time.monotonic()-started
    tally_raw=_raw_tally(captured)
    known_cost=captured.get('cost_usd')
    cost_unknown=not isinstance(known_cost,(int,float)) or isinstance(known_cost,bool)
    cost=0. if cost_unknown else known_cost
    wall_s=float(captured.get('wall_s',0))
    output={'schema_version':'stowlm.intake-comparison/8','label':'measured','case':case.name,
        'caption':expected.get('caption',f'{len(expected["rows"])} synthetic boxes; not a whole ship'),
        'scored_utc':_utc(),'recordings':_record_hashes(case),'reading_status':status,
        'validation_error':validation_error,'cost_unknown':cost_unknown,
        'raw_tally_separate_from_accepted_cargo':True,
        'methods':{SPARK:metrics(spark,expected,name=SPARK,raw=tally_raw,documents=documents,
                                wall_s=wall_s+spark_s,cost_usd=cost),
                   BASELINE:metrics(baseline,expected,name=BASELINE,documents=documents,wall_s=baseline_s)},
        'baseline_status':baseline_status,'offline_validation_wall_s':spark_s,
        'cost_note':'Reported request cost only; unknown provider billing remains unknown, never assumed free.'}
    if baseline_status!='completed':
        output['baseline_error']=baseline_error
    if save:
        if blind_case:
            verify_frozen(case.parent)
            save_once(case/'reading.json',spark.model_dump(mode='json'))
            save_once(case/'baseline_reading.json',baseline.model_dump(mode='json'))
            save_once(case/'metrics.json',output)
            files={**output['recordings'],**{n:sha256_file(case/n) for n in ('score_attempt.json','reading.json','baseline_reading.json','metrics.json')}}
            save_once(case/'metrics.seal.json',{'schema_version':'stowlm.intake-score-seal/8','files':files})
        else:
            write(case/'reading.json',spark.model_dump(mode='json'))
            write(case/'baseline_reading.json',baseline.model_dump(mode='json'))
            write(case/'metrics.json',output)
    return output


def verify_case_seal(case):
    case=Path(case)
    seal=json.loads((case/'metrics.seal.json').read_text())
    if seal.get('schema_version')!='stowlm.intake-score-seal/8':
        raise ValueError('Invalid case score seal')
    for name,digest in seal['files'].items():
        if Path(name).name!=name or sha256_file(case/name)!=digest:
            raise ValueError('A case recording or score changed after publication')
    metrics=json.loads((case/'metrics.json').read_text())
    if metrics.get('case')!=case.name or _record_hashes(case)!=metrics['recordings']:
        raise ValueError('Case recordings were substituted after scoring')
    authenticated_capture(case,_input(case))
    return metrics


def aggregate(cases):
    if len(cases)!=POLICY['case_count'] or len({c['case'] for c in cases})!=POLICY['case_count']:
        raise ValueError('Every preregistered case must appear exactly once')
    if any(c['methods'][name]['rows_total']!=POLICY['boxes_per_case'] for c in cases for name in (SPARK,BASELINE)):
        raise ValueError('An aggregate case changed its preregistered denominator')
    output=c7.aggregate(cases)
    output.update(schema_version='stowlm.intake-comparison/8',evaluation_split='authority_v8_blind',
        preregistered_policy=POLICY,
        rejected_case_count=sum(c['reading_status']!='accepted_schema' for c in cases),
        unknown_cost_case_count=sum(bool(c.get('cost_unknown')) for c in cases),
        accounting_note='Dollar total sums reported costs; cases with unknown billing are named separately.',
        unknown_cost_cases=[c['case'] for c in cases if c.get('cost_unknown')])
    return output


def blind():
    target=COHORT/'metrics.json'
    if target.exists():
        raise ValueError('The blind cohort score is already frozen')
    manifest=verify_frozen()
    cases=[]
    for item in manifest['cases']:
        verify_frozen()
        case=COHORT/item['split']
        if (case/'metrics.json').exists():
            score=verify_case_seal(case)
        else:
            if not (case/'spark_response.json').exists():
                from .record_v8 import record_case
                wait_for_slot()
                try:
                    record_case(case)
                except Exception:
                    # A persisted failure is scored under the policy fixed
                    # before the call. No second call or repaired proposal.
                    if not (case/'spark_response.json').exists():
                        raise
            wait_for_slot()
            score=score_case(case)
        cases.append(score)
        print(json.dumps({'scored':case.name,'status':score['reading_status'],
            'methods':{name:{k:v[k] for k in ('field_accuracy','boxes_stopped','planted_problems_stopped','silent_wrong_values')}
                       for name,v in score['methods'].items()}}),flush=True)
    output=aggregate(cases)
    verify_frozen()
    save_once(target,output)
    save_once(COHORT/'metrics.seal.json',{'schema_version':'stowlm.intake-cohort-seal/8',
        'metrics_sha256':sha256_file(target),
        'case_seals':{c['case']:sha256_file(COHORT/c['case']/'metrics.seal.json') for c in cases}})
    return output


def demo_answers(case,*,save=True,max_rounds=8):
    # c7's helper only processes explicit nonblind synthetic planner answers;
    # it does not publish another accuracy score or bypass v8 source policy.
    case=Path(case).resolve()
    if _blind(case):
        raise ValueError('No blind answer-key interaction')
    authenticated_reading(case,_input(case))
    return c7.demo_answers(case,save=save,max_rounds=max_rounds)


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('action',choices=('freeze','blind','score','demo'))
    cli.add_argument('case',nargs='?',type=Path)
    args=cli.parse_args()
    if args.action=='freeze':
        result=freeze()
    elif args.action=='blind':
        result=blind()
    else:
        wait_for_slot()
        result=score_case(args.case) if args.action=='score' else demo_answers(args.case)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
