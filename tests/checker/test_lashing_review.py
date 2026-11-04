"""Additional review cases for unsupported securing and physical box counting."""
from types import SimpleNamespace

import pytest

from stowage.checker import lashing as l
from stowage.checker.lashing import check_lashing
from stowage.model import Placement
from stowage.model.fixtures import build_instance
from stowage.model import VesselFieldProvenance


@pytest.fixture(autouse=True)
def explicit_unit_test_guide_design(monkeypatch):
    # These fixtures isolate IRS force/stress arithmetic and background evidence;
    # their placeholder hull has no declared lashing manual. Give the mocked
    # design an explicit synthetic cap; D56's real cap has dedicated tests.
    original_builder = build_instance
    def unit_instance(*args, **kwargs):
        instance = original_builder(*args, **kwargs)
        instance.vessel.hull.max_gm_lashing_m = 1e9
        instance.vessel.hull.max_gm_lashing_m_provenance = VesselFieldProvenance(
            assumption='Unit-test cap for fixed guide/stress arithmetic, not a vessel design')
        return instance
    monkeypatch.setitem(globals(), 'build_instance', unit_instance)
    design = l.CellGuideDesign(l.CELL_GUIDE_SECTION_MODULUS_M3,
        l.CELL_GUIDE_AREA_M2, 1e9, '00101H', 0., 0., 2)
    monkeypatch.setattr(l, 'derive_cell_guide_design', lambda _: design)



def _instance(**kwargs):
    # Isolate explicit-box securing calculations from the builder's unmodelled
    # background cargo. Dedicated background tests require that cargo to fail.
    instance = build_instance(**kwargs)
    instance.vessel.fixed_loads = []
    return instance


def test_modest_oog_uses_synthetic_ratings(monkeypatch):
    monkeypatch.setattr('stowage.checker.visibility._loading_condition', lambda *_: SimpleNamespace(
        mass_kg=9600000., gm_m=1., t_aft_m=6., t_fwd_m=6.,
        geometry=SimpleNamespace(submerged=lambda *_: SimpleNamespace(volume_m3=.65*120*20*7))))
    instance = _instance()
    box = instance.containers[3]  # builder's explicitly over-height box
    instance.onboard.placements = [Placement(slot='0010182', container_id=box.id)]
    report = check_lashing(instance, instance.onboard, call_index=0)
    assert report.ran
    assert not report.violations
    assert report.metrics['physical_box_count'] == 1


def test_forty_foot_stack_counts_physical_box_once(monkeypatch):
    monkeypatch.setattr('stowage.checker.visibility._loading_condition', lambda *_: SimpleNamespace(
        mass_kg=9600000., gm_m=1., t_aft_m=6., t_fwd_m=6.,
        geometry=SimpleNamespace(submerged=lambda *_: SimpleNamespace(volume_m3=.65*120*20*7))))
    instance = _instance()
    box = instance.containers[0]
    instance.onboard.placements = [Placement(slot='0020182', container_id=box.id)]
    report = check_lashing(instance, instance.onboard, call_index=0)
    assert report.ran and not report.violations
    # Retaining only one physical stack must preserve the complete forty-foot
    # box force (not half mass); the partner is deduplicated in the full vessel.
    instance.vessel.stacks = [s for s in instance.vessel.stacks if s.id == '00101D']
    single = check_lashing(instance, instance.onboard, call_index=0)
    assert report.metrics == single.metrics
