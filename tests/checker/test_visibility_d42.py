"""D42 box evidence, mixed support geometry and mass accounting regressions."""
from types import SimpleNamespace

import pytest

from stowage.checker import visibility
from stowage.model import FixedCargoBox, FixedLoad, Placement
from stowage.model.fixtures import build_instance
from tests.checker.test_visibility import condition, deck_box


def background(instance, *, height=14., tier=82):
    """Move a complete deck stack to fixed geometry, preserving its upper cells."""
    deck_box(instance, height=height)
    vessel = instance.vessel
    stack = next(s for s in vessel.stacks if s.id == '00101D')
    vessel.fixed_load_stacks = [stack]
    vessel.stacks = [s for s in vessel.stacks if s.id != stack.id]
    vessel.fixed_load_cells = [c for c in vessel.cells if c.stack_id == stack.id]
    vessel.cells = [c for c in vessel.cells if c.stack_id != stack.id]
    # Use a fixed pedestal to isolate cargo geometry from cover geometry.
    stack.hatch_cover_id = None
    for cover in vessel.hatch_covers:
        cover.deck_stack_ids = [sid for sid in cover.deck_stack_ids if sid != stack.id]
    box = FixedCargoBox(cell_ids=[f'00101{tier}'],length_ft=20,height_m=height,
        gross_mass_kg=12000.,x_m=60.,y_m=0.,z_m=stack.base_z_m+height/2,
        stack_id=stack.id,tier=tier)
    vessel.fixed_loads = [FixedLoad(kind='cargo',cargo_boxes=[box],mass_kg=box.gross_mass_kg,
        x_m=box.x_m,y_m=box.y_m,z_m=box.z_m)]
    instance.onboard.placements = []
    return box


def test_background_box_blocks_visibility_without_legacy_silhouette(monkeypatch):
    condition(monkeypatch)
    instance = build_instance()
    background(instance)
    report = visibility.check_visibility(instance,instance.onboard,call_index=4)
    assert report.ran
    assert report.metrics['blind_distance_m'] > report.metrics['limit_m']
    assert any(v.rule_id == 'solas.v22.visibility' and v.call_index == 4 for v in report.violations)


@pytest.mark.parametrize('kind',['unknown','other'])
def test_positive_unknown_kind_fails_closed_even_with_envelope(monkeypatch,kind):
    from tests.checker.test_visibility import background_load
    condition(monkeypatch)
    instance = build_instance()
    instance.vessel.fixed_loads = [background_load().model_copy(update={'kind':kind})]
    report = visibility.check_visibility(instance,instance.onboard,call_index=0)
    assert not report.ran
    assert report.violations[0].rule_id == 'checker.missing_vessel_data'


def test_mixed_stack_uses_background_actual_height_for_planned_floor():
    instance = build_instance()
    box = background(instance,height=2.896)
    planned = instance.containers[2]
    planned.oog = None
    instance.onboard.placements = [Placement(slot='0010184',container_id=planned.id)]
    physical = list(visibility._cargo_geometry(instance,instance.onboard))
    assert len(physical) == 1  # FixedLoad aggregate already contains background mass.
    assert physical[0][-2] == pytest.approx(9.+box.height_m)


def test_mutated_background_box_aggregate_is_revalidated(monkeypatch):
    condition(monkeypatch)
    instance = build_instance()
    box = background(instance)
    instance.vessel.fixed_loads[0].cargo_boxes[0].gross_mass_kg += 1.
    report = visibility.check_visibility(instance,instance.onboard,call_index=0)
    assert not report.ran
    assert 'sum must equal' in report.error


def test_background_box_geometry_replaces_aggregate_silhouette(monkeypatch):
    from stowage.model import FixedLoadSilhouette
    condition(monkeypatch)
    instance = build_instance()
    background(instance,height=2.591)
    # The legacy aggregate envelope may span multiple stack blocks. It must
    # neither replace box evidence nor create an invented solid between boxes.
    instance.vessel.fixed_loads[0].silhouette = FixedLoadSilhouette(
        x_min_m=10.,x_max_m=100.,y_min_m=-20.,y_max_m=20.,z_min_m=9.,z_max_m=100.)
    report = visibility.check_visibility(instance,instance.onboard,call_index=0)
    assert report.ran and not report.violations


@pytest.mark.parametrize('damage',['height','tier','cg','cell_fit','reciprocal','overlap'])
def test_inconsistent_mutated_background_evidence_fails_closed(monkeypatch,damage):
    condition(monkeypatch)
    instance = build_instance()
    background(instance)
    load = instance.vessel.fixed_loads[0]
    box = load.cargo_boxes[0]
    if damage == 'height':
        box.height_m = -1.
    elif damage == 'tier':
        box.tier = 84
    elif damage == 'cg':
        box.z_m = load.z_m = 100.
    elif damage == 'cell_fit':
        instance.vessel.fixed_load_cells[0].accepts_20 = False
    elif damage == 'reciprocal':
        instance.vessel.fixed_load_stacks[0].cell_ids.remove(box.cell_ids[0])
    else:
        instance.onboard.placements = [Placement(slot=box.cell_ids[0],container_id=instance.containers[1].id)]
    report = visibility.check_visibility(instance,instance.onboard,call_index=0)
    assert not report.ran
    assert report.violations[0].rule_id == 'checker.missing_vessel_data'


def test_background_and_mixed_planned_equilibrium_masses_count_exactly_once(monkeypatch):
    instance = build_instance()
    background_box = background(instance,height=2.896)
    vessel, ship = instance.vessel, instance.onboard
    vessel.tanks = []
    ship.tank_masses_kg = {}
    hull = vessel.hull
    hull.hull_offsets_ref = 'params/fleet/feeder_500.toml#hull_form'
    solid = SimpleNamespace(lpp_m=hull.lpp_m,beam_m=hull.beam_m,depth_m=hull.depth_m)
    solid.equilibrium = lambda *args: SimpleNamespace(t_aft_m=5.,t_fwd_m=4.,
        heel_rad=0.,trim_m=1.,submerged=SimpleNamespace(vcb_m=2.,bm_t_m=6.))
    monkeypatch.setattr(visibility,'_geometry',lambda _: (solid,{}))
    base = visibility._loading_condition(instance,ship)
    assert base.mass_kg == hull.lightship_mass_kg+background_box.gross_mass_kg
    assert base.kg_m == pytest.approx((hull.lightship_mass_kg*hull.lightship_vcg_m+
                                     background_box.gross_mass_kg*background_box.z_m)/base.mass_kg)
    planned = instance.containers[2]
    planned.oog = None
    ship.placements = [Placement(slot='0010184',container_id=planned.id)]
    mixed = visibility._loading_condition(instance,ship)
    assert mixed.mass_kg == base.mass_kg+planned.gross_mass_kg
    planned_cg = 9.+background_box.height_m+planned.height_m/2
    assert mixed.kg_m == pytest.approx((base.mass_kg*base.kg_m+
                                      planned.gross_mass_kg*planned_cg)/mixed.mass_kg)


@pytest.mark.parametrize('damage',[False,True])
def test_raw_fixed_catalog_is_normalized_or_fails_closed(monkeypatch,damage):
    condition(monkeypatch)
    instance = build_instance()
    background(instance)
    vessel = instance.vessel
    vessel.fixed_load_cells = [cell.model_dump() for cell in vessel.fixed_load_cells]
    vessel.fixed_load_stacks = [stack.model_dump() for stack in vessel.fixed_load_stacks]
    if damage:
        vessel.fixed_load_cells[0]['stack_id'] = '00301D'
    report = visibility.check_visibility(instance,instance.onboard,call_index=0)
    assert report.ran == (not damage)
    if damage:
        assert report.violations[0].rule_id == 'checker.missing_vessel_data'
