"""D48 booking references stay inside actual, bounded decision geometry."""
from math import fsum, isfinite

import pytest

from stowage.conventions import CONTAINER_HEIGHT_STD_M
from stowage.model import Cell, HatchCover, Stack, Vessel

H = CONTAINER_HEIGHT_STD_M


def _vessel():
    cells = [Cell(id=cid, stack_id=sid, lcg_m=20., tcg_m=-1.5, vcg_base_m=z)
             for cid,sid,z in [('0010102','00101H',2.), ('0010104','00101H',2.+H),
                              ('0010182','00101D',10.), ('0010184','00101D',10.+H)]]
    stacks = [Stack(id=sid,bay=1,row=1,on_deck=deck,cell_ids=[c.id for c in cells if c.stack_id==sid],
                    max_mass_kg=cap/2,max_mass_20_kg=cap,max_height_m=2*H,base_z_m=base,
                    hatch_cover_id='HC-TEST')
              for sid,deck,cap,base in [('00101H',False,40000.,2.),('00101D',True,30000.,10.)]]
    cover=HatchCover(id='HC-TEST',bays=[1],hold_stack_ids=['00101H'],deck_stack_ids=['00101D'],
                     mass_kg=1000.,lcg_m=20.,tcg_m=-1.5,vcg_m=9.7)
    return Vessel(id='booking-test',name='Booking test',nominal_teu=4,cells=cells,stacks=stacks,hatch_covers=[cover])


def _reference(vessel, placements, mass):
    from stowage.vessel.fleet_booking import booking_reference
    return booking_reference(vessel,placements,expected_load_mass_kg=mass,reference_seed=29)


def _assert_bounded(vessel, audit, mass):
    assert audit['method']=='bounded_c3_heavy_low_iso20_v1'
    assert audit['mass_kg']==pytest.approx(mass)
    assert fsum(row['mass_kg'] for row in audit['cells'])==pytest.approx(mass)
    assert audit['reference_seed']==29
    assert audit['actual_booking_stow'] is False
    assert audit['contributes_fixed_mass'] is False
    assert audit['approximation'].startswith('APPROXIMATION:')
    by_id={row['cell_id']:row for row in audit['cells']}
    assert by_id.keys()<={cell.id for cell in vessel.cells}
    for stack in vessel.stacks:
        values=[by_id.get(cid,{}).get('mass_kg',0.) for cid in stack.cell_ids]
        occupied=[cid for cid in stack.cell_ids if cid in by_id]
        assert occupied==stack.cell_ids[:len(occupied)]
        assert sum(values)<=(stack.max_mass_20_kg if stack.max_mass_20_kg is not None else stack.max_mass_kg)+1e-6
        assert all(value<=(24999. if stack.on_deck else 30480.)+1e-6 for value in values)
        assert all(upper<=lower+1e-6 for lower,upper in zip(values,values[1:]))
        assert len(occupied)*H<=stack.max_height_m+1e-9
    assert all(isfinite(value) for value in audit['cg_m'])
    if mass:
        expected=[fsum((row['mass_kg']/mass)*row['cg_m'][axis] for row in audit['cells']) for axis in range(3)]
        assert audit['cg_m']==pytest.approx(expected)


def test_scaled_c3_preserves_reference_weights_and_cg_without_mutating_vessel():
    vessel=_vessel(); original=vessel.model_dump_json()
    placements=[('0010102',24000.),('0010104',10000.),('0010182',9000.),('9990102',1e6)]
    audit=_reference(vessel,placements,21500.)
    assert audit['allocation_mode']=='scaled_C3'
    assert audit['normalization']==.5
    assert {row['cell_id']:row['mass_kg'] for row in audit['cells']}=={
        '0010102':12000.,'0010104':5000.,'0010182':4500.}
    _assert_bounded(vessel,audit,21500.)
    assert vessel.model_dump_json()==original
    assert _reference(vessel,list(reversed(placements)),21500.)==audit


@pytest.mark.parametrize('mass',[60000.,70000.])
def test_higher_booking_fills_low_cells_with_all_capacity_bounds(mass):
    vessel=_vessel(); audit=_reference(vessel,[('0010102',2200.)],mass)
    assert audit['allocation_mode']=='bounded_heavy_low'
    assert audit['capacity_kg']==70000.
    values={row['cell_id']:row['mass_kg'] for row in audit['cells']}
    assert values['0010102']==30480.
    assert values['0010104']==9520.
    # The deck remains usable even though it shares the decision hold's cover.
    assert values['0010182']>0
    _assert_bounded(vessel,audit,mass)
    assert _reference(vessel,[],mass)['cells']==audit['cells']


def test_partial_region_never_uses_omitted_or_nondecision_cells():
    vessel=_vessel(); chosen={'0010102','0010182'}
    vessel=vessel.model_copy(update={
        'cells':[cell for cell in vessel.cells if cell.id in chosen],
        'stacks':[stack.model_copy(update={'cell_ids':[cid for cid in stack.cell_ids if cid in chosen]})
                  for stack in vessel.stacks]})
    audit=_reference(vessel,[('0010104',20000.),('0010184',18000.)],50000.)
    assert audit['capacity_kg']==55479.
    _assert_bounded(vessel,audit,50000.)
    assert {row['cell_id'] for row in audit['cells']}==chosen


@pytest.mark.parametrize('placements',[[('0010104',10000.)],[('0010102',1000.),('0010104',9000.)]])
def test_unsupported_or_inverted_reference_falls_back_to_supported_low_tiers(placements):
    vessel=_vessel(); audit=_reference(vessel,placements,5000.)
    assert audit['allocation_mode']=='bounded_heavy_low'
    assert audit['cells'][0]['cell_id']=='0010102'
    _assert_bounded(vessel,audit,5000.)


def test_zero_mass_has_declared_finite_cg_and_no_reference_mass():
    vessel=_vessel(); audit=_reference(vessel,[],0.)
    assert audit['allocation_mode']=='zero_mass'
    assert audit['cells']==[]
    assert audit['zero_mass_cg_policy']=='lowest_supported_iso20_cell'
    _assert_bounded(vessel,audit,0.)


@pytest.mark.parametrize('mass',[-1.,float('inf'),float('nan'),True])
def test_invalid_booking_mass_rejects(mass):
    with pytest.raises(ValueError,match='booking mass'):
        _reference(_vessel(),[],mass)


def test_above_capacity_fails_closed_even_without_hull():
    with pytest.raises(ValueError,match='booking mass exceeds.*decision.*capacity'):
        _reference(_vessel(),[],70000.1)


def test_non20_and_unsupported_floor_cells_do_not_create_capacity():
    vessel=_vessel()
    for cell in vessel.cells:
        if cell.stack_id=='00101H': cell.accepts_20=False
    next(stack for stack in vessel.stacks if stack.on_deck).max_height_m=H/2
    with pytest.raises(ValueError,match='decision.*capacity'):
        _reference(vessel,[],1.)
    audit=_reference(vessel,[],0.)
    assert audit['capacity_kg']==0.
    assert audit['cg_m']==[0.,0.,0.]
    assert audit['zero_mass_cg_policy']=='origin_no_supported_iso20_cells'


@pytest.mark.parametrize('axis',['lcg_m','tcg_m','vcg_base_m'])
def test_nonfinite_geometry_fails_closed(axis):
    vessel=_vessel(); setattr(vessel.cells[0],axis,float('nan'))
    with pytest.raises(ValueError,match='finite.*geometry'):
        _reference(vessel,[],1000.)


def test_arbitrarily_small_upper_only_reference_cannot_skip_a_support():
    vessel=_vessel(); audit=_reference(vessel,[('0010104',5e-7)],2.5e-7)
    assert audit['allocation_mode']=='bounded_heavy_low'
    assert audit['cells'][0]['cell_id']=='0010102'
    _assert_bounded(vessel,audit,2.5e-7)


def test_explicit_zero_stack_rating_never_falls_back_to_general_rating():
    vessel=_vessel()
    next(stack for stack in vessel.stacks if not stack.on_deck).max_mass_20_kg=0.
    audit=_reference(vessel,[],30000.)
    assert audit['capacity_kg']==30000.
    assert all(row['stack_id']=='00101D' for row in audit['cells'])
    with pytest.raises(ValueError,match='exceeds.*capacity'):
        _reference(vessel,[],30000.1)


def test_upper_only_partial_geometry_has_no_fabricated_bottom_support():
    vessel=_vessel(); upper=next(cell for cell in vessel.cells if cell.id=='0010104')
    stack=next(stack for stack in vessel.stacks if not stack.on_deck)
    vessel=vessel.model_copy(update={'cells':[upper], 'stacks':[stack.model_copy(update={'cell_ids':[upper.id]})]})
    with pytest.raises(ValueError,match='exceeds.*capacity'):
        _reference(vessel,[(upper.id,1000.)],1000.)
    assert _reference(vessel,[],0.)['capacity_kg']==0.


def test_underflowing_reference_scale_falls_back_without_losing_positive_mass():
    mass=5e-324
    audit=_reference(_vessel(),[('0010102',30000.)],mass)
    assert audit['allocation_mode']=='bounded_heavy_low'
    assert audit['mass_kg']==audit['cells'][0]['mass_kg']==mass
    _assert_bounded(_vessel(),audit,mass)
