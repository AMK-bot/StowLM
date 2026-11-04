"""Background deck tiers change vertical moments while retaining ship loading.

GM is the height of the transverse metacentre above the centre of gravity:
raising cargo reduces GM (IMO 2008 IS Code, Part A 2.2). This screen is not
an intact-stability or free-surface certificate.
"""

from types import SimpleNamespace

import pytest

from stowage.conventions import CONTAINER_HEIGHT_STD_M
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_conditions import slice_condition
from stowage.vessel.fleet_gm import condition_deck_tiers
from stowage.vessel.fleet_slices import slice_preset


@pytest.mark.parametrize("blocked", [False, True])
def test_same_column_transfer_preserves_mass_and_horizontal_moments(monkeypatch, blocked):
    from stowage.vessel.hydrostatics import HullGeometry

    vessel = load_vessel("feeder_500", with_hull=False)
    cells = {cell.id: cell for cell in vessel.cells}
    decks = {(stack.bay, stack.row): stack for stack in vessel.stacks if stack.on_deck}
    hold = next(stack for stack in vessel.stacks if not stack.on_deck and len(stack.cell_ids) >= 3)
    deck = decks[hold.bay, hold.row]
    # Keep an existing bottom deck box and an intentionally inverted light
    # hold donor. D61 moves that 1.8 t mass to the top within its physical
    # column; it must not drain it or change either horizontal first moment.
    before = [(cid, 20000.0 if index else 1800.0) for index, cid in enumerate(hold.cell_ids)]
    before.append((deck.cell_ids[0], 9000.0))
    fake = SimpleNamespace(density_kg_m3=1025.0, _solve=lambda *args: SimpleNamespace(
        submerged=SimpleNamespace(vcb_m=5.0, bm_t_m=30.0),
        mean_draught_m=7.0, trim_m=0.0))
    monkeypatch.setattr(HullGeometry, "from_params", lambda params: fake)
    # A decision stack in another column of the same removable cover also
    # makes this background deck column unavailable.
    other = next(stack for stack in vessel.stacks
                 if stack.hatch_cover_id == deck.hatch_cover_id and stack.id not in {hold.id, deck.id})
    after, audit = condition_deck_tiers(
        vessel, load_ship_params("feeder_500"), before, stack_ids={other.id} if blocked else set(),
        expected_load_mass_kg=0.0, expected_load_cg_m=(50.0, 0.0, 10.0))
    old, new = dict(before), dict(after)
    # Sorting changes tier assignment, including when a hatch blocks any
    # transfer to deck. Conservation applies to both branches, without changing
    # its original precision or allowing mass to move between footprints.
    for axis in (None, "lcg_m", "tcg_m"):
        def moment(records):
            return sum(mass * (1.0 if axis is None else getattr(cells[cid], axis))
                       for cid, mass in records)
        assert moment(after) == pytest.approx(moment(before), rel=1e-12)
    for stack in (hold, deck):
        occupied_masses = [new[cid] for cid in stack.cell_ids if cid in new]
        assert all(upper <= lower for lower, upper in zip(occupied_masses, occupied_masses[1:]))
    assert new[hold.cell_ids[-1]] == 1800.0
    assert new[deck.cell_ids[0]] == 9000.0
    if blocked:
        assert new.keys() == old.keys()
        assert sorted(new[cid] for cid in hold.cell_ids) == sorted(old[cid] for cid in hold.cell_ids)
        assert audit["transferred_mass_kg"] == 0
        assert audit["added_deck_cells"] == 0
        assert not audit["target_reached"]
        vertical_change = sum((new[cid]-old[cid])*cells[cid].vcg_base_m for cid in old)
        assert vertical_change < 0
        assert audit["order_vertical_moment_change_kgm"] == pytest.approx(vertical_change)
        assert audit["estimated_solid_gm_after_m"] > audit["estimated_solid_gm_before_m"]
        return
    assert audit["transferred_mass_kg"] > 0
    assert audit["added_deck_cells"] > 0
    for cid in hold.cell_ids[:-1]:
        assert new[cid] >= 2200.0 - 1e-8
    assert sum(mass*cells[cid].vcg_base_m for cid, mass in after) > sum(
        mass*cells[cid].vcg_base_m for cid, mass in before)
    for cid in new.keys() - old.keys():
        assert cid in deck.cell_ids
        assert 0 < new[cid] <= 20000.0
    occupied = [cid for cid in deck.cell_ids if cid in new]
    assert len(occupied) >= 3
    assert occupied == deck.cell_ids[:len(occupied)]
    # A new tier may not outweigh its physical support below. Previously the
    # 9 t bottom box received 20 t boxes above it to force the GM target.
    ordered = [new.get(cid, 0.) for cid in deck.cell_ids]
    assert all(upper <= lower for lower, upper in zip(ordered, ordered[1:]))
    assert len(occupied)*CONTAINER_HEIGHT_STD_M <= deck.max_height_m
    assert sum(new[cid] for cid in occupied) <= (deck.max_mass_20_kg or deck.max_mass_kg)


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800"])
@pytest.mark.parametrize("preset", ["tiny", "small", "medium"])
def test_low_gm_background_remains_unchanged(name, preset):
    vessel = slice_preset(load_vessel(name, with_hull=False), preset)
    audit = slice_condition(vessel)["gm_screen"]
    assert audit["estimated_solid_gm_before_m"] <= 2.5
    assert audit["estimated_solid_gm_after_m"] == audit["estimated_solid_gm_before_m"]
    assert audit["transferred_mass_kg"] == 0
    assert audit["added_deck_cells"] == 0


@pytest.mark.parametrize("preset", ["tiny", "small", "medium"])
def test_1700_background_screen_reports_capacity_limited_gm_honestly(preset):
    vessel = slice_preset(load_vessel("feeder_1700", with_hull=False), preset)
    condition = slice_condition(vessel)
    audit = condition["gm_screen"]
    assert condition["achieved_design_band"]
    # Real support masses limit the upward transfer: the former exact 2 m
    # result placed heavier tiers on lighter ones. Do not invent unavailable
    # vertical moment or treat this synthetic screen as checker acceptance.
    assert audit["estimated_solid_gm_after_m"] > 2.0
    assert not audit["target_reached"]
    available = audit["available_vertical_moment_kgm"]
    delta_gm = audit["estimated_solid_gm_before_m"]-audit["estimated_solid_gm_after_m"]
    assert delta_gm*condition["expected_displacement_kg"] == pytest.approx(available)
    emitted = {row["cell_id"]: row["mass_kg"] for row in condition["background_cells"]}
    parent = load_vessel("feeder_1700", with_hull=False)
    for stack in parent.stacks:
        ordered = [emitted.get(cid, 0.) for cid in stack.cell_ids]
        assert all(upper <= lower+1e-7 for lower, upper in zip(ordered, ordered[1:]))
    assert audit["transferred_mass_kg"] > 0
    assert audit["added_deck_cells"] > 0
    assert "APPROXIMATION:" in audit["approximation"]


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800", "feeder_1700"])
def test_background_stack_sort_preserves_horizontal_footprints(name):
    """Tier sorting is valid only within one real longitudinal/transverse column."""
    vessel = load_vessel(name, with_hull=False)
    cells = {cell.id: cell for cell in vessel.cells}
    for stack in vessel.stacks:
        assert len({(cells[cid].lcg_m, cells[cid].tcg_m)
                    for cid in stack.cell_ids}) <= 1, stack.id
