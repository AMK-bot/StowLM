"""SW1/SW2 are placement checks, not shipment-wide protection declarations."""
import pytest

from stowage.generate.cargo_shipments import shipment_requirements
from stowage.rules.imdg.dgl import entries_for_un


@pytest.mark.parametrize("un,legacy_field", [(12, "protected_from_heat"), (336, "protected_from_heat"),
    (1978, "clear_living_quarters"), (1017, "clear_living_quarters"),
    (1361, "protected_from_heat"), (1748, "protected_from_heat"),
    (3109, "protected_from_heat"), (2810, "clear_living_quarters"), (3373, "clear_living_quarters")])
def test_shipment_requirements_placement_derivation_removes_only_legacy_scalar(un, legacy_field):
    for entry in entries_for_un(un):
        legacy = shipment_requirements(entry)
        assert legacy_field in legacy
        active = shipment_requirements(entry, placement_derivable=True)
        assert active == [field for field in legacy if field != legacy_field]
        assert shipment_requirements(entry, placement_derivable=False) == legacy


@pytest.mark.parametrize("un,retained", [(1361, {"special_provisions", "verified_provisions"}),
    (1748, {"air_circulation", "shaded", "special_provisions", "verified_segregation_codes"}),
    (3109, {"special_provisions", "verified_segregation_codes"}),
    (3373, {"special_provisions", "verified_provisions"})])
def test_shipment_requirements_placement_geometry_cannot_clear_compound_guards(un, retained):
    for entry in entries_for_un(un):
        assert retained <= set(shipment_requirements(entry, placement_derivable=True))


@pytest.mark.parametrize("un", [1006, 1263, 1333, 1402, 2915, 1789, 3480])
def test_shipment_requirements_other_entries_remain_unchanged(un):
    for entry in entries_for_un(un):
        assert shipment_requirements(entry, placement_derivable=True) == shipment_requirements(entry)
