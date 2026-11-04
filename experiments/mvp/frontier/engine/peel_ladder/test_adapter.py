"""Engine routing, isolation and strict relaxed-record admission regressions."""
from contextlib import contextmanager
from types import SimpleNamespace as NS
from pathlib import Path
import importlib
import json
import sys
import pytest

from . import active, run
from .loader import isolated, verify_copies, NAMES
from engine import peel


@pytest.mark.parametrize('size,n,want', [('tiny',100,False),('small',100,False),
    ('medium',4,True),('large',4,True),('full',4,True),(None,60,False),(None,61,True)])
def test_routes_match_existing_assignment(size,n,want):
    assert active(NS(size_class=size,load_list=['x']*n)) is want


def test_eight_bodies_and_policy_match_frozen_manifest():
    hashes=verify_copies()
    assert all(name in hashes for name in ('assignment_method.py','assignment_proposal.py',
        'assignment_masks.py','elastic.py','assignment_runtime.py','assignment_records.py','finishing.py','tiers.py'))
    assert len(hashes)==24


def test_all_ambient_modules_functions_and_checker_restore_on_exception():
    import scipy.optimize
    from stowage.checker import rules_special
    from stowage.checker import check
    original={n:sys.modules.get(n) for n in NAMES}
    path=list(sys.path);lp=scipy.optimize.linprog;parameter=rules_special._parameter
    with pytest.raises(RuntimeError,match='sentinel'):
        with isolated() as method:
            ordinary=method.load_assignment()
            assert '/peel_ladder/assignment_vendor/' in ordinary.__file__
            elastic=importlib.import_module('elastic')
            assert '/peel_ladder/' in elastic.__file__
            assert rules_special._parameter is parameter
            raise RuntimeError('sentinel')
    assert all(sys.modules.get(n) is v for n,v in original.items())
    assert sys.path==path and scipy.optimize.linprog is lp
    assert rules_special._parameter is parameter
    from stowage.checker import check as after
    assert after is check


def test_api_rejects_held_full_pass_before_import_or_routing():
    with pytest.raises(ValueError,match='never peel'):
        run(object(),{'checked_pass':object()})
    with pytest.raises(ValueError,match='never peel'):
        peel.no_pass(object(),{'passed':True})


def test_inactive_metadata_never_imports_frozen_numerical_runtime(monkeypatch):
    import engine.peel_ladder.loader as loader
    monkeypatch.setattr(loader,'isolated',lambda:pytest.fail('inactive route imported LP'))
    row=run(NS(size_class='small',load_list=['x']*100),{})
    assert not row['applicable'] and not row['passed']


def test_default_hook_forwards_seed_budgets_and_custom_callable_stays_compatible(monkeypatch):
    classified=dict(completed=None,best_safe_partial=None,status='existing_witnesses_only')
    calls=[]
    monkeypatch.setattr(peel,'_classify',lambda *a,**k:calls.append(k) or dict(classified))
    import engine.peel_ladder as package
    monkeypatch.setattr(package,'run',lambda *a,**k:calls.append(k) or dict(
        applicable=True,relaxed=True,passed=False,completed={'level':4},best_safe_partial=None))
    instance=NS(size_class='medium',load_list=['x']*3)
    row=peel.no_pass(instance,{},seed=7,budgets={'proposal_s':20.})
    assert row['relaxed'] and not row['passed'] and calls[-1]['seed']==7
    calls.clear(); callback=lambda *a:[]
    peel.no_pass(instance,{},ladder=callback)
    assert calls==[dict(ladder=callback)]
    calls.clear(); peel.no_pass(instance,{},ladder=False)
    assert len(calls)==1


def test_copied_controller_keeps_l3_partial_then_completes_l4_without_pass(monkeypatch,synthetic):
    import engine.peel_ladder.loader as loader
    s=synthetic
    partial=s.make('partial',s.instance.load_list[:3])
    full=s.make('complete',s.instance.load_list,rules=['iacs.member_irs.container_securing'])
    visited=[]
    @contextmanager
    def configured():
        with isolated() as method:
            finish=importlib.import_module('finishing')
            monkeypatch.setattr(method,'check',lambda i,p:s.reports[p.id])
            monkeypatch.setattr(finish,'check',lambda i,p:s.reports[p.id])
            monkeypatch.setattr(method,'_reference_from_checked',lambda *a:None)
            monkeypatch.setattr(method,'finish_candidate',lambda *a,**k:None)
            def generate(instance,*,level,partial=False,**kwargs):
                visited.append((level,partial))
                return ([partial_plan] if level==3 and partial else [full] if level==4 else []),{}
            partial_plan=partial
            monkeypatch.setattr(method,'generate',generate)
            yield method
    monkeypatch.setattr(loader,'isolated',configured)
    row=run(s.instance,{'plans':[]},seed=4)
    assert visited==[(1,False),(2,False),(3,False),(3,True),(4,False)]
    assert row['relaxed'] and not row['passed'] and row['level']==4
    assert row['completed']['boxes_left_ashore']==[]
    assert row['best_safe_partial']['boxes_aboard_percent']==75.
    assert row['rules_broken'][0]['rule_id']=='iacs.member_irs.container_securing'
    assert not row['completed']['passed'] and not row['best_safe_partial']['passed']
    json.dumps(row)


@pytest.mark.parametrize('root_enabled',[False,True])
@pytest.mark.parametrize('raises',[False,True])
def test_private_e9_install_is_scoped_and_root_ablation_still_works(root_enabled,raises):
    from engine import e9fix as root_fix
    import stowage.feasibility._engine as native
    from stowage.checker import rules_special
    owner=next(cls for cls in vars(native).values()
               if isinstance(cls,type) and '_e9_batch' in vars(cls))
    entry_batch=owner._e9_batch;entry_parameter=rules_special._parameter
    state={name:getattr(root_fix,name) for name in
           ('_installed','_owner','_original_batch','_wrapper')}
    try:
        root_fix.uninstall()
        native_batch=owner._e9_batch
        if root_enabled:
            root_fix.install()
        expected=owner._e9_batch
        def exercise():
            with isolated() as method:
                method.load_assignment()
                private_fix=importlib.import_module('e9fix')
                assert private_fix is not root_fix
                private_fix.install()
                assert owner._e9_batch is not expected
                # Deliberate exceptional callback mutation: the outer context
                # must protect the checker lookup even before yielding again.
                rules_special._parameter=lambda *a,**k:None
                if raises:
                    raise RuntimeError('private installer unwind')
        if raises:
            with pytest.raises(RuntimeError,match='private installer unwind'):
                exercise()
        else:
            exercise()
        assert owner._e9_batch is expected
        assert rules_special._parameter is entry_parameter
        root_fix.uninstall()
        assert owner._e9_batch is native_batch
        # Reloaded private modules must not stack wrappers on later calls.
        for _ in range(2):
            with isolated() as method:
                method.load_assignment()
                importlib.import_module('e9fix').install()
                assert owner._e9_batch is not native_batch
            assert owner._e9_batch is native_batch
            assert rules_special._parameter is entry_parameter
    finally:
        owner._e9_batch=entry_batch
        rules_special._parameter=entry_parameter
        for name,value in state.items():
            setattr(root_fix,name,value)


def test_e9_and_parameter_restore_if_private_import_fails(monkeypatch):
    import engine.peel_ladder.loader as loader
    import stowage.feasibility._engine as native
    from stowage.checker import rules_special
    import scipy.optimize
    owner=next(cls for cls in vars(native).values()
               if isinstance(cls,type) and '_e9_batch' in vars(cls))
    batch,parameter=owner._e9_batch,rules_special._parameter
    modules={name:sys.modules.get(name) for name in NAMES}
    path=list(sys.path);lp=scipy.optimize.linprog
    original=loader.importlib.import_module
    def broken(name,*args,**kwargs):
        if name=='assignment_method':
            owner._e9_batch=lambda *a,**k:None
            rules_special._parameter=lambda *a,**k:None
            raise ImportError('private dependency import failed')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(loader.importlib,'import_module',broken)
    with pytest.raises(ImportError,match='private dependency import failed'):
        with isolated():
            pytest.fail('failed import yielded')
    assert owner._e9_batch is batch and rules_special._parameter is parameter
    assert sys.path==path and scipy.optimize.linprog is lp
    assert all(sys.modules.get(name) is module for name,module in modules.items())
