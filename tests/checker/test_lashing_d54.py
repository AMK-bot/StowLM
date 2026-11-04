"""D54 guide sizing comes from the rated vessel, independently of actual cargo."""
from types import SimpleNamespace

import pytest

from stowage.checker import lashing as l
from stowage.model import Placement, ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_vessel


def rated_stack():
    instance = build_instance()
    instance.vessel.fixed_loads = []
    # Reuse the checksum-pinned synthetic vessel's declared design GM;
    # the generic geometry fixture intentionally has no lashing manual.
    physical_vessel = load_vessel('feeder_500')
    instance.vessel.hull = physical_vessel.hull
    instance.vessel.tanks = physical_vessel.tanks
    stack = next(s for s in instance.vessel.stacks if s.id == '00101H')
    # Homogeneous maximum tier count and height: two 3 m boxes fill the 6 m
    # rating. Each of the physical 20-foot support cells takes the full mass.
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


def test_rated_maximum_height_tiers_pass_guide_with_ten_percent_margin(monkeypatch):
    instance, state, _ = rated_stack()
    # Exercise the declared maximum GM used by D56's guide design.
    gm = l.derive_cell_guide_design(instance.vessel).gm_m
    condition(monkeypatch, instance, gm)
    report = l.check_lashing(instance, state, call_index=0)
    assert report.ran, report.error
    assert report.metrics['max_cell_guide_ratio'] <= 1/1.1 + 1e-10


def test_overrated_stack_still_fails_even_inside_guide_design_margin(monkeypatch):
    instance, state, stack = rated_stack()
    gm = l.derive_cell_guide_design(instance.vessel).gm_m
    condition(monkeypatch, instance, gm)
    for box in instance.containers:
        box.gross_mass_kg = 30500.  # Per-box IRS table rating; stack is 1 t over.
    report = l.check_lashing(instance, state, call_index=7)
    assert report.ran, report.error
    assert report.metrics['max_cell_guide_ratio'] < 1.
    assert any(v.measured == 61000. and v.limit == stack.max_mass_kg
               and v.unit == 'kg' and v.call_index == 7 for v in report.violations)


def test_guide_derivation_is_deterministic_and_independent_of_actual_load():
    instance, _, _ = rated_stack()
    design = l.derive_cell_guide_design(instance.vessel)
    assert design == l.derive_cell_guide_design(instance.vessel.model_copy(deep=True))
    reordered = instance.vessel.model_copy(deep=True)
    reordered.stacks.reverse()
    reordered.cells.reverse()
    assert design == l.derive_cell_guide_design(reordered)
    assert design.section_modulus_m3 >= l.CELL_GUIDE_SECTION_MODULUS_M3
    assert design.area_m2 >= l.CELL_GUIDE_AREA_M2
    assert design.rated_ratio <= 1/1.1 + 1e-10
    assert design.governing_stack_id in {s.id for s in instance.vessel.stacks if not s.on_deck}
    # Load content is deliberately irrelevant to scantlings, including a
    # background aggregate which has no securing evidence of its own.
    instance.vessel.fixed_loads = build_instance().vessel.fixed_loads
    assert design == l.derive_cell_guide_design(instance.vessel)


def test_guide_derivation_uses_physical_content_not_mutable_vessel_id():
    instance, _, _ = rated_stack()
    first = l.derive_cell_guide_design(instance.vessel)
    changed = instance.vessel.model_copy(deep=True)
    for stack in changed.stacks:
        if not stack.on_deck:
            stack.max_mass_kg *= 2
    second = l.derive_cell_guide_design(changed)
    assert second.section_modulus_m3 > first.section_modulus_m3 or second.area_m2 > first.area_m2
    assert second.rated_ratio <= 1/1.1 + 1e-10


def test_guide_design_is_identical_when_full_support_catalog_moves_to_background():
    instance, _, _ = rated_stack()
    parent = instance.vessel
    sliced = parent.model_copy(deep=True)
    sliced.id = parent.id + '.slice'
    sliced.slice_of = parent.id
    sliced.fixed_load_cells, sliced.cells = sliced.cells, []
    sliced.fixed_load_stacks, sliced.stacks = sliced.stacks, []
    sliced.fixed_load_hatch_covers, sliced.hatch_covers = sliced.hatch_covers, []
    assert l.derive_cell_guide_design(parent) == l.derive_cell_guide_design(sliced)


def test_guide_derivation_uses_rated_height_not_nominal_cell_floor_pitch():
    instance, _, _ = rated_stack()
    first = l.derive_cell_guide_design(instance.vessel)
    taller = instance.vessel.model_copy(deep=True)
    # The two allowed tiers remain fixed. Actual homogeneous box heights must
    # fill this new rating; nominal cell.vcg_base_m still assumes 2.591 m boxes.
    for stack in taller.stacks:
        if not stack.on_deck:
            stack.max_height_m *= 2
    second = l.derive_cell_guide_design(taller)
    assert second.section_modulus_m3 > first.section_modulus_m3 or second.area_m2 > first.area_m2
    assert second.rated_ratio <= 1/1.1 + 1e-10


def test_declared_design_gm_does_not_require_attainable_envelope_bound(monkeypatch):
    instance, state, _ = rated_stack()
    instance.vessel.hull.min_trim_m = None
    instance.vessel.hull.max_trim_m = None
    cap = instance.vessel.hull.max_gm_lashing_m
    condition(monkeypatch, instance, cap)
    design = l.derive_cell_guide_design(instance.vessel)
    assert design.gm_m == cap
    report = l.check_lashing(instance, state, call_index=0)
    assert report.ran, report.error
    assert report.metrics['max_cell_guide_ratio'] <= 1/1.1 + 1e-10
