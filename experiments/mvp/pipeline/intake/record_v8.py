"""One paid attempt per immutable v8 case, including rejected readings."""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import sha256_json
from . import _documents
from .interpretation import InterpretationClient,SchemaRejection,read


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _write(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)
        stream.write('\n')


def record_case(case):
    """Save the request before transport and every response before validation."""
    case=Path(case).resolve()
    names=('spark_request.json','spark_response.json','spark_raw.json','spark_rejection.json')
    if any((case/name).exists() for name in names):
        raise ValueError('Recording exists; no retry or overwrite')
    if case.name.startswith('blind_'):
        # A v7 freeze cannot authorize a v8 blind read.
        from .campaign_v8 import verify_frozen
        verify_frozen()
    documents=_documents(json.loads((case/'documents.json').read_text()))
    class Capture(InterpretationClient):
        def complete_documents(self,wire,**kwargs):
            if (case/'spark_request.json').exists():
                raise ValueError('Recording permits one paid attempt only')
            started,started_utc=time.monotonic(),_utc()
            request={'schema_version':'stowlm.intake-request/8','policy_version':8,
                     'started_utc':started_utc,'recorded_utc':started_utc,
                     'wire':wire,'documents_sha256':sha256_json(wire),
                     'prompt_sha256':hashlib.sha256(wire[0]['text'].encode()).hexdigest(),
                     'max_output_tokens':kwargs.get('max_output_tokens'),
                     'reasoning_effort':kwargs.get('reasoning_effort'),'attempt_limit':1}
            _write(case/'spark_request.json',request)
            try:
                response=super().complete_documents(wire,**kwargs)
            except Exception as error:
                _write(case/'spark_response.json',{'status':'transport_or_client_error',
                    'error_type':type(error).__name__,'error':str(error),'policy_version':8,
                    'request_id':getattr(error,'request_id',None),'started_utc':started_utc,
                    'usage':asdict(error.usage) if getattr(error,'usage',None) is not None else None,
                    'cost_usd':float(error.cost_usd) if getattr(error,'cost_usd',None) is not None else None,
                    'finished_utc':_utc(),'wall_s':time.monotonic()-started,
                    'prompt_sha256':request['prompt_sha256'],'documents_sha256':request['documents_sha256']})
                raise
            _write(case/'spark_response.json',{'text':response.text,'usage':asdict(response.usage),
                'cost_usd':float(response.cost_usd),'request_id':response.request_id,
                'recorded_utc':_utc(),'started_utc':started_utc,'finished_utc':_utc(),
                'wall_s':time.monotonic()-started,'policy_version':8,
                'prompt_sha256':request['prompt_sha256'],'documents_sha256':request['documents_sha256']})
            return response
    try:
        raw=read(documents,client=Capture(),retry_schema=False)
    except SchemaRejection as error:
        _write(case/'spark_rejection.json',{'status':'schema_rejected','error':str(error),
               'attempts':error.attempts,'metadata':error.metadata,'recorded_utc':_utc()})
        raise
    _write(case/'spark_raw.json',raw)
    return {key:value for key,value in raw['metadata'].items() if key not in ('raw_text','attempts')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case',type=Path)
    args=parser.parse_args()
    from .admission import wait_for_slot
    wait_for_slot()
    print(json.dumps(record_case(args.case)),flush=True)
