"""Necessary-condition failures and permissive edge cases on model fixtures."""
from stowage.generate.instances_screen import count_unresolved_provisions, screen_load_list
from stowage.model import DangerousGoods, OutOfGauge
from stowage.model.fixtures import build_instance


def dry(box, **updates):
    return box.model_copy(update={"dg": None, "reefer": False, "reefer_setpoint_c": None,
                                  "special_stowage": [], "oog": None, **updates})


def test_fixture_screen_does_not_claim_feasibility():
    i = build_instance()
    assert screen_load_list(i.vessel, i.containers, port=i.port) == []


def test_capacity_and_per_length():
    i = build_instance()
    box = dry(i.containers[1])
    reasons = screen_load_list(i.vessel, [box] * 17)
    assert "expert.physical.slot_occupancy" in reasons
    assert "expert.physical.size_fit" in reasons
    v = i.vessel.model_copy(deep=True)
    for c in v.cells:
        c.accepts_20 = False
    assert "expert.physical.size_fit" in screen_load_list(v, [box])


def test_45_foot_uses_two_cells_and_adjacent_pair_need_not_start_at_bay_one():
    i = build_instance()
    box = dry(i.containers[0], length_ft=45, iso_type="L5G1")
    assert "expert.physical.slot_occupancy" not in screen_load_list(i.vessel, [box]*8)
    v = i.vessel.model_copy(deep=True)
    # Existing 001/003 pairs renamed to 003/005 for this permissive geometry test.
    for c in v.cells:
        c.id = f"{int(c.id[:3])+2:03d}" + c.id[3:]
    for s in v.stacks:
        s.cell_ids = [f"{int(c[:3])+2:03d}" + c[3:] for c in s.cell_ids]
    assert "expert.physical.size_fit" not in screen_load_list(v, [box])


def test_reefers_require_eligible_plugs():
    i = build_instance()
    v = i.vessel.model_copy(deep=True)
    for c in v.cells:
        c.reefer_plug = False
    assert "expert.physical.reefer_plug" in screen_load_list(v, [i.containers[0]])


def test_local_and_aggregate_stack_mass():
    i = build_instance()
    v = i.vessel.model_copy(deep=True)
    for s in v.stacks:
        s.max_mass_kg = 1000
    assert "expert.physical.stack_weight" in screen_load_list(v, [dry(i.containers[1])])
    # The optional homogeneous limit can be higher than the general limit.
    for s in v.stacks:
        s.max_mass_20_kg = 60000
    assert "expert.physical.stack_weight" not in screen_load_list(v, [dry(i.containers[1])])


def test_special_regions_and_top_capacity():
    i = build_instance()
    v = i.vessel.model_copy(deep=True)
    for c in v.cells:
        if not next(s for s in v.stacks if s.id == c.stack_id).on_deck:
            c.accepts_20 = False
    box = dry(i.containers[1], special_stowage=["under_deck"])
    assert "expert.e8.under_deck" in screen_load_list(v, [box])
    top = dry(i.containers[1], special_stowage=["top_stowage"])
    assert "expert.e8.top_stowage" in screen_load_list(i.vessel, [top]*9)
    high = dry(i.containers[1], oog=OutOfGauge(over_height_m=10))
    assert "expert.e9.over_height" in screen_load_list(i.vessel, [high])


def test_dg_categories_doc_and_fail_closed_provisions():
    i = build_instance()
    for un, cls, category in (("0336", "1.4G", "02"), ("2915", "7", "A")):
        box = dry(i.containers[1], dg=DangerousGoods(un_number=un, imdg_class=cls,
                                                  stowage_category=category))
        assert "imdg.stowage.3_2" in screen_load_list(i.vessel, [box])
    v = i.vessel.model_copy(deep=True)
    for s in v.stacks:
        s.dg_classes_allowed = []
    assert "imdg.stowage.3_2" in screen_load_list(v, [i.containers[1]])
    assert "imdg.stowage.3_2" not in screen_load_list(i.vessel, [i.containers[1]])


def test_draught_and_displacement_include_tanks_fixed_loads_and_water_density():
    i = build_instance()
    p = i.port.model_copy(update={"max_draught_m": 4.0, "water_density_kg_m3": 1000.0})
    reasons = screen_load_list(i.vessel, [], port=p, tank_masses_kg={"WB-P": 6e6})
    assert "iscode.loading.displacement" in reasons
    assert "port.call.draft" in reasons
    assert "port.call.draft" not in screen_load_list(i.vessel, [], port=p)


def test_oog_overhang_requires_some_nonintersecting_pair_of_positions():
    i = build_instance()
    v = i.vessel.model_copy(deep=True)
    # Deck-only bookings and a single occupied height force same-level overlap.
    for s in v.stacks:
        s.max_height_m = 3.0
    a = dry(i.containers[1], special_stowage=["on_deck"], oog=OutOfGauge(
        over_width_port_m=100, over_width_starboard_m=100,
        over_length_fore_m=100, over_length_aft_m=100))
    b = dry(i.containers[1], special_stowage=["on_deck"])
    assert "expert.e9.over_width_length" in screen_load_list(v, [a, b])
    assert "expert.e9.over_width_length" not in screen_load_list(v, [a])
    assert "expert.e9.over_width_length" not in screen_load_list(i.vessel, [a, b])


def test_dg_rejections_do_not_invent_downstream_height_or_mass_failures():
    i = build_instance()
    v = i.vessel.model_copy(deep=True)
    for stack in v.stacks:
        stack.dg_classes_allowed = []
    assert screen_load_list(v, [i.containers[1]]) == ["imdg.stowage.3_2"]
    box = dry(i.containers[1], special_stowage=["top_stowage"],
              dg=DangerousGoods(un_number="0336", imdg_class="1.4G", stowage_category="02"))
    assert screen_load_list(v, [box]) == ["imdg.stowage.3_2"]
    assert count_unresolved_provisions([box, i.containers[1], i.containers[0]]) == 1
    assert count_unresolved_provisions([i.containers[1]]) == 0


def test_eligibility_cache_mass_ranks_preserve_exact_thresholds():
    i = build_instance()
    v = i.vessel.model_copy(deep=True)
    for stack in v.stacks:
        stack.max_mass_kg = 10000.
        stack.max_mass_20_kg = stack.max_mass_40_kg = None
    # Equal and just-below bounds must share eligibility; just-above must not.
    low = dry(i.containers[1], gross_mass_kg=9999.999999999998)
    equal = dry(low, gross_mass_kg=10000.)
    high = dry(low, gross_mass_kg=10000.000000000002)
    assert screen_load_list(v, [low, equal]) == []
    assert screen_load_list(v, [low, equal, high]) == ["expert.physical.stack_weight"]
    assert screen_load_list(v, [high, equal, low]) == ["expert.physical.stack_weight"]


def test_policy_cache_uses_declaration_and_preserves_mixed_dg(monkeypatch):
    from stowage.generate import instances_screen as module
    i = build_instance()
    original = module._dg_policy
    calls = []
    def record(box):
        calls.append(box.dg)
        return original(box)
    monkeypatch.setattr(module, "_dg_policy", record)
    valid = i.containers[1]
    invalid = dry(valid, dg=DangerousGoods(un_number="2915", imdg_class="7", stowage_category="A"))
    assert module.screen_load_list(i.vessel, [valid, valid, invalid, invalid]) == ["imdg.stowage.3_2"]
    assert len(calls) == 2


def test_call_local_geometry_caches_observe_mutation():
    i = build_instance()
    v = i.vessel.model_copy(deep=True)
    assert screen_load_list(v, [i.containers[0]]) == []
    for cell in v.cells:
        cell.reefer_plug = False
    assert "expert.physical.reefer_plug" in screen_load_list(v, [i.containers[0]])
