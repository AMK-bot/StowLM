"""Sheet parsing plus independently corroborated paper interpretation, v7."""
from collections import defaultdict
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import re
import time

from experiments.mvp.pipeline.contract.schema import IntakeResult,Mode,Question,StageRecord,sha256_json,sha256_file
from .cargo_gates import validate_cargo_rows
from .paper import baseline_paper,grounded,normalize,source_unit,fingerprint,_ID
from .provenance import is_csv_document,is_image_document
from .sheets import parse_sheet,baseline_mapping,BASELINE,SHEET_METADATA
from .validation import _put


def token(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        return json.dumps(float(value),allow_nan=False)
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False)


def _declaration_origin(source,document):
    """Forwarding a quoted declaration doesn't create another independent form."""
    if source['source_kind'] in ('image','sheet'):
        return (source['source_kind'],fingerprint(document))
    quote=source.get('quote','')
    lines=[line for line in quote.splitlines() if source['row_id'] in line]
    if lines:
        quote=' '.join(line[line.index(source['row_id']):] for line in lines)
    quote=' '.join(re.sub(r'(?m)^\s*>+\s*','',quote).split()).casefold()
    origin=document.get('source_origin',document.get('origin_document',document.get('origin_id')))
    role=document.get('role') or 'unspecified'
    if role in ('email','forwarded_email','correspondence'):
        role='correspondence'
    return ('paper',str(origin) if origin is not None else role,quote)


def _uncovered_columns(document,parsed,literal):
    mapping=parsed.get('mapping')
    if mapping is None:
        return ['unmapped header row']
    header_row=mapping['header_row']
    text=document.get('text',document.get('content'))
    if not isinstance(text,str):
        text=Path(document['path']).read_text(encoding='utf-8-sig')
    reader=csv.reader(io.StringIO(text.lstrip('\ufeff')),strict=True)
    records=[]
    try:
        while True:
            row=reader.line_num+1
            records.append((row,next(reader)))
    except StopIteration:
        pass
    except csv.Error:
        return ['invalid CSV']
    headers=next((cells for row,cells in records if row==header_row),[])
    covered={item['column'] for item in mapping['columns']}
    if literal.get('header_row')==header_row:
        covered.update(item['column'] for item in literal['columns'])
    return [f'{column}: {header}' for column,header in enumerate(headers,1)
            if header.strip() and column not in covered and any(
                row>header_row and len(cells)>=column and cells[column-1].strip() for row,cells in records)]


def evaluate(raw,documents,base,*,confirmations=None,shut_out=(),duplicate_resolutions=(),amendment_reviews=(),reader_resolutions=(),reviewed_sheets=(),policy_version=7):
    confirmations=confirmations or {}
    docs={d['id']:d for d in documents}
    authority=None
    if policy_version>=8:
        from . import authority
    booking_refs={d.get('booking_ref') for d in documents if d.get('role')=='booking' and d.get('booking_ref')}
    booking_members={ref:set().union(*(set(_ID.findall(str(d.get('text','')))) for d in documents if d.get('role')=='booking' and d.get('booking_ref')==ref)) for ref in booking_refs}
    invalid_scope_roles=set()
    grouped=defaultdict(lambda:defaultdict(list))
    sources,questions,findings=[],[],[]
    excluded=set(shut_out)
    ids=set(base['load_list'])-excluded
    maps={}
    for mapping in raw.get('header_maps',[]):
        if not isinstance(mapping,dict) or mapping.get('document') not in docs or mapping['document'] in maps:
            raise ValueError('Invalid or duplicate header map')
        maps[mapping['document']]=mapping
    fahrenheit=set()
    def issue(rid,field,reason,prompt,candidates=()):
        item={'row_id':rid,'field':field,'reason':reason,'prompt':prompt,'candidates':[str(v) for v in candidates]}
        if not any(q['row_id']==rid and q['field']==field and q['reason']==reason for q in questions):
            questions.append(item)
    def add(observation,kind):
        rid,field=observation['row_id'],observation['field']
        if rid in excluded:
            return
        ids.add(rid)
        source={**observation,'source_kind':kind,'accepted':False}
        sources.append(source)
        source_doc=docs[source['document']]
        scoped_form=source_doc.get('role') in {'shipping_instructions','vgm','dg_declaration','gate_slip','reefer_list','oog_list'}
        ref=source_doc.get('booking_ref')
        if authority and booking_refs and scoped_form and (ref not in booking_refs or rid not in booking_members.get(ref,set())):
            source['evidence']='wrong_booking_scope'
            invalid_scope_roles.add(source_doc['role'])
            issue(None,'document:'+source_doc['role'],'document_scope_mismatch',f'{source["document"]}: declaration does not match the explicit booking reference and roster.')
            return
        if kind=='sheet' and field in SHEET_METADATA:
            source.update(accepted=True,evidence='verified_metadata')
            return
        if field.endswith(('.packing_form','.ctu_closed','.flashpoint_c')):
            source['evidence']='unknown_contextual_assessment'
            return
        if source.get('temperature_unit')=='F' or str(source.get('unit','')).upper()=='F':
            fahrenheit.add(rid)
        if field=='booking_status' and isinstance(source['value'],str):
            source['value']={'confirmed':'booked','accepted':'booked'}.get(source['value'].strip().lower(),source['value'].strip().lower())
        if field=='booked_vessel' and isinstance(source['value'],str):
            source['value']=source['value'].strip().upper()
        grouped[rid][field].append(source)
    for doc in documents:
        if is_csv_document(doc):
            literal=baseline_mapping(doc) or {'columns':[]}
            canonical={(c['column'],c['field']) for c in literal['columns']}
            parsed=parse_sheet(doc,maps.get(doc['id']))
            uncovered=_uncovered_columns(doc,parsed,literal)
            if uncovered and doc['id'] not in reviewed_sheets:
                issue(None,'sheet:'+doc['id'],'unmapped_sheet_headers',
                      f'{doc["id"]}: review populated sheet columns whose meanings were not verified: '+ '; '.join(uncovered),uncovered)
            independent=parse_sheet(doc) if doc['id'] in maps else {'observations':[],'issues':[]}
            known_columns={c['column']:c['field'] for c in literal['columns']}
            combined={}
            for observation in [*independent['observations'],*parsed['observations']]:
                field=observation['field']
                actual=known_columns.get(observation['column'])
                if actual and actual!=field and not (actual=='dg' and field=='dg_declared'):
                    issue(observation['row_id'],field,'header_field_conflict',f'{observation["row_id"]}: model header meaning {field} conflicts with literal {actual}.',[actual,field])
                    continue
                observation['canonical_header']=(observation['column'],field) in canonical
                key=(observation['row_id'],observation['csv_row'],observation['column'],field,token(observation['value']))
                combined[key]=observation
            for observation in combined.values():
                add(observation,'sheet')
            parsed['issues'].extend(independent['issues'])
            for item in parsed['issues']:
                rid=item.get('row_id')
                if rid in excluded:
                    continue
                if item['reason']=='duplicate_id' and rid in duplicate_resolutions:
                    continue
                # Cell absence belongs to the business gates; a supplementary
                # paper declaration can supply it without a redundant question.
                if item['reason'] in ('missing_cell_value','unmapped_sheet_headers'):
                    continue
                issue(rid,item.get('field'),item['reason'],f'{rid or doc["id"]}: {item["prompt"]}',item.get('candidates',[]))
        else:
            for observation in baseline_paper(doc):
                add(observation,'parser_text')
        # Every amendment is independently registered even if Spark returns no
        # observation. A host must explicitly review its effect, never the model.
        if doc.get('role')=='amendment':
            affected=set(_ID.findall(str(doc.get('text','')))) or {None}
            for rid in affected-excluded:
                if doc['id'] not in amendment_reviews:
                    issue(rid,'amendment','amendment_review',f'{rid or doc["id"]}: independently review amendment {doc["id"]}; retained declarations may differ.',[doc.get('text','')])
    for observation in raw.get('observations',[]):
        doc=docs.get(observation.get('document'))
        if doc is None or observation.get('row_id') in excluded:
            continue
        rid,field=observation['row_id'],observation['field']
        if field=='net_mass_kg' or field.endswith(('.packing_form','.ctu_closed','.flashpoint_c')):
            sources.append({**observation,'accepted':False,'evidence':'unknown_contextual_assessment'})
            continue
        if is_csv_document(doc):
            # Software owns cells. A redundant transcription adds no evidence;
            # it also adds no desk task when this exact source field/value has
            # already been obtained by verified physical cell parsing.
            parsed=grouped[rid].get(field,[])
            redundant=authority and any(s['source_kind']=='sheet' and s['document']==doc['id']
                                       and token(s['value'])==token(normalize(observation)) for s in parsed)
            sources.append({**observation,'accepted':False,'evidence':'rejected_model_sheet'})
            if redundant:
                findings.append({'row_id':rid,'field':field,'document':doc['id'],
                                 'code':'rejected_redundant_sheet_proposal','severity':'info',
                                 'message':'Model cell transcription rejected; software cell binding remains the only evidence.'})
                continue
            issue(rid,field,'model_sheet_value',f'{rid}: sheet values must come from software cell parsing.')
            continue
        if observation.get('uncertain') or not grounded(observation,doc):
            if not authority:
                issue(rid,field,'unsupported_source',f'{rid}: {field} has no definite grounded paper reading; confirm or supply another source.')
            else:
                findings.append({'row_id':rid,'field':field,'document':doc['id'],'code':'rejected_unsupported_proposal',
                                 'severity':'info','message':'Rejected ungrounded model proposal; independently sourced authoritative declarations and all business gates still apply.'})
            sources.append({**observation,'accepted':False,'evidence':'unsupported_paper'})
            continue
        actual_unit=source_unit(observation)
        if actual_unit=='f':
            fahrenheit.add(rid)
        emitted_field='dg_declared' if field=='dg' and observation['value'] is True else field
        add({**observation,'field':emitted_field,'value':normalize(observation)},'image' if is_image_document(doc) else 'paper')
    for exception in raw.get('exceptions',[]):
        doc=docs[exception['document']]
        quote=exception.get('quote','')
        if quote and (is_image_document(doc) or quote in str(doc.get('text',''))):
            rid=exception.get('row_id')
            if rid is None and exception.get('field') is None:
                findings.append({'document':doc['id'],'code':'non_cargo_exception','message':exception.get('reason'),'quote':quote,'severity':'info'})
                continue
            if rid not in excluded:
                issue(rid,exception.get('field'),'model_exception',f'{rid or doc["id"]}: {exception.get("reason","source exception")}',[quote])
    missing_roles=(authority.prepare(documents,base,grouped,add,issue,excluded)|invalid_scope_roles) if authority else set()
    facts={}
    for rid in sorted(ids):
        row={'id':rid}
        if any(o['value'] is True for o in grouped[rid].get('dg_declared',[])) and any(o['value'] is None for o in grouped[rid].get('dg',[])) and 'dg' not in confirmations.get(rid,{}):
            issue(rid,'dg','cross_document_conflict',f'{rid}: DG indicator YES disagrees with a non-DG declaration.',['DG YES','NON-DG'])
        overlaps={parent for parent in ('dg','oog') if parent in grouped[rid] and any(f.startswith(parent+'.') for f in grouped[rid])}
        overlaps={p for p in overlaps if p not in confirmations.get(rid,{})}
        for parent in overlaps:
            issue(rid,parent,'cross_document_conflict',f'{rid}: sources disagree whether {parent} applies.',[str(o['value']) for f,items in grouped[rid].items() if f==parent or f.startswith(parent+'.') for o in items])
        for field,declarations in grouped[rid].items():
            if field=='dg_declared':
                continue
            if any(field==p or field.startswith(p+'.') for p in overlaps):
                continue
            if field=='id':
                # Extraction identity remains verified by an actual source id.
                pass
            if any(field == f or field.startswith(f + '.') for f in confirmations.get(rid, {})):
                for source in declarations:
                    source['superseded_by_planner']=True
                continue
            values={token(o['value']):o['value'] for o in declarations}
            if len(values)>1:
                descriptions=[f'{o["document"]}: {json.dumps(o["value"],ensure_ascii=False)}' for o in declarations]
                issue(rid,field,'cross_document_conflict',f'{rid}: {field} differs: '+ '; '.join(dict.fromkeys(descriptions)),list(dict.fromkeys(descriptions)))
                continue
            independent={fingerprint(docs[o['document']]) for o in declarations}
            origins={_declaration_origin(o,docs[o['document']]) for o in declarations}
            machine=not authority and any(o['source_kind']=='sheet' and o.get('canonical_header') for o in declarations)
            corroborated=len(independent)>=2 and len(origins)>=2
            if field=='special_stowage':
                _put(row,field,next(iter(values.values())))
                for source in declarations:
                    source['evidence']='proposed_instruction'
                continue
            if authority:
                has_authority=any(authority.authoritative(field,docs[o['document']]) for o in declarations)
                photo_authority=any(docs[o['document']].get('role') in authority.field_roles(field) for o in declarations)
                accepted=has_authority or photo_authority and corroborated
                if not accepted:
                    if field=='tare_kg':
                        row['tare_kg']=None
                    if authority.required(field) and not authority.missing_role(field,missing_roles):
                        reason='needs_corroboration' if photo_authority else 'missing_authoritative_declaration'
                        issue(rid,field,reason,f'{rid}: {field} requires its authoritative document; photo/handwriting readings also require independent agreement.',[f'{o["document"]}: {o["value"]}' for o in declarations])
                    for source in declarations:
                        source['evidence']='supplementary_only'
                    continue
            elif not machine and not corroborated:
                issue(rid,field,'needs_corroboration',f'{rid}: {field} is read from one paper/image source only. Confirm it or supply an independent agreeing source.',[f'{o["document"]}: {o["value"]}' for o in declarations])
                continue
            _put(row,field,next(iter(values.values())))
            for source in declarations:
                source['accepted']=True
                source['evidence']='closed_world_declaration' if source.get('closed_world') else 'authoritative_declaration' if authority and authority.authoritative(field,docs[source['document']]) else 'verified_cell' if source['source_kind']=='sheet' else 'corroborated_sources'
                source['corroborating_documents']=sorted({o['document'] for o in declarations if o['document']!=source['document']})
                if source.get('temperature_unit')=='F' or str(source.get('unit','')).upper()=='F':
                    row['temperature_unit']='F'
        vgm_sources=grouped[rid].get('gross_mass_kg',[])
        if not (authority and 'vgm' in missing_roles) and not any(docs[o['document']].get('role')=='vgm' or re.search(r'vgm|verified|รับรอง',o.get('header',o.get('quote','')),re.I)
                   or o['source_kind']=='sheet' and o.get('header')=='gross_mass_kg' for o in vgm_sources) and 'gross_mass_kg' not in confirmations.get(rid,{}):
            issue(rid,'gross_mass_kg','missing_vgm',f'{rid}: gate/cargo weight cannot replace a VGM declaration.')
        for field,value in confirmations.get(rid,{}).items():
            _put(row,field,value)
            sources.append({'row_id':rid,'field':field,'value':value,'document':'planner_answer','quote':json.dumps(value,ensure_ascii=False),'source_kind':'planner','accepted':True,'evidence':'planner_confirmation'})
        if rid in fahrenheit and 'reefer_setpoint_c' not in confirmations.get(rid,{}):
            row['temperature_unit']='F'
        facts[rid]=row
    # Planner corrections resolve source questions for that exact field only.
    questions=[q for q in questions if q['row_id'] not in excluded and not any(
        q['field']==f or str(q['field']).startswith(f+'.') for f in confirmations.get(q['row_id'],{}))]
    checked=validate_cargo_rows(facts,base,field_sources=sources)
    for q in checked['questions']:
        if authority and q['reason'] in ('missing_declaration','missing_dg_declaration','container_validation','missing_reefer_setpoint','missing_vgm'):
            prior=[p for p in questions if p['row_id']==q['row_id'] and p['field'] is not None and q['field'] is not None and
                   (p['field']==q['field'] or p['field'].startswith(q['field']+'.') or q['field'].startswith(p['field']+'.'))]
            if prior:
                continue
            if q['reason']=='container_validation' and str(q['field']).split('.')[0] not in facts[q['row_id']]:
                continue
        if authority and authority.missing_role(q['field'] or '',missing_roles) and q['reason'] in ('missing_declaration','missing_dg_declaration','container_validation','missing_reefer_setpoint','wrong_vessel','booking_not_confirmed','missing_vgm'):
            continue
        if q['reason']=='reader_referral' and q['row_id'] in reader_resolutions:
            continue
        issue(q['row_id'],q['field'],q['reason'],q['prompt'],q.get('candidates',[]))
    if authority:
        authority.dependency_questions(checked['rows'],questions,issue)
    indexes={rid:i for i,rid in enumerate(checked['row_ids'])}
    out_questions=[]
    for q in questions:
        q.pop('row',None)
        rid=q.pop('row_id')
        out_questions.append(Question(id=f'{rid or "document"}:{q["field"] or "row"}:{q["reason"]}',row=indexes.get(rid),**q))
    for source in checked['field_sources']:
        source['row']=indexes.get(source['row_id'])
        if any(q.row==source['row'] and (q.field is None or q.field==source['field'] or source['field'].startswith(q.field+'.') or q.field.startswith(source['field']+'.')) for q in out_questions):
            source['accepted']=False
    findings=findings+checked['findings']+[{'row':q.row,'field':q.field,'code':q.reason,'message':q.prompt,'severity':'blocking'} for q in out_questions]
    return checked['rows'],out_questions,checked['field_sources'],findings,checked['row_ids']


def finish(raw,documents,base,*,state=None,records=(),reader_evidence=()):
    state=deepcopy(state or {})
    started=time.monotonic()
    rows,questions,sources,findings,row_ids=evaluate(raw,documents,base,policy_version=raw.get('metadata',{}).get('policy_version',7),**state)
    meta=raw.get('metadata',{})
    context={'version':7,'raw':raw,'documents':documents,'base_instance':base,'state':state,'row_ids':row_ids,'reader_evidence':list(reader_evidence)}
    files=('__init__.py','realistic.py','authority.py','paper.py','interpretation.py','sheets.py','cargo_gates.py',
           'provenance.py','text_provenance.py','validation.py','spark_reader.py','measure.py')
    note={'intake_context':context,'findings':findings,'caption':f'Synthetic intake: {len(rows)} boxes; not a whole-ship benchmark.',
          'reader_requests':[s for s in sources if s['field']=='special_stowage' and s.get('value')], 'baseline':meta.get('baseline')}
    record=StageRecord(stage='intake',mode=Mode(meta.get('mode','recorded')),model=meta.get('model'),
                       input_sha256=sha256_json({'documents':documents,'base_instance':base,'state':state}),
                       output_sha256=sha256_json({'rows':rows,'questions':[q.model_dump(mode='json') for q in questions],'field_sources':sources}),
                       prompt_sha256=meta.get('prompt_sha256'),source_sha256=sha256_json({n:sha256_file(Path(__file__).with_name(n)) for n in files}),
                       wall_s=time.monotonic()-started,cost_usd=0 if records else float(meta.get('cost_usd',0)),notes=json.dumps(note,ensure_ascii=False))
    return IntakeResult(status='needs_answers' if questions else 'complete',rows=rows,questions=questions,field_sources=sources,records=[*records,record])


def rejected(error,documents,base):
    """A bounded live schema failure is a single batch exception, never facts."""
    meta=error.metadata
    question=Question(id='document:schema_rejected',row=None,field=None,
                      reason='schema_rejected',prompt='The model reading failed schema validation after the bounded attempt(s). No cargo values were accepted; resubmit for reading.')
    rows=[{'id':rid} for rid in sorted(base['load_list'])]
    record=StageRecord(stage='intake',mode=Mode(meta.get('mode','live')),model=meta.get('model'),
                       input_sha256=sha256_json({'documents':documents,'base_instance':base}),
                       output_sha256=sha256_json({'status':'rejected','rows':rows}),
                       prompt_sha256=meta.get('prompt_sha256'),wall_s=meta.get('wall_s',0),cost_usd=meta.get('cost_usd',0),
                       notes=json.dumps({'schema_rejection':meta,'findings':[{'code':'schema_rejected','severity':'blocking','message':str(error)}]},ensure_ascii=False))
    return IntakeResult(status='rejected',rows=rows,questions=[question],field_sources=[],records=[record])


def answer(result,answers,context):
    known={q.id:q for q in result.questions}
    if not isinstance(answers,dict) or set(answers)-known.keys():
        raise ValueError('Answers must name current questions')
    state=deepcopy(context['state'])
    for qid,value in answers.items():
        q=known[qid]
        rid=context['row_ids'][q.row] if q.row is not None else None
        action=value.get('action') if isinstance(value,dict) else None
        if action=='shut_out' or value=='shut_out':
            if rid is None:
                raise ValueError('Shut out requires a specific cargo row')
            state.setdefault('shut_out',[]).append(rid)
        elif q.reason=='unmapped_sheet_headers':
            if action!='reviewed_sheet' or rid is not None or not str(q.field).startswith('sheet:'):
                raise ValueError('Review the sheet explicitly with action=reviewed_sheet')
            document_id=q.field[len('sheet:'):]
            if not any(doc['id']==document_id and is_csv_document(doc) for doc in context['documents']):
                raise ValueError('Sheet review names no source document')
            state.setdefault('reviewed_sheets',[]).append(document_id)
        elif q.reason=='amendment_review':
            if action!='reviewed':
                raise ValueError('Review an amendment explicitly with action=reviewed')
            for doc in context['documents']:
                if doc.get('role')=='amendment' and (rid is None or rid in str(doc.get('text',''))):
                    state.setdefault('amendment_reviews',[]).append(doc['id'])
        elif q.reason=='duplicate_id':
            if value!='keep_one':
                raise ValueError('Duplicate needs keep_one or shut_out')
            state.setdefault('duplicate_resolutions',[]).append(rid)
        elif q.reason=='reader_referral':
            raise ValueError('Use route_special_stowage; cargo answers cannot authorize a rule')
        elif rid is None or q.field is None:
            raise ValueError('Supply a corrected document')
        else:
            actual=value.get('value') if isinstance(value,dict) and 'value' in value else value
            field=value.get('field',q.field) if isinstance(value,dict) else q.field
            if field!=q.field and not q.field.startswith(field+'.'):
                raise ValueError('Correction must name this field or a parent')
            state.setdefault('confirmations',{}).setdefault(rid,{})[field]=actual
            if q.field=='reefer_setpoint_c':
                state['confirmations'][rid]['temperature_unit']='C'
    return finish(context['raw'],context['documents'],context['base_instance'],state=state,records=result.records,reader_evidence=context.get('reader_evidence',[]))


def parser_baseline(documents,*,base_instance):
    from . import _documents,_base
    base=_base(base_instance)
    raw={'schema_version':'stowlm.intake-interpretation/7','header_maps':[],'observations':[],'exceptions':[],
         'metadata':{'mode':'generated','model':None,'baseline':BASELINE,'cost_usd':0,
                     'policy_version':8 if base['rule_overrides'].get('intake.policy',{}).get('document_authority') else 7}}
    return finish(raw,_documents(documents),base)


def route_special_stowage(result,*,parser):
    """Actually call the authenticated reader; return its opaque receipt to host.

    The host inspects and explicitly confirms this receipt via binding.confirm_brief.
    A refusal stays a question or can be shut out; intake never fabricates a rule.
    """
    from experiments.mvp.reader import public_brief
    from .measure import assemble_rows
    context=json.loads(result.records[-1].notes)['intake_context']
    if any(q.reason!='reader_referral' for q in result.questions):
        raise ValueError('Resolve cargo facts before routing reader instructions')
    requests=[s for s in result.field_sources if s['field']=='special_stowage' and s.get('value')]
    sentences=tuple(dict.fromkeys(str(s['value']) for s in requests))
    if not sentences:
        raise ValueError('No instruction to route')
    assembled=assemble_rows(context['base_instance'],result.rows)
    receipt=public_brief.read_brief(assembled,public_brief.VoyageBrief(standing='',voyage=sentences),parser=parser)
    return receipt


def accept_reader_confirmation(result,receipt):
    from experiments.mvp.pipeline.binding.confirmation import verify_confirmation
    from experiments.mvp.reader import public_brief
    from .measure import assemble_rows
    event=verify_confirmation(receipt)
    context=json.loads(result.records[-1].notes)['intake_context']
    evidence=public_brief._verify(assemble_rows(context['base_instance'],result.rows),receipt)
    requests=[s for s in result.field_sources if s['field']=='special_stowage' and s.get('value')]
    expected={str(s['value']) for s in requests}
    actual={s['sentence'] for s in evidence['sentences'] if s['role']=='voyage'}
    if actual!=expected or any(s['status']!='accepted' for s in evidence['sentences'] if s['role']=='voyage'):
        raise ValueError('Reader refused or did not read every instruction')
    state=deepcopy(context['state'])
    state['reader_resolutions']=sorted({s['row_id'] for s in requests})
    return finish(context['raw'],context['documents'],context['base_instance'],state=state,records=result.records,reader_evidence=[*context.get('reader_evidence',[]),{'reading':evidence,'confirmation':event}])
