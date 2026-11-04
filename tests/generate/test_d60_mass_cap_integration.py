"""Real production providers retain D58 while applying D60 to emitted cargo."""
from copy import deepcopy

import pytest

from stowage.generate import instances, instances_v2_random, random_yard
from stowage.generate.cargo_limits import MAX_GENERATED_GROSS_MASS_KG
from stowage.generate.instances import GenerationReport
from stowage.params import load_param_file


@pytest.mark.slow
@pytest.mark.parametrize("provider", ["random", "simulated"])
def test_real_medium_providers_cap_cargo_without_weakening_background_or_demand(provider):
    """No mocks or demand restrictions: both real full-checker gates must pass."""
    audit = GenerationReport()
    kwargs = dict(seed=1, noise=.2, vessel_name="feeder_800",
                  port_name="laem_chabang_like", max_attempts=50, report=audit)
    if provider == "random":
        result = instances_v2_random.generate_random_v2("medium", **kwargs)
        parent = random_yard._full_vessel("feeder_800")
    else:
        result = instances.generate_simulated_instance("medium", **kwargs)
        parent = instances._full_vessel("feeder_800")

    assert result.vessel.hull is not None
    assert result.containers and len(result.containers) > len(result.load_list)
    assert all(box.tare_kg <= box.gross_mass_kg <= MAX_GENERATED_GROSS_MASS_KG
               for box in result.containers)
    fixed_boxes = [box for load in result.vessel.fixed_loads
                   for box in load.cargo_boxes or []]
    assert fixed_boxes
    assert all(box.gross_mass_kg <= MAX_GENERATED_GROSS_MASS_KG for box in fixed_boxes)

    params = deepcopy(load_param_file("cargo", "feeder_export"))
    ratings = {item["iso_size_type"]: item["max_gross_kg"]["value"]
               for item in params["equipment"]}
    assert all(box.max_gross_mass_kg == ratings[box.iso_type] for box in result.containers)
    assert any(box.max_gross_mass_kg > MAX_GENERATED_GROSS_MASS_KG for box in result.containers)
    assert result.family["cargo_profile"] == "feeder_export"
    assert result.family["equipment_profile"] == "full_mix"
    assert result.family["requested_n_boxes"] == "auto"

    # Independently repeat the unchanged capacity rule for the actual route.
    # Its booking is preserved in full; no lost demand makes the cap test pass.
    params["rotations"] = [route for route in params["rotations"]
                           if route["id"] == result.rotation.service]
    expected, budget, lengths = random_yard.capacity_load_size(
        result.vessel, random_yard.SIZE_LADDER["medium"], params, full_vessel=parent)
    assert len(result.load_list) == audit.requested_targets == expected
    assert audit.capacity_teu == len(result.vessel.cells)
    assert audit.teu_budget == budget and audit.length_slot_budgets == lengths
    assert audit.booking_reference["region_capacity_teu"] == audit.capacity_teu
    targets = [box for box in result.containers if box.id in result.load_list]
    assert audit.realised_teu == sum(box.length_ft / 20 for box in targets)
    assert audit.completion_checks >= 1 and audit.c2_screened is False

    assert audit.accepted and audit.background_checks >= 1
    assert audit.background_last["accepted"] is True
    assert set(audit.background_last["states"]) == {"departure", "mid_voyage", "arrival"}
    assert audit.background_last["states"]["arrival"]["b23_ratio"] >= 1.05
