"""D64 synthetic affine fixtures exercise the necessary screen, not hull qualification."""
from types import SimpleNamespace as NS

import numpy as np
import pytest

from stowage.actions.space import Place
from stowage.feasibility.in_port import in_port_screen_enabled, screen_in_port_prefix
from stowage.linear.model import LinearConstraintSet, ValidityBand


@pytest.fixture
def context(monkeypatch):
    from stowage.checker import full
    import stowage.feasibility.in_port as in_port
    from stowage.rules import catalogue
    import stowage.linear as linear
    import stowage.linear.operations as operations

    monkeypatch.setattr(in_port, 'D64_COST_PRUNING_QUALIFIED', True)
    monkeypatch.setattr(catalogue, 'rule_freeze_v1_enabled', lambda: True)
    monkeypatch.setattr(full, 'IN_PORT_EVERY_LIFT_ENABLED', True, raising=False)
    band = ValidityBand(90., 200., 0., 20., 100., .1)
    model = NS(band=band, gm_min_m=.5, max_trim_m=100., max_list_rad=.1,
        cell_ids=('0010102',), tank_ids=('port', 'starboard', 'heelp', 'heels'),
        tank_adjustable=np.ones(4, dtype=bool), tank_capacity_kg=np.ones(4)*10.,
        tank_lcg_m=np.zeros(4), tank_tcg_m=np.array([2., -2., 5., -5.]),
        tank_vcg_m=np.zeros(4), const_mass_kg=100., hull_max_draught_m=10.,
        trim_line=(0., 0., 0.), draught_line=(0., .01),
        cell_lcg_m=np.zeros(1), cell_tcg_m=np.zeros(1), cell_vcg_m=np.zeros(1),
        const_moment_x_kgm=0., const_moment_y_kgm=0., const_moment_z_kgm=0.,
        km_line=(0., 10.), tank_fsm_max_kgm=np.zeros(4), measurement_diagnostics={})
    names = ('gm', 'trim', 'list_port', 'list_starboard', 'draught',
             'draught_aft', 'draught_forward', 'displacement', 'cargo_mass', 'sf@0')
    n = len(names)
    ac = np.zeros((n, 1)); at = np.zeros((n, 4)); const = np.zeros(n)
    lo = np.full(n, -np.inf); hi = np.full(n, np.inf)
    # All original rows except the selected in-port rows are deliberately impossible.
    hi[0] = 1000.; lo[1] = -100.; hi[1] = 100.
    hi[2:4] = 100.; at[2] = model.tank_tcg_m; at[3] = -model.tank_tcg_m
    hi[4:7] = 10.; lo[4:7] = 9.
    lo[7:] = 999999.; hi[7:] = -999999.
    rows = LinearConstraintSet(names, ac, at, const, lo, hi)
    monkeypatch.setattr(linear, 'linear_constraints', lambda *a, **kw: rows)
    monkeypatch.setattr(linear, 'build_linear_model', lambda *a, **kw: model)
    monkeypatch.setattr(operations, 'mass_vectors', lambda *a, **kw: (np.zeros(1), np.zeros(4)))
    arrival = NS(tank_masses_kg={'port': 0., 'starboard': 0., 'heelp': 5., 'heels': 5.})
    inst = NS(onboard=arrival, vessel=NS(hull=NS(lpp_m=100., max_draught_m=10., max_list_rad=.1),
        tank_pairs=[NS(role='anti_heeling', port_tank='heelp', starboard_tank='heels')]),
        port=NS(max_draught_m=10., water_density_kg_m3=1025.),
        model_copy=lambda **kwargs: NS(**{**vars(inst), **kwargs.get('update', {})}))
    return NS(model=model, rows=rows, instance=inst, ship=NS(covers_off=[]), full=full,
              catalogue=catalogue, linear=linear)


def test_gate_defaults_off_without_checker_every_lift_flag(context, monkeypatch):
    monkeypatch.delattr(context.full, 'IN_PORT_EVERY_LIFT_ENABLED')
    assert not in_port_screen_enabled()
    assert screen_in_port_prefix(context.instance, context.ship).status == 'undecided'


def test_activation_switch_off_never_builds_rows(context, monkeypatch):
    monkeypatch.setattr(context.catalogue, 'rule_freeze_v1_enabled', lambda: False)
    monkeypatch.setattr(context.linear, 'build_linear_model', lambda *a, **k: pytest.fail('legacy built D64'))
    assert not in_port_screen_enabled()
    assert screen_in_port_prefix(context.instance, context.ship).status == 'undecided'


def test_seagoing_lower_displacement_cargo_and_strength_rows_omitted(context):
    result = screen_in_port_prefix(context.instance, context.ship)
    assert result.status == 'feasible'


def test_heel_authority_fixed_total_and_other_pairs_symmetric(context):
    context.rows.const[2] = 151.
    # At most 50 of HEEL moment may be corrected; ordinary pair net moment is zero.
    assert screen_in_port_prefix(context.instance, context.ship).status == 'reject'


def test_heel_pair_can_use_any_split_at_fixed_total(context):
    context.rows.const[2] = 149.
    assert screen_in_port_prefix(context.instance, context.ship).status == 'feasible'


def test_partial_prefix_snapshot_manifest(context, monkeypatch):
    def build(instance):
        assert instance.load_list == [] and instance.discharge_list == []
        assert instance.onboard is context.ship
        return context.model
    monkeypatch.setattr(context.linear, 'build_linear_model', build)
    assert screen_in_port_prefix(context.instance, context.ship).status == 'feasible'


def test_gm_floor_is_point_fifteen_not_seagoing_floor(context):
    context.rows.const[0] = 20.
    context.rows.ub[0] = 0.
    assert screen_in_port_prefix(context.instance, context.ship).status == 'feasible'


def test_outside_qualified_displacement_is_undecided(context):
    object.__setattr__(context.model.band, 'displacement_min_kg', 150.)
    assert screen_in_port_prefix(context.instance, context.ship).status == 'undecided'


def test_open_hatch_defers_until_linear_model_supports_it(context):
    context.ship.covers_off = ['H1']
    assert screen_in_port_prefix(context.instance, context.ship).status == 'undecided'


def test_missing_qualification_defers(context, monkeypatch):
    def unqualified(*a, **kw):
        raise ValueError('necessary direction lacks independent qualification')
    monkeypatch.setattr(context.linear, 'linear_constraints', unqualified)
    assert screen_in_port_prefix(context.instance, context.ship).status == 'undecided'


def test_solver_failure_is_not_rejection(context, monkeypatch):
    import stowage.feasibility.in_port as module
    monkeypatch.setattr(module, 'linprog', lambda *a, **kw: NS(status=4))
    assert screen_in_port_prefix(context.instance, context.ship).status == 'undecided'


def test_solver_exception_is_undecided(context, monkeypatch):
    import stowage.feasibility.in_port as module
    def broken(*a, **kw):
        raise RuntimeError('solver interrupted')
    monkeypatch.setattr(module, 'linprog', broken)
    assert screen_in_port_prefix(context.instance, context.ship).status == 'undecided'


def test_dense_water_has_no_seagoing_displacement_maximum(context):
    context.instance.port.water_density_kg_m3 = 1040.
    assert screen_in_port_prefix(context.instance, context.ship).status == 'feasible'


def test_actual_draught_error_crossing_domain_edge_defers(context):
    context.model.errors = NS(draught_m=30., trim_m=0.)
    assert screen_in_port_prefix(context.instance, context.ship).status == 'undecided'


def test_unworked_tank_maximum_fsm_must_not_falsely_reject_gm(context):
    # 3.7 adds every tank maximum to its GM numerator. At empty/full unworked
    # fills the checker may charge none, and departure's worked set is unknown.
    context.model.tank_fsm_max_kgm[:] = [1000., 1000., 200., 200.]
    context.rows.const[0] = 2400.
    assert screen_in_port_prefix(context.instance, context.ship).status == 'feasible'


def test_unworked_tank_maximum_fsm_must_not_falsely_reject_list(context):
    context.model.tank_fsm_max_kgm[:] = [1000., 1000., 0., 0.]
    context.rows.const[2] = 251.
    assert screen_in_port_prefix(context.instance, context.ship).status == 'feasible'


def test_in_port_list_override_outside_boundary_qualification_defers(context):
    context.instance.rule_overrides = {'port.stability.cargo_operations': {'max_list_rad': .2}}
    assert screen_in_port_prefix(context.instance, context.ship).status == 'undecided'


def test_fsm_relaxation_with_real_37_constraint_assembly(context, monkeypatch):
    """Exercise the production 3.7 FSM algebra, mocking only qualification."""
    import stowage.linear.model as model_api
    import stowage.linear.margin_policy as policy
    model = context.model
    model.station_x_m = np.zeros(0)
    model.const_sf_n = model.const_bm_nm = np.zeros(0)
    model.sf_per_kg_cell = model.bm_per_kg_cell = np.zeros((0, 1))
    model.sf_per_kg_tank = model.bm_per_kg_tank = np.zeros((0, 4))
    model.sf_min_n = model.sf_max_n = model.bm_min_nm = model.bm_max_nm = np.zeros(0)
    model.const_moment_z_kgm = 980.
    model.const_moment_y_kgm = 60.
    model.tank_fsm_max_kgm[:] = [1000., 1000., 0., 0.]
    error_names = ('gm_m', 'trim_m', 'list_rad', 'draught_m', 'sf_n', 'bm_nm',
                   'draught_aft_m', 'draught_forward_m')
    reserves = {side: {key: np.zeros(0) if key in ('sf_n', 'bm_nm') else 0.
                       for key in error_names}
                for side in ('lower_unsafe', 'upper_unsafe')}
    reserves['list_moment_kgm'] = {'lower_unsafe': 0., 'upper_unsafe': 0.}
    monkeypatch.setattr(policy, 'default_reserves', lambda *a, **kw: reserves)
    monkeypatch.setattr(context.linear, 'linear_constraints', model_api.linear_constraints)
    assert screen_in_port_prefix(context.instance, context.ship).status == 'feasible'


def test_port_action_helper_checks_each_expanded_lift(context, monkeypatch):
    import stowage.feasibility.in_port as module
    from stowage.feasibility._port_screen import port_action_allowed
    import stowage.moves as moves
    seen = []
    def assess(instance, ship):
        seen.append(ship)
        return NS(status='reject' if ship == 'lift2' else 'feasible',
                  rule_id='port.stability.cargo_operations')
    monkeypatch.setattr(module, 'screen_in_port_prefix', assess)
    monkeypatch.setattr(module, 'InPortPrefixScreen', lambda instance, ship:
                        NS(evaluate=lambda candidate: assess(instance, candidate)))
    monkeypatch.setattr(moves, 'apply_move', lambda instance, world, move: NS(ship=move))
    ex = NS(where={}, can_expand=lambda *a, **kw: True, world_state=lambda: NS(ship='initial'),
            step=lambda action: ['lift1', 'lift2', 'lift3'])
    ex.copy = lambda: ex
    state = NS(ex=ex, rem=np.zeros(0, dtype=bool), sp={})
    tables = NS(box_ids=[], slot_ids=[], static=np.zeros((0, 0), dtype=bool))
    assert not port_action_allowed(context.instance, tables, state, Place('box', '0010102'))
    assert seen == ['lift1', 'lift2']


def test_port_action_helper_missing_gate_never_replays(context, monkeypatch):
    from stowage.feasibility._port_screen import port_action_allowed
    monkeypatch.delattr(context.full, 'IN_PORT_EVERY_LIFT_ENABLED')
    ex = NS(where={}, can_expand=lambda *a, **kw: True,
            world_state=lambda: pytest.fail('disabled D64 replayed prefix'))
    state = NS(ex=ex, rem=np.zeros(0, dtype=bool), sp={})
    tables = NS(box_ids=[], slot_ids=[], static=np.zeros((0, 0), dtype=bool))
    assert port_action_allowed(context.instance, tables, state, Place('box', '0010102'))


def _tank_metadata(context, *, kind='ballast', lightship=1000.):
    context.instance.vessel.hull.lightship_mass_kg = lightship
    context.instance.vessel.tanks = [NS(id=key, kind=kind, content_density_kg_m3=1000.)
                                     for key in context.model.tank_ids]
    context.model._reference_params = {'tanks': [
        {'id': key, 'geometry': {'shape': 'rectangular_prism', 'x_min_m': 0., 'x_max_m': 12.,
                               'y_min_m': 0., 'y_max_m': 1., 'z_min_m': 0., 'z_max_m': 1.}}
        for key in context.model.tank_ids]}


def test_mandatory_nonexempt_heel_maximum_fsm_is_retained(context):
    _tank_metadata(context)
    context.model.tank_fsm_max_kgm[:] = [0., 0., 1000., 1000.]
    context.rows.const[0] = 2000.
    assert screen_in_port_prefix(context.instance, context.ship).status == 'reject'


def test_mandatory_carried_consumable_maximum_fsm_is_retained(context):
    _tank_metadata(context, kind='fuel_oil')
    context.model.tank_fsm_max_kgm[:] = [1000., 1000., 0., 0.]
    context.rows.const[0] = 2000.
    assert screen_in_port_prefix(context.instance, context.ship).status == 'reject'


def test_missing_lightship_metadata_keeps_fsm_optimistic(context):
    context.instance.vessel.tanks = [NS(id=key, kind='ballast') for key in context.model.tank_ids]
    context.model.tank_fsm_max_kgm[:] = [0., 0., 1000., 1000.]
    context.rows.const[0] = 2000.
    assert screen_in_port_prefix(context.instance, context.ship).status == 'feasible'


def test_possibly_exempt_heel_tank_maximum_kept_optimistic(context):
    _tank_metadata(context)
    context.model.tank_fsm_max_kgm[:] = [0., 0., 5., 5.]
    context.rows.const[0] = 10.
    context.rows.ub[0] = -45.
    assert screen_in_port_prefix(context.instance, context.ship).status == 'feasible'


def test_mandatory_fsm_never_uses_linear_upward_guard(context):
    from stowage.feasibility.in_port import _mandatory_fsm
    _tank_metadata(context)
    context.model.tank_fsm_max_kgm[:] = [0., 0., 1001., 1001.]
    # The actual rectangular solid has length 12, width 1, density 1000:
    # each checker maximum is 12 * 1**3 / 12 * 1000 = 1000 kg m.
    assert _mandatory_fsm(context.model, context.instance) == pytest.approx(2000.)


def test_missing_mandatory_tank_geometry_keeps_fsm_optimistic(context):
    _tank_metadata(context)
    context.model._reference_params = {}
    context.model.tank_fsm_max_kgm[:] = [0., 0., 1000., 1000.]
    context.rows.const[0] = 2000.
    assert screen_in_port_prefix(context.instance, context.ship).status == 'feasible'


def test_cost_pruning_qualification_gate_defaults_off(context, monkeypatch):
    import stowage.feasibility.in_port as module
    monkeypatch.setattr(module, 'D64_COST_PRUNING_QUALIFIED', False, raising=False)
    monkeypatch.setattr(context.linear, 'build_linear_model',
                        lambda *a, **k: pytest.fail('unqualified D64 built model'))
    assert not in_port_screen_enabled()
    assert screen_in_port_prefix(context.instance, context.ship).status == 'undecided'


def test_prepared_prefix_builds_model_and_rows_once_for_candidate_deltas(context, monkeypatch):
    import stowage.feasibility.in_port as module
    import stowage.linear.operations as operations
    counts = {'model': 0, 'rows': 0}
    real_rows = module._rows
    def build(prefix):
        counts['model'] += 1
        return context.model
    def rows(*args):
        counts['rows'] += 1
        return real_rows(*args)
    monkeypatch.setattr(context.linear, 'build_linear_model', build)
    monkeypatch.setattr(module, '_rows', rows)
    monkeypatch.setattr(operations, 'mass_vectors',
                        lambda model, ship, *a, **kw: (np.array([ship.cargo]), np.zeros(4)))
    context.ship.cargo = 0.
    # Candidate cargo makes the qualified affine LIST row infeasible; the
    # original prefix and a smaller candidate remain feasible.
    context.rows.a_cell[2, 0] = 10.
    prepared = module.InPortPrefixScreen(context.instance, context.ship)
    assert prepared.evaluate(context.ship).status == 'feasible'
    assert prepared.evaluate(NS(covers_off=[], cargo=1.)).status == 'feasible'
    assert prepared.evaluate(NS(covers_off=[], cargo=20.)).status == 'reject'
    assert counts == {'model': 1, 'rows': 1}


def test_prepared_prefix_open_cover_never_builds_model(context, monkeypatch):
    import stowage.feasibility.in_port as module
    monkeypatch.setattr(context.linear, 'build_linear_model',
                        lambda *a, **k: pytest.fail('open cover built D64 rows'))
    context.ship.covers_off = ['H1']
    prepared = module.InPortPrefixScreen(context.instance, context.ship)
    assert prepared.evaluate(context.ship).status == 'undecided'


def test_prepared_prefix_candidate_manifest_validation_stays_fail_open(context, monkeypatch):
    import stowage.feasibility.in_port as module
    import stowage.linear.operations as operations
    prepared = module.InPortPrefixScreen(context.instance, context.ship)
    assert prepared.evaluate(context.ship).status == 'feasible'
    def invalid(*a, **kw):
        raise ValueError('departure manifest differs from model calibration')
    monkeypatch.setattr(operations, 'mass_vectors', invalid)
    verdict = prepared.evaluate(NS(covers_off=[]))
    assert verdict.status == 'undecided'
    assert 'manifest' in verdict.reason
