"""The published CC0 vessel profiles are parsed as data, never as executable code."""
from pathlib import Path

import pytest

from stowage.conventions import parse_slot
from stowage.model import Vessel
from stowage.vessel.larsen_pacino import load_larsen_pacino


RAW = Path(__file__).resolve().parents[2] / "data/raw/larsen-pacino/vessel_data"


@pytest.mark.parametrize("size,teu,plugs", [("S", 7032, 770), ("M", 10264, 951), ("L", 15372, 1144)])
def test_published_vessel_profiles(size, teu, plugs):
    vessel = load_larsen_pacino(RAW / f"vessel_{size}.txt")
    assert vessel.nominal_teu == len(vessel.cells) == teu
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel
    assert all(parse_slot(c.id).is_20ft_bay for c in vessel.cells)
    assert sum(c.reefer_plug for c in vessel.cells) == plugs
    assert all(not c.accepts_45 for c in vessel.cells)
    assert all(s.hatch_cover_id for s in vessel.stacks)
    stack_ids = {s.id for s in vessel.stacks}
    assert all(set(h.hold_stack_ids + h.deck_stack_ids) <= stack_ids for h in vessel.hatch_covers)
    assert vessel.hull is None and vessel.bridge is None and not vessel.tanks
    assert any(s.startswith("sha256:") for s in vessel.sources)


def test_published_values_and_determinism():
    path = RAW / "vessel_S.txt"
    vessel = load_larsen_pacino(path)
    assert vessel.model_dump_json() == load_larsen_pacino(path).model_dump_json()
    # Bay 1, source stack 4 (TCG -8.505), above deck: five tiers,
    # maxHeight 13.050, maxWeight20 67.500 t, maxWeight40 100.800 t.
    stack = next(s for s in vessel.stacks if s.bay == 7 and s.on_deck and
                 next(c for c in vessel.cells if c.id == s.cell_ids[0]).tcg_m == -8.505)
    assert len(stack.cell_ids) == 5
    assert stack.max_height_m == 13.05
    assert stack.max_mass_20_kg == 67500
    assert stack.max_mass_40_kg == 50400
    assert stack.max_mass_kg == 50400
    assert stack.base_z_m == 26.1


@pytest.mark.parametrize("text", ["__import__('os').system('false')", "# Ship: bays stacks tiers tcgTollerance\n21 nan 18 0.1", "# Unknown: x\n1"])
def test_rejects_untrusted_non_numeric_or_unknown_records(tmp_path, text):
    path = tmp_path / "input.txt"
    path.write_text(text)
    with pytest.raises(ValueError, match="Larsen"):
        load_larsen_pacino(path)


def test_rejects_truncated_profile(tmp_path):
    path = tmp_path / "truncated.txt"
    path.write_text((RAW / "vessel_S.txt").read_text().split("## Bay:")[0])
    with pytest.raises(ValueError, match="incomplete"):
        load_larsen_pacino(path)


def test_40foot_cells_cannot_cross_source_bays():
    vessel = load_larsen_pacino(RAW / "vessel_S.txt")
    cells = {(a.bay, a.row, a.tier) for c in vessel.cells for a in [parse_slot(c.id)]}
    for bay, row, tier in cells:
        assert bay % 6 in (1, 3)
        if (bay + 2, row, tier) in cells:
            assert (bay - 1) // 6 == (bay + 1) // 6


def test_nested_imported_slice_preserves_parent_background_after_round_trip():
    from stowage.vessel.fleet_slices import recover_parent, slice_vessel

    parent = load_larsen_pacino(RAW / "vessel_S.txt")
    direct = slice_vessel(parent, [7], name="slice")
    intermediate = slice_vessel(parent, [7, 9], name="intermediate")
    intermediate = Vessel.model_validate_json(intermediate.model_dump_json())
    assert recover_parent(intermediate) == parent
    nested = slice_vessel(intermediate, [7], name="slice")
    assert nested.fixed_loads == direct.fixed_loads
    assert sum(point.mass_kg for point in nested.fixed_loads) > 100_000_000
    assert nested.slice_of == direct.slice_of == parent.id


@pytest.mark.parametrize("remove_snapshot", [False, True], ids=["missing-condition", "missing-both"])
@pytest.mark.parametrize("operation", ["slice", "recover", "recondition", "preset"])
def test_imported_slice_rejects_missing_parent_evidence(remove_snapshot, operation):
    from stowage.vessel.fleet_slices import (
        recondition_slice, recover_parent, slice_preset, slice_vessel,
    )

    parent = load_larsen_pacino(RAW / "vessel_S.txt")
    intermediate = slice_vessel(parent, [7, 9], name="intermediate")
    intermediate = Vessel.model_validate_json(intermediate.model_dump_json())
    intermediate.sources = [source for source in intermediate.sources
                            if not source.startswith("stowage.slice_condition:")
                            and not (remove_snapshot and source.startswith("stowage.slice_parent:"))]
    assert intermediate.slice_of == parent.id
    assert len(intermediate.cells) < len(parent.cells)
    with pytest.raises(ValueError, match="(condition metadata|parent geometry).*reload the full vessel"):
        if operation == "slice":
            slice_vessel(intermediate, [7], name="slice")
        elif operation == "recover":
            recover_parent(intermediate)
        elif operation == "recondition":
            recondition_slice(intermediate, expected_load_mass_kg=50_000)
        else:
            slice_preset(intermediate, "small")
