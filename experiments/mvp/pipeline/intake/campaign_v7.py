"""Frozen one-shot comparison: Spark plus software versus Literal-header parser.

The fixture author freezes documents before tuning. This module pins source,
project dependencies and runtime before any blind model read or score. Planner
corrections are a separate demo exercise, never an accuracy measurement.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from experiments.mvp.pipeline.contract.schema import IntakeResult, sha256_file, sha256_json
from . import _documents, answer_questions, parser_baseline, read_documents
from .admission import wait_for_slot
from .evaluate_v2 import dependency_fingerprints, runtime_dependencies, write
from .interpretation import content, prompt, proposal
from .spark_reader import RecordedClient

HERE = Path(__file__).resolve().parent
COHORT = HERE / 'fixtures' / 'realistic_v7'
SOURCE_FREEZE = 'SOURCE_FREEZE.json'
BASELINE = 'Literal-header parser'
SPARK = 'Muse Spark + software'
_DG_FIELDS = ('un_number', 'imdg_class', 'packing_group', 'limited_quantity', 'marine_pollutant')
_BASIC = ('id', 'iso_type', 'pol', 'pod', 'gross_mass_kg', 'tare_kg', 'empty', 'reefer')
_MISSING = object()
_REASON_ALIASES = {
    'unbooked_vessel': {'wrong_vessel'}, 'amendment_pod': {'amendment_review','cross_document_conflict'},
    'cod': {'amendment_review','cross_document_conflict'}, 'shut_out': {'amendment_review','booking_not_confirmed'},
    'fahrenheit': {'fahrenheit_setpoint'}, 'temperature_range': {'reefer_setpoint_range'},
    'empty_gross': {'empty_mass_mismatch'}, 'tare_band': {'implausible_tare'},
    'unknown_un': {'unknown_un','unresolved_imdg_entry'}, 'missing_setpoint': {'missing_reefer_setpoint'},
}


def save_once(path,value):
    """Atomically publish one immutable score/freeze without replacing a peer."""
    temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    try:
        os.link(temporary,path)
    except FileExistsError as exc:
        raise ValueError('Frozen output already exists; overwrite forbidden') from exc
    finally:
        temporary.unlink(missing_ok=True)


def verify_inputs(cohort=None):
    """Hash bytes only; do not interpret blind documents or expected answers."""
    cohort = Path(cohort or COHORT).resolve()
    path = cohort / 'blind_freeze.json'
    if sha256_file(path) != (cohort / 'blind_freeze.sha256').read_text().split()[0]:
        raise ValueError('The blind input manifest changed')
    manifest = json.loads(path.read_text())
    if not manifest.get('frozen_before_tuning') or not manifest.get('frozen_before_scoring'):
        raise ValueError('Blind fixture freeze must precede tuning and scoring')
    if len(manifest['cases']) < 5:
        raise ValueError('At least five blind cases are required')
    for case in manifest['cases']:
        for name, expected in case['files'].items():
            if Path(name).name != name or sha256_file(cohort / case['split'] / name) != expected:
                raise ValueError('A frozen blind input changed')
    return manifest


def freeze(cohort=None):
    cohort = Path(cohort or COHORT).resolve()
    target = cohort / SOURCE_FREEZE
    if target.exists():
        raise ValueError('The v7 implementation is already frozen')
    manifest = verify_inputs(cohort)
    if any((cohort / case['split'] / name).exists() for case in manifest['cases']
           for name in ('spark_raw.json','spark_response.json','metrics.json','reading.json')):
        raise ValueError('Cannot freeze after a blind model read or score')
    value = {'schema_version':'stowlm.intake-source-freeze/7',
             'frozen_utc':datetime.now(timezone.utc).isoformat(), 'frozen_before_scoring':True,
             'evaluation_split':'realistic_v7_blind',
             'input_freeze_sha256':sha256_file(cohort / 'blind_freeze.json'),
             'source_files':{path.name:sha256_file(path) for path in sorted(HERE.glob('*.py'))},
             'prompt_sha256':hashlib.sha256(prompt().encode()).hexdigest(),
             'dependency_files':dependency_fingerprints(), 'runtime_dependencies':runtime_dependencies(),
             'notes':'Independent fixture bytes and header variants frozen before development tuning; source and prompt frozen before any blind reading. No after-answers accuracy.'}
    save_once(target,value)
    return {'source_freeze_sha256':sha256_file(target),'input_freeze_sha256':value['input_freeze_sha256']}


def verify_frozen(cohort=None):
    cohort = Path(cohort or COHORT).resolve()
    source = json.loads((cohort / SOURCE_FREEZE).read_text())
    if source.get('schema_version') != 'stowlm.intake-source-freeze/7':
        raise ValueError('Unsupported source freeze')
    if sha256_file(cohort / 'blind_freeze.json') != source['input_freeze_sha256']:
        raise ValueError('The source freeze names different blind inputs')
    current = {path.name:sha256_file(path) for path in sorted(HERE.glob('*.py'))}
    if current != source['source_files']:
        raise ValueError('The frozen intake source changed')
    if hashlib.sha256(prompt().encode()).hexdigest() != source['prompt_sha256']:
        raise ValueError('The effective interpretation prompt changed')
    if dependency_fingerprints() != source.get('dependency_files'):
        raise ValueError('The frozen project dependencies changed')
    if runtime_dependencies() != source.get('runtime_dependencies'):
        raise ValueError('The frozen runtime dependencies changed')
    return verify_inputs(cohort)


def _blind(case):
    return case.name.startswith('blind_')


def _input(case):
    if _blind(case):
        manifest = verify_frozen(case.parent)
        frozen_case = next((item for item in manifest['cases'] if item['split'] == case.name), None)
        if frozen_case is None:
            raise ValueError('Case is absent from the blind freeze')
    else:
        frozen_case = None
    documents = _documents(json.loads((case / 'documents.json').read_text()))
    if frozen_case:
        allowed = {(case / name).resolve() for name in frozen_case['files']}
        if any(Path(doc['path']).resolve() not in allowed for doc in documents if doc.get('path')):
            raise ValueError('Document references an unfrozen input path')
    return documents


def authenticated_reading(case,documents):
    """Bind our immutable captured response, exact prompt and exact document wire."""
    case = Path(case).resolve()
    if _blind(case):
        verify_frozen(case.parent)
    raw = json.loads((case / 'spark_raw.json').read_text())
    captured = json.loads((case / 'spark_response.json').read_text())
    meta = raw.get('metadata',{})
    if (not meta.get('request_id') or meta.get('request_id') != captured.get('request_id')
            or not meta.get('raw_text') or captured.get('text') != meta['raw_text']):
        raise ValueError('Recording does not match its captured Spark response')
    if meta.get('documents_sha256') != sha256_json(content(documents)):
        raise ValueError('Recording does not bind the frozen document wire')
    if meta.get('prompt_sha256') != hashlib.sha256(prompt().encode()).hexdigest():
        raise ValueError('Recording used a different effective prompt')
    if meta.get('cost_usd') != captured.get('cost_usd') or meta.get('usage') != captured.get('usage'):
        raise ValueError('Recording accounting differs from captured response')
    text = captured['text'].strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.split('\n',1)[1].rsplit('```',1)[0].strip()
    emitted = proposal(json.loads(text),documents)
    if emitted != {key:value for key,value in raw.items() if key != 'metadata'}:
        raise ValueError('Recording proposal changed after capture')
    return raw


def _get(row,field,default=_MISSING):
    value = row
    try:
        for part in field.split('.'):
            value = value[int(part)] if isinstance(value,list) else value[part]
        return value
    except (TypeError,KeyError,IndexError,ValueError):
        return default


def business_fields(row):
    """Count declared cargo facts, excluding classifications and permission defaults."""
    fields = {field:row[field] for field in _BASIC if field in row}
    if row.get('reefer') and 'reefer_setpoint_c' in row:
        fields['reefer_setpoint_c'] = row['reefer_setpoint_c']
    if 'dg' in row and not isinstance(row['dg'],dict):
        fields['dg'] = row['dg']
    elif isinstance(row.get('dg'),dict):
        def entry(value,prefix):
            for field in _DG_FIELDS:
                if field in value:
                    fields[f'{prefix}.{field}'] = value[field]
            for i,child in enumerate(value.get('additional',[])):
                entry(child,f'{prefix}.additional.{i}')
        entry(row['dg'],'dg')
    return fields


def _overlap(field,other):
    return isinstance(field,str) and isinstance(other,str) and (field == other or field.startswith(other+'.') or other.startswith(field+'.'))


def _questions(result,rid):
    return [q for q in result.questions if q.row is not None and 0 <= q.row < len(result.rows)
            and result.rows[q.row].get('id') == rid]


def _blocked(questions,field):
    # A row-level question never hides a wrong field. Exact/ancestor field
    # questions block only their own branch, not unrelated declared values.
    return any(q.field is not None and (q.field == field or field.startswith(q.field+'.')) for q in questions)


def _accepted(result,rid,field,questions):
    if _blocked(questions,field):
        return False
    return any(source.get('row_id') == rid and source.get('accepted') and not source.get('derived')
               and (source.get('field') == field or field.startswith(str(source.get('field'))+'.'))
               for source in result.field_sources)


def _trap_questions(result,trap):
    rid,field,kind = trap['row_id'],trap.get('field'),trap['kind']
    reasons = {trap.get('reason')} | _REASON_ALIASES.get(kind,set())
    matches = []
    for q in _questions(result,rid):
        if q.reason not in reasons:
            continue
        amendment = kind in {'amendment_pod','cod','shut_out'} and q.field == 'amendment' and q.reason == 'amendment_review'
        if not amendment and not _overlap(q.field,field):
            continue
        # Missing a field is not finding its wrong declared value.
        if q.reason == 'wrong_vessel' and ('None' in q.candidates or 'None' in q.prompt):
            continue
        matches.append(q.id)
    return matches


def tally_accuracy(raw,documents,expected):
    images = {d['id'] for d in documents if str(d.get('mime_type','')).startswith('image/')}
    observations = raw.get('observations',[]) if raw else []
    cells = []
    for cell in expected.get('tally_cells',[]):
        values = [o.get('value') for o in observations if o.get('document') in images
                  and o.get('row_id') == cell['row_id'] and o.get('field') == cell['field'] and not o.get('uncertain')]
        good = bool(values) and all(value == cell['value'] for value in values)
        cells.append({'row_id':cell['row_id'],'field':cell['field'],'correct':good,'values':values})
    return {'cells_correct':sum(c['correct'] for c in cells),'cells_total':len(cells),'cells':cells}


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


def score_case(case,*,save=True):
    case = Path(case).resolve()
    if _blind(case) and ((case / 'metrics.json').exists() or not save):
        raise ValueError('Blind scores are save-once; raw substitution and dry rescoring are forbidden')
    documents = _input(case)
    raw = authenticated_reading(case,documents)
    expected = json.loads((case / 'expected.json').read_text())
    base = json.loads((case / 'base_instance.json').read_text())
    started = time.monotonic()
    spark = read_documents(documents,base_instance=base,client=RecordedClient(case / 'spark_raw.json'))
    spark_validation_s = time.monotonic()-started
    started = time.monotonic()
    baseline = parser_baseline(documents,base_instance=base)
    baseline_s = time.monotonic()-started
    meta = raw['metadata']
    output = {'schema_version':'stowlm.intake-comparison/7','label':'measured','case':case.name,
              'caption':expected['caption'],'scored_utc':datetime.now(timezone.utc).isoformat(),
              'reading_sha256':sha256_file(case / 'spark_raw.json'),
              'response_sha256':sha256_file(case / 'spark_response.json'),
              'methods':{SPARK:metrics(spark,expected,name=SPARK,raw=raw,documents=documents,
                                     wall_s=float(meta.get('wall_s',0))+spark_validation_s,cost_usd=float(meta.get('cost_usd',0))),
                         BASELINE:metrics(baseline,expected,name=BASELINE,documents=documents,wall_s=baseline_s)},
              'offline_validation_wall_s':spark_validation_s}
    if save:
        if _blind(case): verify_frozen(case.parent)
        write(case / 'reading.json',spark.model_dump(mode='json'))
        write(case / 'baseline_reading.json',baseline.model_dump(mode='json'))
        if _blind(case):
            save_once(case / 'metrics.json',output)
            save_once(case / 'metrics.seal.json',{'metrics_sha256':sha256_file(case / 'metrics.json')})
        else:
            write(case / 'metrics.json',output)
    return output


def aggregate(cases):
    if not cases:
        raise ValueError('Cannot aggregate an empty cohort')
    fields = ('fields_correct','fields_total','fields_accepted','rows_exact','rows_total','boxes_stopped',
              'silent_wrong_values','planted_problems_stopped','planted_problems_total','plausible_traps_stopped',
              'plausible_traps_total','silent_wrong_plausible_traps','clean_dg_accepted','clean_dg_total',
              'clean_reefers_accepted','clean_reefers_total','clean_dg_cargo_accepted','clean_reefers_cargo_accepted','document_questions','wall_s','cost_usd')
    methods = {}
    for name in (SPARK,BASELINE):
        value = {key:sum(case['methods'][name][key] for case in cases) for key in fields}
        value.update(method=name,label='measured',field_accuracy=value['fields_correct']/value['fields_total'] if value['fields_total'] else 0.,
                     row_exact_accuracy=value['rows_exact']/value['rows_total'] if value['rows_total'] else 0.,
                     planner_workload=value['boxes_stopped']/value['rows_total'] if value['rows_total'] else 0.,
                     tally={key:sum(case['methods'][name]['tally'][key] for case in cases) for key in ('cells_correct','cells_total')})
        methods[name] = value
    count = methods[SPARK]['rows_total']
    return {'schema_version':'stowlm.intake-comparison/7','label':'measured','evaluation_split':'realistic_v7_blind',
            'scored_utc':datetime.now(timezone.utc).isoformat(),'cases':cases,'case_count':len(cases),
            'box_count':count,'caption':f'{len(cases)} labelled synthetic cases, {count} boxes; not a whole ship or a 195-box test.',
            'methods':methods}


def blind():
    target = COHORT / 'metrics.json'
    if target.exists():
        raise ValueError('The blind cohort score is already frozen')
    manifest = verify_frozen()
    cases = []
    for item in manifest['cases']:
        verify_frozen()
        case = COHORT / item['split']
        if (case / 'metrics.json').exists():
            seal = json.loads((case / 'metrics.seal.json').read_text())
            if seal.get('metrics_sha256') != sha256_file(case / 'metrics.json'):
                raise ValueError('A resumed case score changed after publication')
            score = json.loads((case / 'metrics.json').read_text())
            if score['reading_sha256'] != sha256_file(case / 'spark_raw.json') or score['response_sha256'] != sha256_file(case / 'spark_response.json'):
                raise ValueError('A resumed case recording changed after scoring')
        else:
            if not (case / 'spark_raw.json').exists():
                from .record_v7 import record_case
                wait_for_slot()
                record_case(case)
            wait_for_slot()
            score = score_case(case)
        cases.append(score)
        print(json.dumps({'scored':case.name,'methods':{name:{k:value[k] for k in ('field_accuracy','boxes_stopped','planted_problems_stopped','silent_wrong_values')} for name,value in score['methods'].items()}}),flush=True)
    output = aggregate(cases)
    verify_frozen()
    save_once(target,output)
    return output


def demo_answers(case,*,save=True,max_rounds=8):
    """Synthetic planner interaction and assembly receipt, never an accuracy score."""
    case = Path(case).resolve()
    if _blind(case):
        raise ValueError('Do not convert blind answer keys into accuracy')
    documents = _input(case)
    authenticated_reading(case,documents)
    expected = json.loads((case / 'expected.json').read_text())
    base = json.loads((case / 'base_instance.json').read_text())
    result = read_documents(documents,base_instance=base,client=RecordedClient(case / 'spark_raw.json'))
    truth = {r['id']:r for r in expected['rows']}
    rounds = []
    for _ in range(max_rounds):
        answers = {}
        for q in result.questions:
            if q.reason == 'unmapped_sheet_headers' and str(q.field).startswith('sheet:'):
                answers[q.id] = {'action':'reviewed_sheet'}
                continue
            if q.row is None or q.row >= len(result.rows): continue
            rid = result.rows[q.row].get('id')
            if q.reason == 'reader_referral': continue
            if any(a.get('row_id') == rid and a.get('value') == 'shut_out' for a in expected.get('answers',[])):
                answers[q.id] = {'action':'shut_out'}
                continue
            if q.reason == 'amendment_review': answers[q.id] = {'action':'reviewed'}; continue
            if q.reason == 'duplicate_id': answers[q.id] = 'keep_one'; continue
            prescribed = next((a for a in expected.get('answers',[]) if a['row_id']==rid and (a.get('field')==q.field or _overlap(a.get('field'),q.field))),None)
            if prescribed and prescribed.get('value') == 'shut_out': answers[q.id] = {'action':'shut_out'}; continue
            if q.field == 'booked_vessel': value = base['rule_overrides']['intake.policy']['vessel']; field = q.field
            elif q.field == 'booking_status': value = 'booked'; field = q.field
            elif rid in truth and isinstance(q.field,str):
                field = q.field
                value = _get(truth[rid],field)
                while value is _MISSING and '.' in field:
                    field = field.rsplit('.',1)[0]
                    value = _get(truth[rid],field)
                if value is _MISSING: continue
            else: continue
            answers[q.id] = {'value':value,'field':field,'quote':f'Synthetic planner confirms {rid} {field}; not model output.'}
        if not answers: break
        rounds.append(answers)
        previous = sha256_json(result.model_dump(mode='json'))
        result = answer_questions(result,answers)
        if sha256_json(result.model_dump(mode='json')) == previous: break
        if result.status == 'complete': break
    from .measure import assemble_rows
    assembled = sha256_json(assemble_rows(base,result.rows).model_dump(mode='json')) if result.status=='complete' else None
    from stowage.model.instance import Instance
    base_sha = sha256_json(Instance.model_validate(base).model_dump(mode='json'))
    receipt = {'label':'synthetic planner interaction; not accuracy','rounds':rounds,'status':result.status,
               'assembled_sha256':assembled,'base_sha256':base_sha,'assembled_sha256_equals_base':assembled==base_sha}
    if save:
        write(case / 'answers.json',{key:value for answers in rounds for key,value in answers.items()})
        write(case / 'final.json',result.model_dump(mode='json'))
        write(case / 'assembly.json',receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('freeze','blind','score','demo'))
    parser.add_argument('case',nargs='?',type=Path)
    args = parser.parse_args()
    if args.action in {'score','demo'} and args.case is None: parser.error('This action requires a case directory')
    if args.action != 'freeze': wait_for_slot()
    output = freeze() if args.action=='freeze' else blind() if args.action=='blind' else score_case(args.case) if args.action=='score' else demo_answers(args.case)
    print(json.dumps(output,indent=2,ensure_ascii=False),flush=True)
