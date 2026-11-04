"""True-arrival target accounting on declared development maps only."""
from pathlib import Path
from types import SimpleNamespace
import json
import math
import numpy as np
import pytest
from stowage.io import load_instance
from targets import Targets, derive, level_draught

HERE = Path(__file__).resolve().parent


def test_settings_total_targets_subtract_background_once():
    target = Targets(100., (6000., 10., 500.), 20., 120., 1.2, .2, 5., .65, 5.5, 61., 7., {})
    result = target.settings(gm=1.5, shift_m=2., lcg_halfwidth_m=.4, tcg_halfwidth_m=.03)
    assert result['lcg'] == (62.6, 63.4)
    assert result['background_mass_t'] == 100.
    assert result['background_moments_tm'] == (6000., 10., 500.)
    assert result['vertical_max_tm'] == pytest.approx(120*(7.-.2-1.5))
    assert result['tcg'] == .03


@pytest.mark.parametrize('size,seed', [('small', 331401024), ('medium', 331401048)])
def test_true_arrival_accounting_and_loaded_hydro_target(size, seed):
    instance = load_instance(HERE/'cohort'/f'train.layover.{size}.o30.s{seed}.json')
    target = derive(instance)
    d = target.diagnostics
    assert d['original_arrival_mass_kg'] == pytest.approx(d['background_mass_kg']+d['removed_arrival_cargo_mass_kg'])
    arrival_moments = np.array(d['original_arrival_cg_m'])*d['original_arrival_mass_kg']
    assert np.array(d['background_moments_kgm'])+d['removed_arrival_cargo_moments_kgm'] == pytest.approx(arrival_moments)
    expected_ids = ({p.container_id for p in instance.onboard.placements}|set(instance.load_list))-set(instance.discharge_list)
    assert d['departure_box_count'] == len(expected_ids)
    boxes = {b.id: b for b in instance.containers}
    assert d['departure_cargo_mass_kg'] == pytest.approx(sum(boxes[b].gross_mass_kg for b in expected_ids))
    assert target.arrival_gm_m == d['original_arrival_gm_m']
    assert target.arrival_fsc_m >= -1e-9
    assert 0. < target.arrival_cb < 1.
    from stowage.checker.visibility import _loading_condition
    native = _loading_condition(instance, instance.onboard)
    hull = instance.vessel.hull
    expected_cb = native.geometry.submerged(hull.scantling_draught_m,hull.scantling_draught_m).volume_m3/(hull.lpp_m*hull.beam_m*hull.scantling_draught_m)
    assert target.arrival_cb == pytest.approx(expected_cb,rel=1e-12)
    assert d['securing_cb_basis'] == 'native scantling hull volume'
    assert 0. < target.departure_draught_m < instance.vessel.hull.depth_m
    assert math.isfinite(target.settings()['vertical_max_tm'])
    target.reference(instance.vessel.hull)
    # JSON telemetry has no object handles and no non-finite sentinel values.
    json.dumps(d, allow_nan=False)


def test_level_inverse_respects_density_and_work_count():
    class Geometry:
        depth_m = 10.
        calls = 0
        def submerged(self, aft, fwd, heel, density):
            self.calls += 1
            assert aft == fwd and heel == 0.
            return SimpleNamespace(displacement_kg=100.*aft*density)
    g = Geometry()
    draft, point = level_draught(g, 205000., 1025., iterations=20)
    assert draft == pytest.approx(2., abs=1e-5)
    assert g.calls == 22
    assert point.displacement_kg == pytest.approx(205000., abs=1.)
    with pytest.raises(ValueError, match='exceeds'):
        level_draught(g, 2000000., 1025.)


def test_in_invocation_recipe_refresh_uses_departure_cargo_and_marks_provenance(monkeypatch):
    import targets
    import stowage.checker.full as full
    ship = SimpleNamespace(placements=[SimpleNamespace(container_id='retained'),
                                       SimpleNamespace(container_id='new')])
    monkeypatch.setattr(full, 'departure_condition', lambda instance, plan: ship)
    calls = []
    expected = SimpleNamespace(diagnostics={})
    def fake_derive(instance, **kwargs):
        calls.append((instance, kwargs))
        return expected
    monkeypatch.setattr(targets, 'derive', fake_derive)
    class Instance:
        def model_copy(self, *, update):
            return SimpleNamespace(**update)
    assert targets.derive_from_plan(Instance(), object()) is expected
    working, kwargs = calls[0]
    assert working.onboard is ship
    assert working.load_list == working.discharge_list == []
    assert kwargs['departure_box_ids'] == ['retained', 'new']
    assert expected.diagnostics['target_refresh_only'] is True
