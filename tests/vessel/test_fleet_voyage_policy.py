"""D50 producer declares actual consumables and a labelled synthetic order."""
from copy import deepcopy

import pytest

from stowage.model import Provenance, Tank, TankSoundingPoint, Vessel, VoyageTankPolicy

SOURCE='params/fleet/test.toml sha256:'+'a'*64


def _tank(tid,kind,x,*,adjustable=False):
    return Tank(id=tid,kind=kind,content_density_kg_m3=900.,capacity_m3=10.,
        x_aft_m=x-1.,x_fore_m=x+1.,adjustable=adjustable,
        table=[TankSoundingPoint(volume_m3=0.,lcg_m=x,tcg_m=0.,vcg_m=1.,fsm_m4=0.),
               TankSoundingPoint(volume_m3=10.,lcg_m=x,tcg_m=0.,vcg_m=1.,fsm_m4=0.)])


def _build(tanks):
    from stowage.vessel.fleet_voyage import build_voyage_policy
    return build_voyage_policy(tanks,parameter_source=SOURCE,vessel_id='synthetic-test')


def _bind(tanks,policy):
    return Vessel(id='synthetic-test',name='Synthetic test',nominal_teu=0,
                  cells=[],stacks=[],hatch_covers=[],tanks=tanks,voyage_policy=policy)


def test_declares_all_five_actual_consumable_kinds_without_guessing_other_tanks():
    kinds=['fuel_oil','diesel_oil','lube_oil','fresh_water','stores']
    tanks=[_tank(kind,kind,10.+index) for index,kind in enumerate(kinds)]
    tanks += [_tank('BALLAST','ballast',100.,adjustable=False),
              _tank('OTHER','other',100.),_tank('SLUDGE','sludge',100.)]
    original=deepcopy([tank.model_dump() for tank in tanks])
    policy=_build(tanks)
    assert isinstance(policy,VoyageTankPolicy)
    assert {group.kind:group.tank_ids for group in policy.groups}=={kind:[kind] for kind in kinds}
    assert set(policy.drawdown_order)==set(kinds)
    assert len(policy.drawdown_order)==len(kinds)
    assert [tank.model_dump() for tank in tanks]==original
    assert _bind(tanks,policy).voyage_policy==policy
    assert VoyageTankPolicy.model_validate_json(policy.model_dump_json())==policy


def test_global_order_is_forward_full_sounding_lcg_then_id_and_can_interleave_kinds():
    tanks=[_tank('FO-AFT','fuel_oil',10.),_tank('FW-FWD','fresh_water',50.),
           _tank('FO-FWD-B','fuel_oil',40.),_tank('FO-FWD-A','fuel_oil',40.),
           _tank('DO-MID','diesel_oil',30.)]
    policy=_build(tanks)
    assert policy.drawdown_order==['FW-FWD','FO-FWD-A','FO-FWD-B','DO-MID','FO-AFT']
    assert _build(list(reversed(tanks)))==policy
    group=next(group for group in policy.groups if group.kind=='fuel_oil')
    assert group.tank_ids==['FO-AFT','FO-FWD-A','FO-FWD-B']
    order=policy.drawdown_order_provenance
    assert isinstance(order,Provenance) and order.created_by
    assert any(parent.startswith('ASSUMPTION:') and 'full-sounding LCG' in parent for parent in order.parents)
    assert any('no service-tank roles' in parent for parent in order.parents)


def test_group_and_policy_evidence_pins_actual_tanks_kinds_and_source():
    tanks=[_tank('FO-P','fuel_oil',20.),_tank('FO-S','fuel_oil',20.),_tank('FW','fresh_water',10.)]
    policy=_build(tanks)
    for group in policy.groups:
        assert isinstance(group.provenance,Provenance) and group.provenance.created_by
        assert any('D27' in parent for parent in group.provenance.parents)
        assert any(group.kind in parent for parent in group.provenance.parents)
        for tid in group.tank_ids:
            assert f'{SOURCE}#tanks[{tid}]' in group.provenance.parents
    for evidence in (policy.provenance,policy.drawdown_order_provenance,policy.arrival_rule_provenance):
        assert isinstance(evidence,Provenance) and evidence.created_by and evidence.parents
        assert SOURCE in evidence.parents
    arrival=policy.arrival_rule_provenance
    assert policy.default_arrival_remaining_fraction==.1
    assert any('data/raw/is-code/msc267-85.pdf' in parent and 'PDF p41' in parent
               and '3.4.1.2' in parent and '3.4.1.3' in parent for parent in arrival.parents)
    assert any('D50' in parent for parent in arrival.parents)


def test_declares_d57_common_fraction_and_legacy_order_has_no_fill_effect():
    policy=_build([_tank('FO-P','fuel_oil',20.),_tank('FO-S','fuel_oil',20.)])
    for evidence in (policy.provenance,policy.arrival_rule_provenance):
        assert any('D57' in parent for parent in evidence.parents)
        assert any('proportional' in parent.lower() and 'every consumable tank' in parent.lower()
                   for parent in evidence.parents)
    assert any('documentation only' in parent and 'does not control' in parent
               for parent in policy.drawdown_order_provenance.parents)
    assert not any('D50 checks declared departure/arrival endpoints' in parent
                   for parent in policy.arrival_rule_provenance.parents)


def test_absent_lube_oil_and_stores_do_not_invent_groups():
    policy=_build([_tank('FO','fuel_oil',50.),_tank('DO','diesel_oil',30.),_tank('FW','fresh_water',20.)])
    assert {group.kind for group in policy.groups}=={'fuel_oil','diesel_oil','fresh_water'}
    assert any('lube_oil' in parent and 'stores' in parent and 'absent' in parent.lower()
               for parent in policy.provenance.parents)


def test_geometry_only_catalog_is_explicit_empty_diagnostic_not_missing_policy():
    policy=_build([])
    assert policy.groups==[] and policy.drawdown_order==[]
    assert any(parent.startswith('DIAGNOSTIC:') for parent in policy.provenance.parents)
    assert policy.arrival_rule_provenance.parents and policy.drawdown_order_provenance.parents
    assert _bind([],policy).voyage_policy is not None


@pytest.mark.parametrize('field,value',[
    ('content_density_kg_m3',0.),('content_density_kg_m3',float('inf')),
    ('content_density_kg_m3',True),('capacity_m3',float('nan')),
])
def test_bad_consumable_capacity_rejected_by_producer_and_model_guard(field,value):
    tank=_tank('FO','fuel_oil',10.); good=_build([tank])
    setattr(tank,field,value)
    with pytest.raises(ValueError,match='finite|positive'):
        _build([tank])
    with pytest.raises(ValueError,match='finite|positive|capacity_m3'):
        _bind([tank],good)


def test_nonfinite_full_sounding_lcg_cannot_define_order():
    tank=_tank('FO','fuel_oil',10.)
    tank.table[-1].lcg_m=float('nan')
    with pytest.raises(ValueError,match='finite.*LCG'):
        _build([tank])


def test_duplicate_actual_tank_ids_reject_instead_of_hiding_membership():
    with pytest.raises(ValueError,match='distinct|duplicate'):
        _build([_tank('DUP','fuel_oil',10.),_tank('DUP','fresh_water',20.)])


def test_adjustability_never_substitutes_for_declared_consumable_kind():
    policy=_build([_tank('FO-ADJ','fuel_oil',20.,adjustable=True),
                   _tank('BALLAST-FIXED','ballast',30.,adjustable=False)])
    assert policy.drawdown_order==['FO-ADJ']
    assert policy.groups[0].kind=='fuel_oil'


def test_finite_volume_and_density_cannot_overflow_mass_capacity():
    tank=_tank('FO','fuel_oil',10.); good=_build([tank])
    tank.content_density_kg_m3=1e308
    with pytest.raises(ValueError,match='mass capacity must be finite'):
        _build([tank])
    with pytest.raises(ValueError,match='mass capacity must be finite'):
        _bind([tank],good)
