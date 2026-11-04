"""Independent exact-endpoint and observation-gate tests."""
import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('c4_audit_tests',Path(__file__).with_name('audit.py'))
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_exact_interval_includes_endpoints():
    assert audit.contains(['0.1','0.3'],'0.1')
    assert audit.contains(['0.1','0.3'],'0.3')
    assert not audit.contains(['0.1','0.3'],'0.30000000000000001')


def test_interval_rejects_missing_or_reversed_endpoints():
    with pytest.raises(ValueError):
        audit.exact_interval([1])
    with pytest.raises(ValueError):
        audit.exact_interval([2,1])


def test_observation_gate_has_no_implicit_tolerance():
    assert audit.audit_area_enclosure(.31,.30)['encloses']
    assert not audit.audit_area_enclosure(.30,.300000000000001)['encloses']
    assert audit.audit_area_enclosure(.30,.300000000000001,allowance=1e-10)['encloses']
    assert not audit.audit_area_enclosure(.31,.30)['physical_proof']


def test_observation_gate_rejects_nonfinite_and_negative_allowance():
    for values in ((float('nan'),1,0),(1,float('inf'),0),(1,1,-1)):
        with pytest.raises(ValueError):
            audit.audit_area_enclosure(values[0],values[1],allowance=values[2])


def test_unknown_input_is_rejected_before_open():
    with pytest.raises(ValueError,match='outside explicit approved'):
        audit.load_raw('not-an-approved-input.json')


def test_registry_preserves_all_distinct_passing_plans_and_authority():
    controls=audit.positive_controls()
    assert len(controls)==19
    assert len({r['instance_id'] for r in controls})==12
    assert len({r['plan_canonical_sha256'] for r in controls})==19
    assert sum(r['instance_id']=='random.medium.185100000.0.2' for r in controls)==1
    assert all(r['evidence_aliases'] and r['input'] in audit.approved_inputs() for r in controls)


def test_control_containment_checks_deck_shares_tanks_and_area():
    obs=dict(instance_id='fixture',control_id='fixture-plan',
        phases=dict(departure=dict(mass_kg=100,kg_physical_m=5,kg_virtual_m=5.2,
            draft_mean_m=4,trim_m=.1,measured_positive_area_mrad=.3,
            tank_masses_kg={'t':10})),
        cargo=dict(deck_mass_kg=20,deck_boxes_by_odd_bay={'1':.5},
                   deck_mass_by_odd_bay_kg={'1':10}))
    win=dict(instance_id='fixture',phase_windows=dict(departure=dict(mass_kg=[90,110],
        physical_kg_m=[4,6],area_upper_mrad=.31)),
        cargo=dict(deck_mass_kg=[10,30],by_bay={'1':dict(count=[0,1],mass_kg=[0,20])}),
        tanks=dict(shared_departure_mass_kg={'t':[0,20]}))
    assert audit.audit_window_containment(win,obs)['contained']
    win['phase_windows']['departure']['area_upper_mrad']=.29
    win['cargo']['by_bay']['1']['count']=[0,.4]
    win['tanks']['shared_departure_mass_kg']['t']=[0,9]
    result=audit.audit_window_containment(win,obs)
    assert not result['contained']
    assert len(result['violations'])==3


def test_independent_support_certificate_handles_nonzero_dual_residual():
    matrix=dict(lower=[0.],upper=[5.],eq_rows=[],
        ub_rows=[dict(rhs=-2.,coefficients=[[0,-1.]])])
    cert=dict(objective=[[0,1.]],ub_multipliers=[-1.],eq_multipliers=[])
    assert audit.audit_support_certificate(matrix,cert)['lower_bound']==2.
    cert['ub_multipliers']=[-.9]
    result=audit.audit_support_certificate(matrix,cert)
    assert 1.79<result['lower_bound']<=1.8
    assert not result['physical_proof']


def test_support_certificate_rejects_malformed_or_wrong_sign_data():
    matrix=dict(lower=[0.],upper=[5.],ub_rows=[],eq_rows=[])
    cert=dict(objective=[[0,1.]],ub_multipliers=[],eq_multipliers=[])
    matrix['upper']=[]
    with pytest.raises(ValueError,match='dimensions'):
        audit.audit_support_certificate(matrix,cert)
    matrix['upper']=[5.];cert['objective']=[[0,1.],[0,2.]]
    with pytest.raises(ValueError,match='duplicate'):
        audit.audit_support_certificate(matrix,cert)
