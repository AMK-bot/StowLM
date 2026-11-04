"""True-reference KG feedback and total cargo/background/tank accounting."""
from types import SimpleNamespace as NS
import numpy as np
import pytest
from joint_loading import JointLoading,kg_caps_below_probes,backoff_kg_caps


def test_native_kg_backoff_tightens_once_per_selected_reference():
    probes=[dict(phase='departure',kg_m=7.2),dict(phase='arrival',kg_m=7.4)]
    caps=kg_caps_below_probes(probes,.15)
    assert caps==pytest.approx({'departure':7.05,'arrival':7.25})
    # Repeated bounded construction failures at the same reference do not
    # subtract another .15m. A new lower-KG selected area failure can tighten.
    assert kg_caps_below_probes(probes,.15,caps)==caps
    lower=[dict(phase='departure',kg_m=7.0),dict(phase='arrival',kg_m=7.3)]
    assert kg_caps_below_probes(lower,.15,caps)==pytest.approx({'departure':6.85,'arrival':7.15})
    assert kg_caps_below_probes(probes,0.)=={}
    assert kg_caps_below_probes(probes,0.,caps)==caps
    # A new true reference replaces stale caps rather than perpetuating an
    # unrelated stricter local restriction forever.
    assert kg_caps_below_probes(probes,.15,{'departure':6.,'arrival':6.})==caps


def test_failed_joint_caps_back_off_to_baseline_then_disappear():
    probes=[dict(phase='departure',kg_m=7.2),dict(phase='arrival',kg_m=7.4)]
    caps=kg_caps_below_probes(probes,.15)
    relaxed=backoff_kg_caps(caps,probes,.075)
    assert relaxed==pytest.approx({'departure':7.125,'arrival':7.325})
    assert backoff_kg_caps(relaxed,probes,.075)=={}
    assert backoff_kg_caps({},probes,.075)=={}
    assert backoff_kg_caps(caps,probes,0.)==caps
    assert backoff_kg_caps({'departure':7.3},probes,.075)=={}


@pytest.mark.parametrize('step',[-.1,float('inf'),float('nan')])
def test_invalid_failed_joint_backoff_fails_closed(step):
    with pytest.raises(ValueError):backoff_kg_caps({'departure':7.},[dict(phase='departure',kg_m=7.2)],step)


@pytest.mark.parametrize('step',[-.1,float('inf'),float('nan')])
def test_invalid_kg_backoff_fails_closed(step):
    with pytest.raises(ValueError):kg_caps_below_probes([],step)


def test_caps_can_only_change_before_assembly_and_known_phases():
    joint=JointLoading.__new__(JointLoading)
    joint.last_spec=None;joint.contexts=[('departure',object()),('arrival',object())];joint.metadata={}
    assert joint.set_kg_caps({'arrival':7.})=={'arrival':7.}
    assert joint.metadata['total_kg_search_caps_m']=={'arrival':7.}
    for bad in ({'unknown':7.},{'arrival':-1.},{'arrival':float('nan')}):
        with pytest.raises(ValueError):joint.set_kg_caps(bad)
    joint.last_spec={}
    with pytest.raises(RuntimeError):joint.set_kg_caps({'arrival':6.8})


def fixture(monkeypatch):
    import ballast_loading as bl
    from stowage.checker import stability
    monkeypatch.setattr(stability,'_loading_policy',lambda inst:(-1.,1.,0.,2e6))
    monkeypatch.setattr(stability,'_minimum_loading_gm',lambda inst:.15)
    monkeypatch.setattr(bl,'_tank_reference',lambda ctx:(np.array([[45.,0.,2.]]),30000.))
    class Geometry:
        def full_submerged(self,ta,tf,heel,rho):
            return NS(displacement_kg=rho*100.*(ta+tf),lcb_m=50.+ta-tf,vcb_m=2.,bm_t_m=6.)
    hull=NS(design_draught_m=5.,max_draught_m=10.,min_draught_fwd_m=1.,max_list_rad=.1)
    inst=NS(vessel=NS(hull=hull),port=NS(max_draught_m=10.),rule_overrides={})
    # Original reference base = fixed background (1Mt at KG5) + kept (20t
    # at KG3) + new cargo (30t at KG9). All modeled cargo is removed once.
    ctx=NS(instance=inst,tanks=[object()],direct_geometry=Geometry(),rho=1025.,capacity=np.array([100000.]),
           base=NS(mass_kg=1050000.),base_moments=np.array([52500000.,0.,5330000.]))
    cargo=dict(cargo_mass=np.array([20000.,30000.]),
               cargo_moments=np.array([[1000000.,0.,60000.],[1500000.,0.,270000.]]),
               removed_cargo=[[50000.,2500000.,0.,330000.]])
    return bl,ctx,cargo


def test_total_kg_cap_counts_fixed_kept_new_and_water_once(monkeypatch):
    bl,ctx,cargo=fixture(monkeypatch)
    total_mass=1000000.+20000.+30000.+50000.
    total_z=5000000.+60000.+270000.+100000.
    cap=total_z/total_mass
    A,b,names,meta=bl._assemble([('departure',ctx)],kg_caps_m={'departure':cap},**cargo)
    k=names.index('departure/kg_upper')
    x=np.array([1.,1.,.5,5.,5.]) # kept, new, water fraction, aft, forward.
    assert abs(A[k]@x-b[k])<1e-12
    assert meta[0]['kg_search_cap_m']==cap
    # Extra low water decreases total KG, with its mass denominator included.
    x[2]=1.
    assert A[k]@x-b[k]<0.
    # Moving the new box 3m up raises vertical moment by 90,000kg*m.
    high=dict(cargo);high['cargo_moments']=cargo['cargo_moments'].copy();high['cargo_moments'][1,2]+=90000.
    Ah,bh,nh,_=bl._assemble([('departure',ctx)],kg_caps_m={'departure':cap},**high)
    x[2]=.5
    assert Ah[nh.index('departure/kg_upper')]@x-bh[nh.index('departure/kg_upper')]>0.


def test_kg_caps_default_keeps_all_old_rows_identical(monkeypatch):
    bl,ctx,cargo=fixture(monkeypatch)
    A,b,names,meta=bl._assemble([('departure',ctx)],**cargo)
    capped,cb,cn,cm=bl._assemble([('departure',ctx)],kg_caps_m={'departure':7.},**cargo)
    assert 'departure/kg_upper' not in names
    for j,name in enumerate(names):
        k=cn.index(name)
        np.testing.assert_array_equal(capped[k],A[j]);assert cb[k]==b[j]
    assert meta[0]['kg_search_cap_m'] is None
    assert cm[0]['kg_search_cap_m']==7.
    with pytest.raises(ValueError):bl._assemble([('departure',ctx)],kg_caps_m={'arrival':7.},**cargo)
