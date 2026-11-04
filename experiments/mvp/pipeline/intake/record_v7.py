"""One immutable v7 Spark recording per labelled synthetic case."""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
import json
from pathlib import Path

from . import _documents
from .interpretation import InterpretationClient,read
from .spark_reader import RecordedClient


def record_case(case):
    case=Path(case).resolve()
    if (case/'spark_raw.json').exists() or (case/'spark_response.json').exists():
        raise ValueError('Recording exists; no retry or overwrite')
    if case.name.startswith('blind_'):
        from .campaign_v7 import verify_frozen
        verify_frozen()
    documents=_documents(json.loads((case/'documents.json').read_text()))
    class Capture(InterpretationClient):
        def complete_documents(self,*args,**kwargs):
            response=super().complete_documents(*args,**kwargs)
            (case/'spark_response.json').write_text(json.dumps({'text':response.text,'usage':asdict(response.usage),
                 'cost_usd':float(response.cost_usd),'request_id':response.request_id,
                 'recorded_utc':datetime.now(timezone.utc).isoformat()},indent=2,ensure_ascii=False)+'\n')
            return response
    raw=read(documents,client=Capture(),retry_schema=False)
    (case/'spark_raw.json').write_text(json.dumps(raw,indent=2,ensure_ascii=False)+'\n')
    return {k:v for k,v in raw['metadata'].items() if k!='raw_text'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case',type=Path)
    args=parser.parse_args()
    from .admission import wait_for_slot
    wait_for_slot()
    print(json.dumps(record_case(args.case)),flush=True)
