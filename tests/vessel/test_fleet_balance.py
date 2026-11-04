"""Booking-conditioned backgrounds balance moments with a ballast control reserve.

A loading condition is balanced when its mass centre lies above the upright
buoyancy centre; GM is KM minus KG (IMO 2008 IS Code A2.2). These tests check
that construction contract, not final intact-stability or liquid acceptance.
"""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.conventions import CONTAINER_HEIGHT_STD_M
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_balance import ballast_moment_reserve, balance_background
from stowage.vessel.fleet_conditions import slice_condition
from stowage.vessel.fleet_slices import recondition_slice


def _independent_points(vessel, own_mass, own_cg):
    """Evaluate emitted point loads and live parameter sounding centres."""
    params = load_ship_params(vessel.slice_of or vessel.id)
    h = params['hull']
    points = [(h['lightship_lcg_m'], h.get('lightship_tcg_m', 0.),
               h['lightship_vcg_m'], h['lightship_mass_kg'])]
    points.extend(tuple(load) for load in vessel.fixed_loads)
    for tank in params['tanks']:
        # with_hull=False omits built tanks, while the source-only condition
        # screen still accounts for the documented live departure liquids.
        ballast = slice_condition(vessel).get('departure_ballast_masses_kg', {})
        mass = (ballast.get(tank['id'], 0.) if tank.get('adjustable', False) else
                .97*tank['capacity_m3']*tank['content_density_kg_m3'])
        if not mass:
            continue
        volume = mass/tank['content_density_kg_m3']
        centre = [float(np.interp(volume, [row['volume_m3'] for row in tank['table']],
                                  [row[axis] for row in tank['table']]))
                  for axis in ('lcg_m', 'tcg_m', 'vcg_m')]
        points.append((*centre, mass))
    mass = sum(point[3] for point in points)
    cg = np.array([sum(point[axis]*point[3] for point in points)/mass for axis in range(3)])
    return params, mass, cg


@pytest.mark.parametrize('name', ['feeder_500', 'feeder_800', 'feeder_1700'])
def test_actual_six_cell_booking_reference_balances_changed_booking_and_reserves_ballast(name):
    from stowage.generate.random_yard import _tiny_region
    from stowage.vessel.hydrostatics import HullGeometry

    parent = load_vessel(name, with_hull=False)
    selected = _tiny_region(parent)
    assert len(selected.cells) == 6
    selected_cells = {cell.id: cell for cell in selected.cells}
    originals = selected.model_dump_json()
    outputs = []
    for booking_mass in (45000., 125000.):
        vessel = recondition_slice(selected, expected_load_mass_kg=booking_mass, seed=5247)
        audit = slice_condition(vessel)
        # D48 uses a declared heavy-low mass reference on these exact cells,
        # rather than assigning equal mass to every available tier.
        reference = audit['booking_reference']
        assert reference['method'] == 'bounded_c3_heavy_low_iso20_v1'
        assert {row['cell_id'] for row in reference['cells']} <= selected_cells.keys()
        assert sum(row['mass_kg'] for row in reference['cells']) == pytest.approx(booking_mass)
        own_cg = tuple(sum(row['mass_kg']*(getattr(selected_cells[row['cell_id']], axis)
                            + (CONTAINER_HEIGHT_STD_M/2 if axis == 'vcg_base_m' else 0.))
                          for row in reference['cells'])/booking_mass
                       for axis in ('lcg_m', 'tcg_m', 'vcg_base_m'))
        assert reference['cg_m'] == pytest.approx(own_cg)
        params, mass, cg = _independent_points(vessel, booking_mass, own_cg)
        geometry = HullGeometry.from_params(params)
        screen = audit['gm_screen']
        upright = geometry.submerged(screen['screen_mean_draught_m'], screen['screen_mean_draught_m'])
        assert screen['expected_decision_cg_m'] == pytest.approx(own_cg)
        assert screen['target_reached']
        boxes = [box for load in vessel.fixed_loads for box in load.cargo_boxes or []]
        assert boxes and min(box.gross_mass_kg for box in boxes) >= 2200.
        deck_stacks = {stack.id for stack in vessel.fixed_load_stacks if stack.on_deck}
        deck = [box.gross_mass_kg for box in boxes if box.stack_id in deck_stacks]
        assert np.std(deck) > 1.
        sampling = screen['deck_mass_sampling']
        assert sampling['seed'] == 5247
        caps = sampling['per_box_upper_bound_kg']
        assert len(set(round(value, 6) for value in caps.values())) > 1
        assert all(box.gross_mass_kg <= caps[box.cell_ids[0]]+1e-6
                   for box in boxes if box.stack_id in deck_stacks)
        assert cg[0] == pytest.approx(upright.lcb_m, abs=1e-7)
        assert cg[1] == pytest.approx(upright.tcb_m, abs=1e-7)
        solid_gm = upright.vcb_m+upright.bm_t_m-cg[2]
        assert solid_gm == pytest.approx(screen['estimated_solid_gm_after_m'], abs=1e-7)
        low, high = screen.get('allowed_solid_gm_band_m',
            [screen['requested_solid_gm_m']-.12, screen['requested_solid_gm_m']+.12])
        assert low-1e-7 <= solid_gm <= high+1e-7
        assert solid_gm > .15
        assert sum(audit['departure_ballast_masses_kg'].values()) > 0
        reserve = audit['ballast_moment_reserve']
        assert reserve['reserve_feasible']
        assert reserve['displacement_headroom_kg'] >= reserve['required_mass_headroom_kg']
        for direction in reserve['directions'].values():
            assert direction['attainable_reserve_fraction'] >= .30-1e-9
        assert mass == pytest.approx(screen['expected_background_mass_kg'])
        assert mass+booking_mass == pytest.approx(audit['target_displacement_kg'])
        assert mass <= .97*audit['design_displacement_kg'] + 1e-5
        assert all(load.kind == 'cargo' for load in vessel.fixed_loads)
        # Full emitted envelopes, not their CGs, must clear the conservative
        # eye-to-sea boundary used for SOLAS V/22 background design.
        eye = vessel.bridge
        waterline = screen['screen_mean_draught_m']-.1
        height = eye.eye_z_m-waterline
        boundary = eye.bow_x_m-eye.x_m+min(2*params['hull']['loa_m'], 500.)
        for load in vessel.fixed_loads:
            box = load.silhouette
            if box is not None and box.x_max_m > eye.x_m:
                assert (height*(box.x_max_m-eye.x_m)
                        + boundary*(box.z_max_m+.3-waterline)
                        <= boundary*height+1e-7)
        outputs.append((mass+booking_mass, sum(load.mass_kg for load in vessel.fixed_loads)))
    assert outputs[0][0] == pytest.approx(outputs[1][0])
    assert outputs[0][1]-outputs[1][1] == pytest.approx(80000.)
    assert selected.model_dump_json() == originals


@pytest.mark.parametrize('roundoff', [False, True], ids=['nominal', 'sub_kg_order_residual'])
def test_balance_linear_program_matches_moments_without_hydrostatic_solver(monkeypatch, roundoff):
    import stowage.vessel.fleet_balance as module

    vessel = load_vessel('feeder_500', with_hull=False)
    cells = {cell.id: cell for cell in vessel.cells}
    eligible = [cid for stack in vessel.stacks for index, cid in enumerate(stack.cell_ids)
                if not stack.on_deck or index < 2]
    positions = np.array([[cell.lcg_m, cell.tcg_m,
                           cell.vcg_base_m+CONTAINER_HEIGHT_STD_M/2] for cid in eligible
                          for cell in [cells[cid]]])
    centroid = positions.mean(axis=0)
    own_mass = 20000.
    params = dict(hull=dict(lightship_mass_kg=10000., lightship_lcg_m=centroid[0],
                           lightship_tcg_m=centroid[1], lightship_vcg_m=centroid[2],
                           design_draught_m=7.05, lpp_m=100., beam_m=20.,
                           loa_m=load_ship_params('feeder_500')['hull']['loa_m']), tanks=[])
    target = centroid+np.array([.25, .05, .20])
    fake = SimpleNamespace(submerged=lambda aft, fwd: SimpleNamespace(
        displacement_kg=aft*1e6, lcb_m=target[0], tcb_m=target[1],
        vcb_m=target[2], bm_t_m=1.8))
    monkeypatch.setattr(module, '_hull', lambda params: fake)
    if roundoff:
        actual_linprog = module.linprog

        def numerical_feasibility_residue(*args, **kwargs):
            result = actual_linprog(*args, **kwargs)
            if result.success:
                # HiGHS accepts tiny inequality residuals in tonnes. Simulate
                # 0.00005 kg on a binding order row, well below one gram. A
                # physically ordered output must withstand this numerical
                # uncertainty, rather than serialize an inverted pair.
                n = len(kwargs['bounds']) // 3
                inequalities = kwargs['A_ub'].tocsr()
                for row_index in range(inequalities.shape[0]):
                    row = inequalities.getrow(row_index)
                    if len(row.indices) != 2 or not np.all(row.indices < n):
                        continue
                    if set(row.data) != {-1., 1.}:
                        continue
                    lower = row.indices[np.flatnonzero(row.data == -1.)[0]]
                    upper = row.indices[np.flatnonzero(row.data == 1.)[0]]
                    if abs(result.x[lower]-result.x[upper]) < 1e-7:
                        result.x[upper] = result.x[lower] + 5e-8
                        break
            return result

        monkeypatch.setattr(module, 'linprog', numerical_feasibility_residue)
    original = [(cid, 3000.) for cid in eligible]
    balanced, audit = balance_background(vessel, params, original, stack_ids=set(),
        expected_load_mass_kg=own_mass, expected_load_cg_m=tuple(centroid), target_gm_m=1.8)
    assert audit['target_reached']
    assert sum(value for _, value in balanced) == pytest.approx(len(original)*3000.)
    points = [(np.array([cells[cid].lcg_m, cells[cid].tcg_m,
                         cells[cid].vcg_base_m+CONTAINER_HEIGHT_STD_M/2]), mass)
              for cid, mass in balanced]
    # Expected bookings reserve deadweight but cannot contribute absent cargo
    # moments to a D58 empty-background condition.
    total = sum(value for _, value in balanced)+10000.
    cg = (sum((position*mass for position, mass in points), np.zeros(3))+10000.*centroid)/total
    assert cg == pytest.approx(target, abs=1e-8)
    masses = dict(balanced)
    assert min(masses.values()) >= 2200.
    for stack in vessel.stacks:
        values = [masses.get(cid, 0.) for cid in stack.cell_ids]
        assert sum(values) <= (stack.max_mass_20_kg or stack.max_mass_kg)+1e-7
        assert all(upper <= lower for lower, upper in zip(values, values[1:])), (
            stack.id, values)
        assert all(value <= (24999. if stack.on_deck else 30480.)+1e-7 for value in values)


def test_signed_reserve_uses_usable_displacement_headroom(monkeypatch):
    import stowage.vessel.fleet_balance as module

    fake = SimpleNamespace(submerged=lambda aft, fwd: SimpleNamespace(lcb_m=50., tcb_m=0.))
    monkeypatch.setattr(module, '_hull', lambda params: fake)
    tanks = []
    for tid, x, y in [('AP', 0., 0.), ('FP', 100., 0.), ('PORT', 50., 5.), ('STBD', 50., -5.)]:
        tanks.append(dict(id=tid, adjustable=True, capacity_m3=100., content_density_kg_m3=1000.,
                          table=[dict(volume_m3=0., lcg_m=x, tcg_m=y, vcg_m=1.),
                                 dict(volume_m3=100., lcg_m=x, tcg_m=y, vcg_m=1.)]))
    reserve = ballast_moment_reserve(dict(hull=dict(design_draught_m=7.), tanks=tanks), 1e6)
    assert reserve['reserve_feasible']
    assert reserve['required_mass_headroom_kg'] == pytest.approx(31500.)
    assert reserve['target_displacement_kg'] == pytest.approx(968500.)
    for direction in reserve['directions'].values():
        assert direction['reserve_fraction'] == .30
        assert direction['attainable_reserve_fraction'] == pytest.approx(.315)


def test_reserve_rejects_invalid_fraction():
    with pytest.raises(ValueError, match='between zero and one'):
        ballast_moment_reserve({}, 1e6, fraction=1.1)


@pytest.mark.parametrize('with_hull', [False, True])
def test_unsuccessful_condition_is_diagnostic_only_without_hull(monkeypatch, with_hull):
    """An optimizer failure must never become a certified production condition."""
    import stowage.vessel.fleet_balance as module
    from stowage.vessel.fleet_slices import slice_preset

    def unavailable(full, params, placements, **kwargs):
        return placements, dict(target_reached=False, optimizer_status=2,
                                optimizer_message='test: moment capacity exhausted')

    selected = slice_preset(load_vessel('feeder_500', with_hull=with_hull), 'small')
    before = selected.model_dump_json()
    monkeypatch.setattr(module, 'balance_background', unavailable)
    if with_hull:
        with pytest.raises(ValueError, match='cannot balance slice background'):
            recondition_slice(selected, expected_load_mass_kg=400000.)
    else:
        diagnostic = recondition_slice(selected, expected_load_mass_kg=400000.)
        audit = slice_condition(diagnostic)
        assert diagnostic.hull is None
        assert diagnostic.tanks == []
        assert audit['departure_policy'] == 'geometry_only_unconditioned'
        assert audit['gm_screen']['target_reached'] is False
        assert audit['gm_screen']['conditioning_status'] == 'deferred_missing_hull'
        assert 'missing hull' in audit['gm_screen']['deferred_reason']
        # The displacement-band flag remains a mass-accounting statement,
        # independent of the explicitly unsuccessful balance screen.
        assert audit['achieved_design_band']
    assert selected.model_dump_json() == before
