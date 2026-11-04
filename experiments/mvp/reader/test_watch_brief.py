from decimal import Decimal
import json
from pathlib import Path
from experiments.mvp.reader import watch_brief as watch


def fixture(tmp_path, monkeypatch, *, returned=False, compliant=False, cost='0.001'):
    monkeypatch.setattr(watch,'RUN',tmp_path)
    monkeypatch.setattr(watch,'verify_sources',lambda:None)
    monkeypatch.setattr(watch,'evidence_errors',lambda value:[])
    monkeypatch.setattr(watch,'build',lambda:{'audits':30})
    cases=[{'case_id':f'brief-{i:02}'} for i in range(1,31)]
    monkeypatch.setattr(watch,'cases',lambda:cases)
    for folder in ('parser','plans','audits'):
        (tmp_path/folder).mkdir()
    for case in cases:
        name=case['case_id']+'.json'
        parsed={'brief':{'sentences':[{'parser':{'cost_usd':cost}}]}}
        planned={'admission':{}, **({'plan':{}} if returned else {})}
        audited={'audit_completed':True,'baseline_verified':True,'admission':{},'passed':compliant,'compliant_pass':compliant}
        for folder,value in (('parser',parsed),('plans',planned),('audits',audited)):
            (tmp_path/folder/name).write_text(json.dumps(value))


def test_proven_failures_do_not_require_a_plan_to_complete(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch)
    result=watch.verify_completion()
    assert result['status']=='complete' and result['returned_plans']==0
    assert Decimal(result['cost_usd'])==Decimal('0.030')


def test_returned_plan_requires_fresh_compliant_gate(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch,returned=True,compliant=False)
    result=watch.verify_completion()
    assert result['status']=='needs_review'
    assert len(result['errors'])==30


def test_budget_violation_cannot_complete(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch,cost='0.1')
    result=watch.verify_completion()
    assert result['status']=='needs_review'
    assert 'Cohort count or Spark budget violation' in result['errors']
