"""D56 design GM covers each declared standard cargo condition endpoint."""
import json
from math import isfinite

import pytest

from stowage.model import Vessel
from stowage.vessel.fleet import load_vessel
from stowage.vessel.fleet_slices import slice_preset, slice_vessel, recondition_slice


@pytest.mark.parametrize('name,booking_mass',[
    ('feeder_500',1917238.3810685035),('feeder_800',3734513.4862812967),
    ('feeder_1700',6914017.2082863245),
])
def test_d56_standard_condition_gm_cap_is_declared_deterministic_and_inherited(name,booking_mass):
    full=load_vessel(name)
    assert full.hull.max_gm_lashing_m is not None, 'D56 requires a loading-manual design GM'
    cap=full.hull.max_gm_lashing_m
    assert isfinite(cap) and cap>0
    provenance=full.hull.max_gm_lashing_m_provenance
    assert provenance is not None and provenance.source and provenance.assumption
    audit=json.loads(provenance.source)
    rows=audit['conditions']
    assert {(r['condition'],r['phase']) for r in rows}=={
        (condition,phase) for condition in ['C1','C2','C3'] for phase in ['departure','arrival']}
    assert len(rows)==6
    assert all(isfinite(row['gm_m']) and cap>row['gm_m'] for row in rows)
    largest=max(row['gm_m'] for row in rows)
    assert cap==pytest.approx(1.1*largest,rel=1e-12)
    governing=audit['governing']
    assert governing['gm_m']==largest
    assert governing in rows
    assert all(row['water_density_kg_m3']==1025. for row in rows)
    assert all(row['locode']==('THLCH' if row['phase']=='departure' else 'THBKK') for row in rows)
    repeated=load_vessel(name)
    assert repeated.hull.max_gm_lashing_m==cap
    assert repeated.hull.max_gm_lashing_m_provenance==provenance
    regions=[slice_preset(full,size) for size in ['tiny','small','medium','full']]
    regions.append(slice_vessel(full,[1,3],name=f'{name}.d56-custom'))
    regions.append(recondition_slice(regions[2],expected_load_mass_kg=booking_mass,seed=1))
    for region in [full,*regions]:
        assert region.hull.max_gm_lashing_m==cap
        assert region.hull.max_gm_lashing_m_provenance==provenance
        assert Vessel.model_validate_json(region.model_dump_json())==region
