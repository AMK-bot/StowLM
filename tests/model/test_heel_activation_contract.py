"""Public pair metadata distinguishes D64 validation from saved off-state input."""

import pytest

from stowage.model import TankPair


def test_pair_schema_explicitly_scopes_conservation_to_rule_freeze_v1():
    description = TankPair.model_json_schema()["properties"]["role"].get("description", "")
    assert "rule_freeze_v1" in description
    assert "only" in description
    assert "omitted" in description


@pytest.mark.parametrize("declared", [{}, {"WB-P": 1.0}, {"WB-P": 102500.0, "WB-S": 0.0}])
def test_pair_declaration_preserves_exact_legacy_departure_adapter_output(declared):
    """Off-state empty/unequal totals are preserved; construction is not certification."""
    from stowage.checker.full import departure_condition
    from stowage.model import BallastCondition, MovePlan, Vessel
    from stowage.model.fixtures import build_instance
    from stowage.rules.catalogue import rule_freeze_v1

    instance = build_instance()
    plan = MovePlan(id="synthetic-legacy", instance_id=instance.id, call_index=0,
                    ballast=BallastCondition(tank_masses_kg=declared, source="planner"))
    with rule_freeze_v1(enabled=False):
        baseline = departure_condition(instance, plan)
        instance.vessel = Vessel.model_validate({**instance.vessel.model_dump(), "tank_pairs": [
            TankPair(id="HEEL", port_tank="WB-P", starboard_tank="WB-S")]})
        paired = departure_condition(instance, plan)
        assert paired.model_dump_json() == baseline.model_dump_json()
        assert paired.tank_masses_kg["WB-P"] == declared.get("WB-P", 0.0)
        assert paired.tank_masses_kg["WB-S"] == declared.get("WB-S", 0.0)


@pytest.mark.parametrize("declared", [{}, {"WB-P": 1.0}])
def test_enabled_d64_consumer_rejects_departure_total_changes(declared):
    """Existing operational consumer owns conservation; no duplicate model enforcement."""
    from stowage.checker.full import departure_condition
    from stowage.checker.in_port_liquids import prepare_context
    from stowage.model import BallastCondition, MovePlan, Vessel
    from stowage.model.fixtures import build_instance
    from stowage.rules.catalogue import rule_freeze_v1

    instance = build_instance()
    instance.vessel = Vessel.model_validate({**instance.vessel.model_dump(), "tank_pairs": [
        TankPair(id="HEEL", port_tank="WB-P", starboard_tank="WB-S")]})
    plan = MovePlan(id="synthetic-on", instance_id=instance.id, call_index=0,
                    ballast=BallastCondition(tank_masses_kg=declared, source="planner"))
    with rule_freeze_v1(enabled=True):
        departure = departure_condition(instance, plan)
        with pytest.raises(ValueError, match="departure HEEL pair total must equal arrival pair total"):
            prepare_context(instance, departure)
