from pathlib import Path
from types import SimpleNamespace
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from runtime import load_engine
load_engine()
import method
from stowage.io import load_instance
from stowage.actions import get_action_space
from stowage.checker import check
from stowage.model import PlannerResult
from tiers import analyze_report

ROOT=Path(__file__).resolve().parents[6]

def test_snapshot_only():
    import engine
    assert '/c5/c5-peel/engine_snapshot/' in engine.__file__
    import engine.frozen
    assert '/c5/c5-peel/planner.py' in engine.frozen.planner.__file__

def test_l0_return_is_unchanged(monkeypatch):
    # Verify identity at the shortcut without spending the native finish floor.
    instance=SimpleNamespace(load_list=[])
    plan=SimpleNamespace(model_dump=lambda **kw: {'sentinel':'unchanged'})
    result=SimpleNamespace(plan=plan)
    report=SimpleNamespace(passed=True,count=SimpleNamespace(weighted_nonproductive=7.))
    called=[]
    def original(*args,**kwargs):
        called.append(kwargs)
        return result, {'normal':True}
    monkeypatch.setattr(method,'check',lambda *args:report)
    monkeypatch.setattr(method.Candidates,'observe',lambda *args,**kwargs: {'plan':plan,'report':report,'analysis':{'level':0},'source':'ordinary-L0-unchanged'})
    monkeypatch.setattr(method,'_record',lambda *args,**kwargs: {'level':0})
    actual,record=method.solve(instance,normal_call=original)
    assert actual is result and actual.plan is plan
    assert len(called)==1 and record['level']==0

def test_capture_restores_after_error():
    from finishing import Candidates,retain_failures
    from engine.finishing import chain
    import pytest
    original=(chain._checked,chain.solve_ballast,chain.frozen_planner._finish)
    with pytest.raises(RuntimeError):
        with retain_failures(Candidates(None)):
            raise RuntimeError('sentinel')
    assert original==(chain._checked,chain.solve_ballast,chain.frozen_planner._finish)
