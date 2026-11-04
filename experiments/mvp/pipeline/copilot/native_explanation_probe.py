"""One additional functional tool probe; not another frozen recovery score."""
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_file
from .agency_evaluate import Model, RUNTIME
from .live import PURPOSE
from .real_recovery import make_native_tools, run_case

HERE = Path(__file__).resolve().parent
OUT = HERE/'fixtures/agency-v6-native-explain.json'


def run():
    if OUT.exists():
        return json.loads(OUT.read_text())
    case = dict(id='native_explanation_functional_probe', goal='functional_explanation_only',
        message='Why is the selected container stowed where it is? Explain the evidence and any limits on what you can establish.')
    manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(), case=case,
        files={name:sha256_file(HERE/name) for name in (*RUNTIME,'native_explanation_probe.py','real_recovery.py')},
        label='Additional native explanation functional probe after recovery campaign; not a fresh scenario or recovery retry.')
    with (HERE/'fixtures/native-explanation-started.json').open('x') as stream:
        json.dump(manifest,stream,indent=2)
    tools,fixture=make_native_tools(case,output_dir=HERE/'fixtures/native-explanation')
    from llm.spark.client import SparkClient
    client=SparkClient(max_retries=0,timeout=60)
    with client.ledger.locked():
        records=[row for row in client.ledger._records() if str(row.get('purpose','')).startswith(PURPOSE)]
        actual,held=client.ledger._totals(records)
    model=Model(client,PURPOSE+'/agency-v6-native-explain/'+case['id'],actual+held)
    result=run_case(case,model,factory=lambda ignored:(tools,fixture))
    explanation=any(call['name']=='explain_box' and call['result'].get('status')=='explanation'
        for turn in result['turns'] for call in turn['turn']['tool_calls'])
    value=dict(label=manifest['label'],freeze=manifest,complete=True,
        actual_explanation_produced=explanation,
        measured_cost_usd=str(sum((Decimal(call['cost_usd']) for call in model.calls),Decimal(0))),
        result=result)
    OUT.write_text(json.dumps(value,indent=2)+'\n')
    return value


if __name__=='__main__':
    value=run()
    print(json.dumps({k:v for k,v in value.items() if k not in ('result','freeze')},indent=2))
