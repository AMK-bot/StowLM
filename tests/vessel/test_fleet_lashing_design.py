"""D56 design uses the six actual D27/D50 loading-condition GM evaluations."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from stowage.model import Provenance
from stowage.model.fixtures import build_instance


def test_design_cap_is_ten_percent_above_highest_of_all_six_conditions(monkeypatch):
    from stowage.vessel import fleet_lashing_design as module
    vessel=build_instance().vessel
    vessel.hull.max_gm_lashing_m=None
    vessel.hull.max_gm_lashing_m_provenance=None
    recipes=[dict(condition=label,route=('THLCH','THBKK','THSGZ'),cargo_cells=[],
                  departure_tank_masses_kg={},recipe_source='synthetic-unit-recipe',
                  ballast_label='DB+HEEL',historical_witness=False)
             for label in ('C1','C2','C3')]
    monkeypatch.setattr(module,'standard_manual_conditions',lambda vessel,params: recipes)
    evaluated=[]
    values={('C1','departure'):1.,('C1','arrival'):1.2,
            ('C2','departure'):3.5,('C2','arrival'):4.,
            ('C3','departure'):2.,('C3','arrival'):2.5}
    def actual(vessel,params,recipe,phase):
        evaluated.append((recipe['condition'],phase))
        return dict(gm_m=values[recipe['condition'],phase],locode='THLCH' if phase=='departure' else 'THBKK',
                    water_density_kg_m3=1025.,condition_id=recipe['condition']+'.'+phase)
    monkeypatch.setattr(module,'evaluate_manual_condition',actual)
    audit=module.populate_lashing_design(vessel,{},'synthetic parameter hash')
    assert len(evaluated)==6 and set(evaluated)==set(values)
    assert audit['max_gm_lashing_m']==pytest.approx(4.4)
    assert vessel.hull.max_gm_lashing_m==pytest.approx(4.4)
    assert audit['governing']['condition']=='C2' and audit['governing']['phase']=='arrival'
    assert audit['factor']==1.1
    assert len(audit['conditions'])==6
    evidence=vessel.hull.max_gm_lashing_m_provenance
    assert json.loads(evidence.source)==audit
    assert evidence.assumption.startswith('APPROXIMATION:')
    assert evidence.provenance.created_by and evidence.provenance.parents
    assert any('C4' in item for item in audit['excluded'])


def test_unknown_geometry_design_stays_unknown():
    from stowage.vessel.fleet_lashing_design import populate_lashing_design
    vessel=build_instance().vessel.model_copy(update={'hull':None})
    assert populate_lashing_design(vessel,{},'source') is None


@pytest.mark.parametrize('gm',[float('nan'),float('inf')])
def test_nonfinite_condition_cannot_partly_populate_design(monkeypatch,gm):
    from stowage.vessel import fleet_lashing_design as module
    vessel=build_instance().vessel
    vessel.hull.max_gm_lashing_m=None
    monkeypatch.setattr(module,'standard_manual_conditions',lambda *args:[{'condition':label} for label in ('C1','C2','C3')])
    monkeypatch.setattr(module,'evaluate_manual_condition',lambda *args:dict(gm_m=gm))
    with pytest.raises(ValueError,match='finite'):
        module.populate_lashing_design(vessel,{},'source')
    assert vessel.hull.max_gm_lashing_m is None


@pytest.mark.parametrize('locode,density',[('THLCH',1025.),('THBKK',1025.),('VNSGN',1000.),('HKHKG',1025.),('MYPKG',1025.)])
def test_manual_density_resolver_preserves_declared_d34_profiles(locode,density):
    from stowage.vessel.fleet_manual import manual_port
    profile=manual_port(locode)
    assert profile.locode==locode
    assert profile.water_density_kg_m3==density
    assert profile.sources


def test_unknown_manual_density_cannot_be_replaced_with_seawater():
    from stowage.vessel.fleet_manual import manual_port
    with pytest.raises(ValueError,match='density|profile'):
        manual_port('ZZZZZ')


def test_uniform_mass_scan_count_matches_discrete_placement():
    from stowage.vessel.fleet_manual import place_manual_cargo, uniform_placement_count
    vessel=build_instance().vessel
    for mass in (2300.,14000.,24900.,25000.,30400.):
        placements=place_manual_cargo(vessel,[mass]*len(vessel.cells))
        assert uniform_placement_count(vessel,mass)==len(placements)
        assert all(not next(s for s in vessel.stacks if s.id==next(c.stack_id for c in vessel.cells if c.id==cid)).on_deck
                   for cid,value in placements if value>=25000.)


def test_pinned_ballast_recipe_recovers_binary_peak_and_deep_tank_mapping():
    from stowage.vessel.fleet_manual import manual_ballast_masses
    def tank(tid):
        return SimpleNamespace(id=tid,adjustable=True,capacity_m3=10.,content_density_kg_m3=1000.)
    vessel=SimpleNamespace(tanks=[tank(tid) for tid in ('DB-P','HEEL-P','FPT-P','FWD-DEEP-P','APT-P','WING-A-P')])
    masses=manual_ballast_masses(vessel,'DB+HEEL+FPT0.38')
    assert masses=={'DB-P':10000.,'HEEL-P':10000.,'FPT-P':3750.,'FWD-DEEP-P':3750.,'APT-P':0.,'WING-A-P':0.}
