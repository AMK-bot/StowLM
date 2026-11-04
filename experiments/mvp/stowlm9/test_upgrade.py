import importlib.util
from pathlib import Path
from types import SimpleNamespace


def module():
    path=Path(__file__).with_name('run.py')
    spec=importlib.util.spec_from_file_location('stowlm9_next_policy',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def test_rule_budget900_and_scoped_v4(monkeypatch):
    run=module();seen=[]
    import os
    import experiments.mvp.frontier.blocked as blocked
    import stowage.planner as planner
    result=SimpleNamespace(plan=object(),status='ok',message=None)
    monkeypatch.setattr(blocked,'blocked_plan',lambda *a,**kw: (seen.append(('plan',kw['time_limit_s'])) or result))
    def finalize(*a,**kw):
        seen.append(('finish',kw['time_limit_s'],os.environ['STOWAGE_BALLAST_V4']))
        return result
    monkeypatch.setattr(planner,'finalize',finalize)
    monkeypatch.delenv('STOWAGE_BALLAST_V4',raising=False)
    assert run.finish_rule(object())[0] is result.plan
    assert seen==[('plan',300),('finish',900,'1')]
    assert 'STOWAGE_BALLAST_V4' not in os.environ


def test_retry_classifies_timeout_and_preserves_structural_failure():
    run=module()
    assert run.timeout_only({'planning':{'status':'error','message':'Ballast not found (timeout:worker)'}})
    assert not run.timeout_only({'planning':{'status':'dead_end','message':'No box fits an open slot'}})
    assert run.timeout_only({'status':'error','error':'interrupted worker'})
