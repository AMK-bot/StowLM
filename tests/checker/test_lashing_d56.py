"""D56 lashing validity is bounded by declared design GM, never a hull bound."""
import math
from types import SimpleNamespace

import pytest

from stowage.checker import lashing as l
from stowage.model import Placement, ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_vessel


def rated_stack():
    instance = build_instance()
    instance.vessel.fixed_loads = []
    physical_vessel = load_vessel('feeder_500')
    instance.vessel.hull = physical_vessel.hull
    instance.vessel.tanks = physical_vessel.tanks
    stack = next(s for s in instance.vessel.stacks if s.id == '00101H')
    boxes = [c.model_copy(update=dict(length_ft=20, gross_mass_kg=30000.,
                                     height_m=3., oog=None, dg=None))
             for c in instance.containers[1:3]]
    instance.containers = boxes
    state = ShipState(vessel_id=instance.vessel.id,
        placements=[Placement(slot=cid, container_id=c.id)
                    for cid, c in zip(stack.cell_ids, boxes)])
    return instance, state, stack


def condition(monkeypatch, instance, gm):
    hull = instance.vessel.hull
    monkeypatch.setattr('stowage.checker.visibility._loading_condition', lambda *_: SimpleNamespace(
        mass_kg=9600000., gm_m=gm, t_aft_m=3., t_fwd_m=3.,
        geometry=SimpleNamespace(submerged=lambda *_: SimpleNamespace(
            volume_m3=.65*hull.lpp_m*hull.beam_m*hull.scantling_draught_m))))


def test_design_uses_declared_gm_and_rated_stack_has_ten_percent_margin(monkeypatch):
    instance, state, _ = rated_stack()
    cap = instance.vessel.hull.max_gm_lashing_m
    design = l.derive_cell_guide_design(instance.vessel)
    assert design.gm_m == cap
    condition(monkeypatch, instance, cap)
    report = l.check_lashing(instance, state, call_index=0)
    assert report.ran, report.error
    assert report.metrics['max_cell_guide_ratio'] <= 1 / 1.1 + 1e-10
    assert report.metrics['max_cell_guide_ratio'] > 0


def test_declared_gm_changes_design_content_deterministically():
    instance, _, _ = rated_stack()
    first = l.derive_cell_guide_design(instance.vessel)
    changed = instance.vessel.model_copy(deep=True)
    changed.hull.max_gm_lashing_m *= 2
    second = l.derive_cell_guide_design(changed)
    assert second.gm_m == changed.hull.max_gm_lashing_m
    assert second.section_modulus_m3 > first.section_modulus_m3 or second.area_m2 > first.area_m2
    assert second == l.derive_cell_guide_design(changed.model_copy(deep=True))
    assert second.rated_ratio <= 1 / 1.1 + 1e-10


def test_gm_above_design_fails_closed_before_motion_calculation(monkeypatch):
    instance, state, _ = rated_stack()
    cap = instance.vessel.hull.max_gm_lashing_m
    actual = math.nextafter(cap, math.inf)
    condition(monkeypatch, instance, actual)
    monkeypatch.setattr(l, '_motions', lambda *_: pytest.fail('invalid GM reached IRS motion calculation'))
    report = l.check_lashing(instance, state, call_index=4)
    assert not report.ran
    assert any('GM above lashing design GM' in v.message and v.severity == 'hard'
               and v.rule_id == 'iacs.member_irs.container_securing'
               and v.measured == actual and v.limit == cap and v.unit == 'm'
               and v.call_index == 4 for v in report.violations)


@pytest.mark.parametrize('missing', ['value', 'provenance'])
def test_missing_design_gm_or_evidence_fails_closed(monkeypatch, missing):
    instance, state, _ = rated_stack()
    key = 'max_gm_lashing_m' + ('_provenance' if missing == 'provenance' else '')
    setattr(instance.vessel.hull, key, None)
    condition(monkeypatch, instance, 1.)
    with pytest.raises(ValueError, match='max_gm_lashing_m'):
        l.derive_cell_guide_design(instance.vessel)
    report = l.check_lashing(instance, state, call_index=0)
    assert not report.ran
    assert report.violations[0].rule_id == 'checker.missing_vessel_data'


def test_missing_design_gm_fails_closed_even_without_hold_stacks(monkeypatch):
    instance, _, _ = rated_stack()
    instance.vessel.stacks = [s for s in instance.vessel.stacks if s.on_deck]
    instance.vessel.hull.max_gm_lashing_m = None
    condition(monkeypatch, instance, 1.)
    with pytest.raises(ValueError, match='max_gm_lashing_m'):
        l.derive_cell_guide_design(instance.vessel)
    state = ShipState(vessel_id=instance.vessel.id)
    report = l.check_lashing(instance, state, call_index=0)
    assert not report.ran
    assert report.violations[0].rule_id == 'checker.missing_vessel_data'
