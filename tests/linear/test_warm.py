"""Vessel-only warm-up excludes booking/background state and reports reuse."""
from types import SimpleNamespace as NS
import pytest
from stowage.model import FixedLoad
from stowage.model.fixtures import build_instance
from stowage.linear.warm import _empty_context,build_cache


def test_empty_context_retains_physics_and_removes_booking_background():
    original=build_instance().vessel.model_copy(update={'fixed_loads':[
        FixedLoad(x_m=10,y_m=0,z_m=4,mass_kg=1000)]})
    context=_empty_context(original)
    assert context.vessel.fixed_loads==[] and len(original.fixed_loads)==1
    assert context.vessel.cells==original.cells and context.vessel.hull==original.hull
    assert not context.containers and not context.load_list and not context.onboard.placements
    assert not context.yard.stacks and context.port.water_density_kg_m3==1025
    assert context.onboard.vessel_id==original.id


def test_empty_context_loads_only_missing_parent_hull(monkeypatch):
    parent=build_instance().vessel
    calls=[]
    monkeypatch.setattr('stowage.vessel.fleet.load_vessel',lambda name,**kw:
        calls.append((name,kw)) or parent)
    bare=parent.model_copy(update={'hull':None,'slice_of':'parent-ship'})
    context=_empty_context(bare)
    assert context.vessel.hull==parent.hull and context.vessel.cells==bare.cells
    assert calls==[('parent-ship',{'with_hull':True})]
    assert bare.hull is None


@pytest.mark.parametrize('value',[0,-1,1.5,True,None])
def test_invalid_worker_budget_fails_before_physics(value,monkeypatch):
    monkeypatch.setattr('stowage.linear.warm._empty_context',lambda *a:pytest.fail('unexpected geometry'))
    with pytest.raises(ValueError,match='positive integer'):build_cache('ship',workers=value)


def setup_warm(monkeypatch,*,qualified=True,reused=True):
    import stowage.linear.cache as cache
    instance=build_instance();family=NS(cache_key='coefficient')
    monkeypatch.setattr('stowage.vessel.departure_tank_masses',lambda vessel:{})
    monkeypatch.setattr('stowage.linear.warm._empty_context',lambda vessel:instance)
    monkeypatch.setattr('stowage.linear.physics._params_for',lambda context:('ship',{}))
    monkeypatch.setattr('stowage.linear.reusable.family_band',lambda *a:'band')
    monkeypatch.setattr('stowage.linear.reusable.coefficient_family',lambda *a:family)
    monkeypatch.setattr(cache,'coefficient_family_key',lambda *a:'coefficient')
    monkeypatch.setattr(cache,'family_calibration_key',lambda *a,**kw:'calibration',raising=False)
    monkeypatch.setattr(cache,'load_model',lambda *a:NS() if reused else None)
    monkeypatch.setattr(cache,'load_payload',lambda *a,**kw:{} if reused else None)
    monkeypatch.setattr('stowage.linear.family_calibration.reference_inputs',lambda *a:
        {'method':1,'direct':False,'qualified':qualified,'files':{}})
    monkeypatch.setattr('stowage.linear.family_calibration.domain_for',lambda *a:{'kg_min_m':0})
    calls=[]
    def calibration(*args,**kw):
        calls.append(kw);return None,{'samples':192,'margin_policy':{'status':'empirical_measured','sha256':'policy'}}
    monkeypatch.setattr('stowage.linear.family_calibration.calibrated_family',calibration)
    return calls


@pytest.mark.parametrize('reused',[True,False])
def test_public_hook_reports_production_family_and_reuse(monkeypatch,reused):
    calls=setup_warm(monkeypatch,reused=reused)
    result=build_cache('ship',workers=3)
    assert result['status']==('reused' if reused else 'built')
    assert result['coefficient_key']=='coefficient' and result['calibration_key']=='calibration'
    assert result['calibration_samples']==192 and result['workers_used']==1
    assert result['workers_requested']==3 and result['mandatory_exact_checker']
    assert result['margin_policy_sha256']=='policy' and calls==[{'samples':192}]


def test_public_warm_requires_production_transfer_qualification(monkeypatch):
    calls=setup_warm(monkeypatch,qualified=False)
    monkeypatch.setattr('stowage.cache_warm._warm_stability',lambda *a:[])
    with pytest.raises(ValueError,match='did not qualify'):build_cache('ship')
    assert calls==[]


def test_explicit_hook_builds_stability_dependency_before_calibration(monkeypatch):
    calls=setup_warm(monkeypatch)
    snapshots=iter([{'method':1,'direct':False,'qualified':False,'files':{}},
                    {'method':1,'direct':False,'qualified':True,'files':{}}])
    monkeypatch.setattr('stowage.linear.family_calibration.reference_inputs',lambda *a:next(snapshots))
    builds=[]
    monkeypatch.setattr('stowage.cache_warm._warm_stability',lambda *a:builds.append(a) or [{'cache':'liquid','status':'qualified'}])
    result=build_cache('ship')
    assert len(builds)==1 and calls==[{'samples':192}]
    assert result['dependency_builders']==[{'cache':'liquid','status':'qualified'}]


def test_hook_prepares_declared_consumable_fill_integrals(monkeypatch):
    setup_warm(monkeypatch)
    instance=build_instance()
    tank=NS(id='consumable',adjustable=False,content_density_kg_m3=800.)
    vessel=instance.vessel.model_copy(update={'tanks':[tank]})
    instance=instance.model_copy(update={'vessel':vessel})
    monkeypatch.setattr('stowage.linear.warm._empty_context',lambda vessel:instance)
    monkeypatch.setattr('stowage.vessel.departure_tank_masses',lambda vessel:{'consumable':1600.})
    monkeypatch.setattr('stowage.linear.reusable.coefficient_family',lambda *args:
        NS(cache_key='coefficient',template=NS(station_x_m=[0.,5.])))
    calls=[]
    monkeypatch.setattr('stowage.linear.reusable._fixed_tank_integrals',
        lambda *args:calls.append(args))
    result=build_cache('ship')
    assert calls==[({},'consumable',2.,[0.,5.])]
    assert result['fixed_tank_integrals_prepared']==['consumable']
