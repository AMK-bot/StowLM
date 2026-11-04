"""Offline regression replay of SPENT recordings; never a fresh test score."""
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import sha256_json
from . import _documents, read_documents, answer_questions, parser_baseline
from . import campaign_v7 as c7, campaign_v8 as c8
from .evaluate_v2 import write
from .measure import assemble_rows
from .spark_reader import RecordedClient
from stowage.model.instance import Instance

HERE=Path(__file__).resolve().parent


def replay():
    cohort=HERE/'fixtures/authority_v8'
    old=json.loads((cohort/'metrics.json').read_text())
    cases=[]
    for item in old['cases']:
        case=cohort/item['case']
        docs=_documents(json.loads((case/'documents.json').read_text()))
        base=json.loads((case/'base_instance.json').read_text())
        expected=json.loads((case/'expected.json').read_text())
        raw=json.loads((case/'spark_raw.json').read_text()) if (case/'spark_raw.json').exists() else None
        captured=json.loads((case/'spark_response.json').read_text())
        results={}
        for name in (c8.SPARK,c8.BASELINE):
            started=time.monotonic()
            result=(read_documents(docs,base_instance=base,client=RecordedClient(raw)) if raw else c8.rejection_result(expected)) if name==c8.SPARK else parser_baseline(docs,base_instance=base)
            results[name]=c8.metrics(result,expected,name=name,documents=docs,
                                   raw=c8._raw_tally(captured) if name==c8.SPARK else None,
                                   wall_s=time.monotonic()-started)
        cases.append({'case':case.name,'methods':results})
    after=c7.aggregate(cases)
    demo=cohort/'demo'
    docs=_documents(json.loads((demo/'documents.json').read_text()))
    base=json.loads((demo/'base_instance.json').read_text())
    result=read_documents(docs,base_instance=base,client=RecordedClient(demo/'spark_raw.json'))
    # These are the demo's existing explicit synthetic host decisions, unchanged.
    result=answer_questions(result,json.loads((demo/'answers.json').read_text()))
    expected_hash=sha256_json(Instance.model_validate(base).model_dump(mode='json'))
    actual_hash=sha256_json(assemble_rows(base,result.rows).model_dump(mode='json')) if result.status=='complete' else None
    assert result.status=='complete',[(q.field,q.reason) for q in result.questions]
    assert actual_hash==expected_hash
    assert all(v['silent_wrong_values']==0 for v in after['methods'].values()),after['methods']
    value={'label':'development replay of spent v8 recordings; no new paid read or fresh accuracy claim',
        'old_frozen_methods':old['methods'],'development_methods':after['methods'],
        'development_cases':cases,'demo':{'status':result.status,'assembled_sha256':actual_hash,
        'base_sha256':expected_hash,'hash_equals_base':True},'new_api_spend_usd':0}
    write(HERE/'final_development_checks.json',value)
    # Keep demo filenames and shapes stable; the raw paid reading is unchanged.
    write(demo/'final.json',result.model_dump(mode='json'))
    return {k:v for k,v in value.items() if k!='development_cases'}


if __name__=='__main__':
    from .admission import wait_for_slot
    wait_for_slot()
    print(json.dumps(replay(),indent=2),flush=True)
