"""D40 builder policy must use declarations, not inferred bounds/table ends."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.model import HydrostaticRow
from stowage.params import load_param_file
from stowage.vessel import hull as module


@pytest.fixture
def params(monkeypatch):
    params=load_param_file('fleet','feeder_500')
    policy=params['operating_limits']
    policy.update(min_trim_m=-.4,max_trim_m=.8,bilge_keel_area_m2=0.)
    for name in ('min_trim_m','max_trim_m','bilge_keel_area_m2'):
        policy['provenance'][name]={'assumption':f'APPROXIMATION: explicit test {name}'}
    # Deliberately differs from design/scantling draught, calibrated required
    # freeboard and all table endpoints. No source of policy may replace it.
    load_line=params['load_line']
    load_line['inputs']['draught_m']=5.25
    load_line['available_freeboard_m']=load_line['inputs']['depth_m']+load_line['deck_stringer_thickness_m']-5.25
    load_line['approx_required_m']=.1

    class CheapGeometry:
        max_z_m=params['hull']['depth_m']

        def submerged(self,aft,fwd,heel=0.,density=None):
            return SimpleNamespace(displacement_kg=1e6*aft,x_m=np.array([10.,20.]),
                                   sectional_areas_m2=np.array([aft,aft]))

        def hydrostatic_row(self,draught,density,kg_m):
            return HydrostaticRow(draught_m=draught,displacement_kg=1e6*draught+123.,
                kb_m=draught/2,kmt_m=10.,lcb_m=50.,lcf_m=50.,
                tpc_kg_per_cm=10000.,mct_nm_per_cm=1e6)

        def kn_curve(self,angles,mass,**kwargs):
            return np.zeros(len(angles))

    monkeypatch.setattr(module.HullGeometry,'from_params',lambda *a,**kw:CheapGeometry())
    return params


def test_loading_manual_policy_comes_from_independent_declarations(params):
    hull=module.build_hull_model(params)
    assert (hull.min_trim_m,hull.max_trim_m)==(-.4,.8)
    assert hull.bilge_keel_area_m2==0.
    assert hull.min_displacement_kg==params['hull']['lightship_mass_kg']
    assert hull.max_displacement_kg==pytest.approx(5.25e6)
    assert hull.min_displacement_kg!=hull.hydrostatics[0].displacement_kg
    assert hull.max_displacement_kg!=hull.hydrostatics[-1].displacement_kg
    for field in ('min_trim_m','max_trim_m','bilge_keel_area_m2',
                  'min_displacement_kg','max_displacement_kg'):
        evidence=getattr(hull,field+'_provenance')
        assert evidence.source
        assert evidence.assumption
    assert 'load_line' in hull.max_displacement_kg_provenance.source
    assert params['hull_form']['offsets_sha256'] in hull.max_displacement_kg_provenance.source
    assert params['load_line']['assumption'] in hull.max_displacement_kg_provenance.assumption


@pytest.mark.parametrize('bounds',[(-2.,-.5),(.2,.8),(-.4,-.4)])
def test_signed_trim_endpoints_are_not_absolute_magnitudes(params,bounds):
    params['operating_limits'].update(zip(('min_trim_m','max_trim_m'),bounds))
    hull=module.build_hull_model(params)
    assert (hull.min_trim_m,hull.max_trim_m)==bounds


@pytest.mark.parametrize('field,value',[
    ('min_trim_m',None),('max_trim_m',float('nan')),
    ('min_trim_m',True),('bilge_keel_area_m2',False),
    ('bilge_keel_area_m2',-1.),('min_trim_m',2.),
])
def test_invalid_policy_values_fail_before_geometry(params,field,value,monkeypatch):
    params['operating_limits'][field]=value
    monkeypatch.setattr(module.HullGeometry,'from_params',
                        lambda *a,**kw:pytest.fail('invalid policy reached geometry'))
    with pytest.raises(ValueError,match=field):
        module.build_hull_model(params)


def test_declared_area_never_comes_from_legacy_boolean(params):
    params['operating_limits'].update(bilge_keel=False,bilge_keel_area_m2=2.75)
    assert module.build_hull_model(params).bilge_keel_area_m2==2.75


@pytest.mark.parametrize('field',['min_trim_m','max_trim_m','bilge_keel_area_m2'])
def test_missing_policy_value_is_not_inferred(params,field):
    params['operating_limits'].pop(field)
    params['hull'].pop(field,None)
    with pytest.raises(ValueError,match=field):
        module.build_hull_model(params)


@pytest.mark.parametrize('field',['min_trim_m','max_trim_m','bilge_keel_area_m2'])
def test_missing_policy_evidence_fails_closed(params,field):
    params['operating_limits']['provenance'].pop(field)
    with pytest.raises(ValueError,match=field):
        module.build_hull_model(params)


def test_hull_level_declared_policy_keeps_its_own_evidence(params):
    for field in ('min_trim_m','max_trim_m','bilge_keel_area_m2'):
        params['hull'][field]=params['operating_limits'].pop(field)
        params['hull']['provenance'][field]=params['operating_limits']['provenance'].pop(field)
    hull=module.build_hull_model(params)
    assert hull.min_trim_m==-.4
    assert 'hull.min_trim_m' in hull.min_trim_m_provenance.source


@pytest.mark.parametrize('change',[
    lambda p:p.pop('load_line'),
    lambda p:p['load_line'].pop('available_freeboard_m'),
    lambda p:p['load_line']['inputs'].update(draught_m=6.),
    lambda p:p['load_line'].update(deck_stringer_thickness_m=float('nan')),
])
def test_missing_or_inconsistent_load_line_fails_closed(params,change):
    change(params)
    with pytest.raises(ValueError,match='load_line'):
        module.build_hull_model(params)


def test_policy_evidence_preserves_structured_source_details(params):
    params['operating_limits']['provenance']['min_trim_m']['source']={
        'document':'Synthetic loading manual','section':'test signed trim'}
    hull=module.build_hull_model(params)
    evidence=hull.min_trim_m_provenance
    assert 'Synthetic loading manual' in evidence.source
    assert 'test signed trim' in evidence.source
    assert evidence.assumption=='APPROXIMATION: explicit test min_trim_m'
