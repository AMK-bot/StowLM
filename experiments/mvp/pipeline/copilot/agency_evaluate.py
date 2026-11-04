"""Post-freeze Spark behavior on actual ToolSet with declared fixture services."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .agent import Copilot
from .authorization import ActionGrant, TurnRequest
from .agency_fixtures import make_tools
from .behavior_scores import score_trace, score_behavior
from .live import CAP, PURPOSE
from .spending import assert_paid_allowed

HERE = Path(__file__).resolve().parent
F = HERE / 'fixtures'
REVISION = 'agency-v6'
RUNTIME = ('agent.py','authorization.py','safety.py','tools.py','jobs.py','agency_fixtures.py',
           'behavior_scores.py','agency_evaluate.py')


def freeze():
    value = dict(created_utc=datetime.now(timezone.utc).isoformat(),
                 files={name:sha256_file(HERE/name) for name in RUNTIME},
                 frozen_first_sha256=sha256_file(F/'fresh-v4-behavior-scenarios.json'))
    target = F/f'{REVISION}-freeze.json'
    if target.exists():
        old=json.loads(target.read_text())
        if old['files'] != value['files'] or old['frozen_first_sha256'] != value['frozen_first_sha256']:
            raise RuntimeError('Agent changed after freeze; create an explicitly labelled new revision')
        return old
    target.write_text(json.dumps(value,indent=2)+'\n')
    return value


class Model:
    mode='live'
    def __init__(self, client, purpose, prior):
        self.client,self.purpose,self.prior=client,purpose,prior
        self.calls=[]
    def complete(self,prompt):
        assert_paid_allowed(self.purpose)
        from llm.spark.planner_budget import allocation_budget
        from llm.spark.client import SparkIncompleteError
        try:
            with allocation_budget(self.client.ledger,allocation_usd=CAP,prior_job_usd=self.prior):
                response=self.client.complete(prompt,purpose=self.purpose,max_output_tokens=3072,reasoning_effort='low')
        except SparkIncompleteError as error:
            self.calls.append(dict(status='incomplete',request_id=error.request_id,cost_usd=str(error.cost_usd),
                                   usage=vars(error.usage)))
            raise
        self.calls.append(dict(status='completed',request_id=response.request_id,cost_usd=str(response.cost_usd),usage=vars(response.usage)))
        return response.text


def request_for(case,tools,reference,step):
    if 'host_request' in case:
        raw=case['host_request']
        return TurnRequest(selected_sentence=raw.get('selected_sentence'),
                           actions=tuple(ActionGrant(**a) for a in raw.get('actions',[])))
    if case.get('kind')=='injection' or 'injection' in case.get('tags',[]):
        return TurnRequest()
    context=tools.context()
    refs=dict(reference,rules=context.get('confirmed_rules',[]))
    reading=context.get('confirmed_reading_id') or context.get('pending_reading_id')
    if reading: refs['reading_id']=reading
    if context.get('active_job_id'): refs['job_id']=context['active_job_id']
    # An operational question is not an instruction selection. The authority
    # parser may still recognise a planner-owned instruction in the message.
    selected=case.get('reader_sentence')
    return TurnRequest(selected_sentence=selected,allowed_tools=frozenset(tools.names),references=refs)


def run_case(case,model):
    tools,fixture=make_tools(case)
    copilot=Copilot(tools,model,max_steps=10)
    turns=[]
    for step in case.get('turns',[dict(message=case['message'])] if 'message' in case else []):
        host_event=None
        if step.get('confirm_reading'):
            current=tools.context().get('pending_reading_id')
            host_event=tools.confirm_reading(current) if current else {'status':'not_replayable'}
        before=len(model.calls)
        request=request_for(case,tools,fixture['references'],step)
        turn=copilot.turn(step['message'],request=request)
        trace=deepcopy(copilot.last_trace)
        scored=score_trace(trace,turn=turn,model_calls=model.calls[before:])
        turns.append(dict(message=step['message'],turn=turn.model_dump(mode='json'),trace=trace,
            behavior=scored,host_confirmation_event=host_event,host_request=dict(
                selected_sentence=request.selected_sentence,allowed_tools=sorted(request.allowed_tools),references=request.references,
                actions=[dict(name=a.name,arguments=a.arguments) for a in request.actions])))
    actual=[call['name'] for row in turns for call in row['turn']['tool_calls']]
    expected=case.get('expected_tool_sequence',case.get('expected_tools'))
    return dict(id=case['id'],setup=case.get('setup'),kind=case.get('kind'),
                route=dict(actual=actual,expected=expected,passed=(actual==expected) if expected is not None else None,
                           label='Exact legacy requested sequence; extra useful tools count as mismatch, not unsafe behavior.'),
                tools_label=fixture['label'],service_calls=fixture['services'],turns=turns,model_calls=model.calls,
                behavior=score_behavior([row['behavior'] for row in turns]))


def run(cohort):
    from llm.spark.client import SparkClient
    dest=F/f'{REVISION}-{cohort}.json'
    if dest.exists():return json.loads(dest.read_text())
    frozen=freeze()
    path=F/('scenarios.json' if cohort=='tuned' else 'fresh-v4-behavior-scenarios.json')
    if cohort=='frozen-first':
        manifest=json.loads((F/'fresh-v4-behavior-freeze.json').read_text())
        expected=manifest.get('scenarios_sha256',manifest.get('sha256'))
        if sha256_file(path)!=expected:raise RuntimeError('Frozen-first scenarios changed')
    marker=F/f'{REVISION}-{cohort}-started.json'
    with marker.open('x') as out:json.dump(dict(started_utc=datetime.now(timezone.utc).isoformat(),freeze=frozen),out,indent=2)
    cases=json.loads(path.read_text())['scenarios']
    client=SparkClient(max_retries=0,timeout=60)
    with client.ledger.locked():
        previous=[row for row in client.ledger._records() if str(row.get('purpose','')).startswith(PURPOSE)]
        actual,held=client.ledger._totals(previous)
    rows=[]
    for case in cases:
        model=Model(client,PURPOSE+'/'+REVISION+'-'+cohort+'/'+case['id'],actual+held)
        result=run_case(case,model)
        rows.append(result)
        value=dict(schema_version='stowlm.copilot-agency-evaluation/1',cohort=cohort,
            label=('Tuned development42 adapted to actual ToolSet with fixture services' if cohort=='tuned' else
                   'Frozen-first15, authored after earlier reviews; actual ToolSet with fixture services, not engine/reader accuracy'),
            freeze=frozen,scenarios_sha256=sha256_file(path),scenario_count=len(rows),complete=len(rows)==len(cases),
            behavior=score_behavior([turn['behavior'] for row in rows for turn in row['turns']]),
            legacy_route=dict(passed=sum(row['route']['passed'] is True for row in rows),
                              total=sum(row['route']['passed'] is not None for row in rows)),
            measured_cost_usd=str(sum((Decimal(call['cost_usd']) for row in rows for call in row['model_calls']),Decimal(0))),
            results=rows)
        dest.write_text(json.dumps(value,indent=2)+'\n')
        print(json.dumps(dict(cohort=cohort,id=case['id'],lines=result['behavior']['report_lines'],
            tool_count=sum(len(t['turn']['tool_calls']) for t in result['turns']))),flush=True)
    return value


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cohort',choices=('tuned','frozen-first'))
    args=parser.parse_args()
    value=run(args.cohort)
    print(json.dumps({key:val for key,val in value.items() if key!='results'},indent=2))
