"""D64 rev 3 HEEL transfer and worked-tank FSM, isolated synthetic solids."""
import pytest

from stowage.checker.in_port_liquids import prepare_context
from stowage.checker.stability_loading import LoadingCondition
from stowage.model import TankPair
from stowage.model.fixtures import build_instance


@pytest.fixture
def liquid_case(monkeypatch):
    from stowage.checker import stability
    i = build_instance()
    i.vessel.tank_pairs = [TankPair(id='explicit-pair', port_tank='WB-P', starboard_tank='WB-S')]
    params = {'tanks': [dict(id=tid, geometry=dict(x_min_m=10., x_max_m=20.,
        y_min_m=y-1., y_max_m=y+1., z_min_m=0., z_max_m=5.))
        for tid, y in [('WB-P', 5.), ('WB-S', -5.), ('FO-1', 0.)]]}
    monkeypatch.setattr(stability, '_context', lambda _: (None, params))
    return i


def test_t3_transfer_closed_form_preserves_total_and_reports_authority(liquid_case):
    i = liquid_case
    ctx = prepare_context(i, i.onboard)
    moved, metrics = ctx.transferred_ship(i.onboard, heel_moment_kgm=200_000.)
    assert moved.tank_masses_kg['WB-P'] == pytest.approx(31_250.)
    assert moved.tank_masses_kg['WB-S'] == pytest.approx(71_250.)
    assert metrics['heel_transfer_kg'] == pytest.approx(20_000.)
    assert metrics['heel_authority_kgm'] == pytest.approx(512_500.)
    assert metrics['heel_authority_remaining_kgm'] == pytest.approx(312_500.)
    assert metrics['heel_residual_moment_kgm'] == pytest.approx(0.)
    assert i.onboard.tank_masses_kg['WB-P'] == 51_250.


@pytest.mark.parametrize('moment', [-1_000_000., 1_000_000.])
def test_t3_transfer_clips_to_capacity(liquid_case, moment):
    ctx = prepare_context(liquid_case, liquid_case.onboard)
    moved, metrics = ctx.transferred_ship(liquid_case.onboard, heel_moment_kgm=moment)
    assert sorted(moved.tank_masses_kg[t] for t in ('WB-P', 'WB-S')) == [0., 102_500.]
    assert metrics['heel_authority_remaining_kgm'] == pytest.approx(0.)
    assert abs(metrics['heel_residual_moment_kgm']) == pytest.approx(487_500.)


def test_t3_dry_pair_has_zero_authority(liquid_case):
    i = liquid_case
    i.onboard.tank_masses_kg.update({'WB-P': 0., 'WB-S': 0.})
    ctx = prepare_context(i, i.onboard)
    _, metrics = ctx.transferred_ship(i.onboard, heel_moment_kgm=50_000.)
    assert metrics['heel_transfer_kg'] == 0.
    assert metrics['heel_authority_kgm'] == 0.
    assert metrics['heel_residual_moment_kgm'] == 50_000.


def test_pair_is_explicit_and_departure_total_cannot_change(liquid_case):
    i = liquid_case
    departure = i.onboard.model_copy(deep=True)
    departure.tank_masses_kg['WB-P'] += 1.
    with pytest.raises(ValueError, match='pair total'):
        prepare_context(i, departure)
    i.vessel.tank_pairs = []
    with pytest.raises(ValueError, match='explicit'):
        prepare_context(i, i.onboard)


def test_asymmetric_non_heel_departure_rejected(liquid_case):
    i = liquid_case
    for source, tid in [('WB-P', 'other-a'), ('WB-S', 'other-b')]:
        i.vessel.tanks.append(next(t for t in i.vessel.tanks if t.id == source).model_copy(update={'id': tid}))
    departure = i.onboard.model_copy(deep=True)
    departure.tank_masses_kg.update({'other-a': 100., 'other-b': 101.})
    with pytest.raises(ValueError, match='symmetric'):
        prepare_context(i, departure)


def test_t11_dry_and_pressed_heel_always_charge_maximum(liquid_case):
    i = liquid_case
    i.onboard.tank_masses_kg.update({'WB-P': 0., 'WB-S': 102_500.})
    ctx = prepare_context(i, i.onboard)
    original = LoadingCondition(4_000_000., 50., 0., 3., 0., 0.,
        omitted_tanks=('WB-P', 'WB-S'))
    corrected = ctx.correct_loading(original)
    # Each exact 10 x 2 metre free surface has I = length*width^3/12.
    heel_fsm = 2 * 1025. * 10. * 2.**3 / 12.
    fuel_fsm = 950. * 10. * 2.**3 / 12.
    assert corrected.fsm_kgm == pytest.approx(heel_fsm + fuel_fsm)
    assert corrected.fsc_m == pytest.approx(corrected.fsm_kgm / corrected.mass_kg)
    assert corrected.actual_tanks == original.actual_tanks
    assert not corrected.omitted_tanks
    assert {'WB-P', 'WB-S'} <= {t.id for t in corrected.tanks if t.variable}
    assert original.fsm_kgm == 0.


def test_t11_changed_adjustable_center_tank_uses_maximum(liquid_case):
    i = liquid_case
    center = next(t for t in i.vessel.tanks if t.id == 'FO-1').model_copy(update={'id': 'center', 'kind': 'ballast',
        'adjustable': True, 'content_density_kg_m3': 1025.})
    i.vessel.tanks.append(center)
    departure = i.onboard.model_copy(deep=True)
    departure.tank_masses_kg['center'] = 10_250.
    ctx = prepare_context(i, departure).for_phase('departure')
    ctx.params['tanks'].append(dict(id='center', geometry=dict(
        x_min_m=10., x_max_m=20., y_min_m=-1., y_max_m=1., z_min_m=0., z_max_m=5.)))
    assert 'center' in ctx.max_fsm_tank_ids
    assert 'center' in {t.id for t in ctx.correct_loading(
        LoadingCondition(4e6, 50., 0., 3., 0., 0.)).tanks}


def test_fsm_needs_exact_geometry_instead_of_sounding_table_fallback(liquid_case):
    i = liquid_case
    ctx = prepare_context(i, i.onboard)
    ctx.params['tanks'] = []
    with pytest.raises(ValueError, match='geometry'):
        ctx.correct_loading(LoadingCondition(4e6, 50., 0., 3., 0., 0.))


def test_fill_dependent_heel_uses_exact_scalar_moment_refinement(liquid_case):
    """An asymmetric section's transverse centroid changes with fill."""
    from stowage.vessel.tanks import TankGeometry
    from stowage.checker.stability_sounding import sounding
    i = liquid_case
    ctx = prepare_context(i, i.onboard)
    # A tilted parallelogram: width 2, height 5, volume 100 m³;
    # each liquid layer moves progressively outward as its height increases.
    for tid, sign in [('WB-P', 1.), ('WB-S', -1.)]:
        polygon = [[sign*y, z] for y, z in [(4., 0.), (6., 0.), (7., 5.), (5., 5.)]]
        if sign < 0:
            polygon.reverse()
        geometry = dict(shape='hull_sections', x_min_m=10., x_max_m=20.,
            y_min_m=4. if sign > 0 else -7., y_max_m=7. if sign > 0 else -4.,
            z_min_m=0., z_max_m=5., x_m=[12.5, 17.5], integration_weights_m=[5., 5.],
            sections_yz_m=[[polygon], [polygon]])
        record = next(r for r in ctx.params['tanks'] if r['id'] == tid)
        record['geometry'] = geometry
        tank = next(t for t in i.vessel.tanks if t.id == tid)
        solid = TankGeometry.from_params(record)
        tank.table = [sounding(solid, volume) for volume in (0., 50., 100.)]
    # Revalidate now that the physically correct fill-dependent tables exist.
    ctx = prepare_context(i, i.onboard)
    moved, metrics = ctx.transferred_ship(i.onboard, heel_moment_kgm=200_000.)
    assert metrics['heel_residual_moment_kgm'] == pytest.approx(0., abs=1e-5)
    assert metrics['heel_transfer_moment_kgm'] == pytest.approx(200_000.)
    assert moved.tank_masses_kg['WB-P'] != pytest.approx(31_250.)


@pytest.mark.parametrize('destination', ['quay', 'other-cover'])
def test_cached_transverse_moments_equal_exact_loading_with_cargo_and_covers(liquid_case, destination):
    from stowage.model import Placement
    from stowage.checker.stability_loading import loading_condition
    i = liquid_case
    cover, other = i.vessel.hatch_covers[:2]
    ship = i.onboard.model_copy(deep=True)
    box = next(b for b in i.containers if b.length_ft == 20)
    ship.placements = [Placement(container_id=box.id, slot=i.vessel.cells[0].id)]
    ship.covers_off = {cover.id: other.id if destination == 'other-cover' else 'quay'}
    ctx = prepare_context(i, i.onboard)
    condition = loading_condition(i, ship, geometry=ctx.geometry, ship_params=ctx.params,
        precomputed_soundings=True, small_tank_exemption=True)
    assert not condition.errors
    expected = condition.mass_kg*condition.tcg_m
    assert ctx._heel_moment(ship, 'arrival') == pytest.approx(expected, abs=1e-6)
    _, metrics = ctx.transferred_ship(ship)
    assert metrics['heel_residual_moment_kgm'] == pytest.approx(0., abs=1e-6)


def _add_center_ballast(i, tid):
    source = next(t for t in i.vessel.tanks if t.id == 'FO-1')
    i.vessel.tanks.append(source.model_copy(update={'id': tid, 'kind': 'ballast',
        'adjustable': True, 'content_density_kg_m3': 1025.}))


def test_t11_two_pass_fsm_arrival_independent_departure_exact_changed_delta(liquid_case):
    from stowage.checker.stability_loading import CorrectionTank
    i = liquid_case
    _add_center_ballast(i, 'worked')
    _add_center_ballast(i, 'unchanged')
    baseline = i.onboard.model_copy(deep=True)
    candidate = i.onboard.model_copy(deep=True)
    candidate.tank_masses_kg['worked'] = 102_500.
    first = prepare_context(i, baseline)
    second = prepare_context(i, candidate)
    for ctx in (first, second):
        for tid in ('worked', 'unchanged'):
            ctx.params['tanks'].append(dict(id=tid, geometry=dict(
                x_min_m=10., x_max_m=20., y_min_m=-1., y_max_m=1.,
                z_min_m=0., z_max_m=5.)))
    actual = CorrectionTank('unchanged', 1025., 10., 321., None, variable=False)
    condition = LoadingCondition(4e6, 50., 0., 3., 321./4e6, 321., tanks=(actual,))
    arrival_first = first.for_phase('arrival')
    arrival_second = second.for_phase('arrival')
    assert first.max_fsm_tank_ids == arrival_first.max_fsm_tank_ids
    assert second.max_fsm_tank_ids == arrival_second.max_fsm_tank_ids
    assert arrival_first.max_fsm_tank_ids == arrival_second.max_fsm_tank_ids
    assert 'worked' not in arrival_second.max_fsm_tank_ids
    before = arrival_first.correct_loading(condition)
    after = arrival_second.correct_loading(condition)
    assert before.fsm_kgm == after.fsm_kgm
    assert next(t for t in after.tanks if t.id == 'unchanged') is actual
    departure_first = first.for_phase('departure')
    departure_second = second.for_phase('departure')
    assert departure_second.max_fsm_tank_ids - departure_first.max_fsm_tank_ids == {'worked'}
    unchanged = departure_first.correct_loading(condition)
    changed = departure_second.correct_loading(condition)
    exact_extra_fsm = 1025.*10.*2.**3/12.
    assert changed.fsm_kgm-unchanged.fsm_kgm == pytest.approx(exact_extra_fsm)
    gm_solid = 2.
    assert (gm_solid-unchanged.fsc_m)-(gm_solid-changed.fsc_m) == pytest.approx(
        exact_extra_fsm/condition.mass_kg)
    assert next(t for t in changed.tanks if t.id == 'unchanged') is actual
    # Shared maxima caches must never change a previously selected phase's set.
    assert 'worked' not in arrival_second.max_fsm_tank_ids
    assert arrival_second.correct_loading(condition).fsm_kgm == before.fsm_kgm
    assert departure_second.for_phase('arrival').max_fsm_tank_ids == first.max_fsm_tank_ids


def test_t11_departure_only_consumable_cannot_change_arrival_selection(liquid_case):
    i = liquid_case
    source = next(t for t in i.vessel.tanks if t.id == 'FO-1')
    i.vessel.tanks.append(source.model_copy(update={'id': 'dry-water', 'kind': 'fresh_water'}))
    candidate = i.onboard.model_copy(deep=True)
    candidate.tank_masses_kg['dry-water'] = 1_000.
    ctx = prepare_context(i, candidate)
    assert 'dry-water' not in ctx.max_fsm_tank_ids
    assert 'dry-water' not in ctx.for_phase('arrival').max_fsm_tank_ids
    assert 'dry-water' not in ctx.for_phase('departure').max_fsm_tank_ids
    with pytest.raises(ValueError, match='phase'):
        ctx.for_phase('invalid')
