"""7.1/8.1 SFT admission of activated D74 checker reports carrying parts.in_port.ballast_programme
(experiments/stability/in-port-criteria/DESIGN.md Addendum B §B.3, incl. ballast_delay_h). CPU only."""

import json

import pytest
from pydantic import ValidationError

from llm.stowlm.sft_data import validate_in_port_programme
from stowage.model import CheckReport, MoveCount

PROGRAMME = {"changeover_k": 12, "lifts_after_k": 30, "pumped_mass_t": 140.5, "abs_change_t": 160.0,
             "pump_time_required_h": 0.6, "pump_time_available_h": 1.2, "ballast_delay_h": 0.25,
             "pump_window_lifts": 30, "ballast_pump_m3_h": 250.0, "t_lift_s": 120.0, "cranes": 2,
             "gross_moves_per_hour": 30.0, "heel_step_limit_t": 40.0, "heel_max_step_t": 22.0,
             "heel_max_ideal_step_t": 20.0, "heel_path_feasible": True, "heel_path_linearised_miss": False,
             "rate_check_rule": "port.ballast.transfer_rate"}


def activated_report(programme=PROGRAMME, *, key=True) -> CheckReport:
    """A public-JSON activated report fixture: parsed exactly as sft_data parses published gold."""
    in_port = {"name": "in_port", "ran": True}
    if key:
        in_port["ballast_programme"] = programme
    data = {"passed": True, "count": MoveCount().model_dump(mode="json"),
            "parts": {"moves": {"name": "moves"}, "in_port": in_port}}
    return CheckReport.model_validate(json.loads(json.dumps(data)))


def test_activated_report_with_programme_is_admitted():
    report = activated_report()
    assert report.parts["in_port"].ballast_programme.ballast_delay_h == 0.25
    assert validate_in_port_programme(report, activated=True) == "present"
    assert validate_in_port_programme(activated_report(None), activated=True) == "null"  # explicit null: no programme


@pytest.mark.parametrize("field, value", [("pumped_mass_t", -1.0), ("ballast_delay_h", float("nan")),
                                          ("pump_time_required_h", float("inf")), ("cranes", 0),
                                          ("rate_check_rule", "port.ballast.other")])
def test_malformed_programmes_are_rejected(field, value):
    with pytest.raises(ValueError, match="malformed ballast_programme"):
        validate_in_port_programme(activated_report({**PROGRAMME, field: value}), activated=True)


def test_schema_level_malformations_fail_at_parse():
    broken = {k: v for k, v in PROGRAMME.items() if k != "pump_time_available_h"}
    with pytest.raises(ValidationError):
        activated_report(broken)
    with pytest.raises(ValidationError):
        activated_report({**PROGRAMME, "cranes": "two"})


def test_legacy_shaped_in_port_part_is_not_activated_evidence():
    with pytest.raises(ValueError, match="lacks its explicit ballast_programme"):
        validate_in_port_programme(activated_report(key=False), activated=True)


def test_switch_off_reports_are_unchanged():
    assert validate_in_port_programme(activated_report(key=False), activated=False) is None
    assert validate_in_port_programme(activated_report({**PROGRAMME, "cranes": 0}), activated=False) is None
    legacy = CheckReport.model_validate({"passed": True, "count": MoveCount().model_dump(mode="json"),
                                         "parts": {"moves": {"name": "moves"}}})
    assert validate_in_port_programme(legacy, activated=True) is None  # no in-port part: nothing to check
    assert "ballast_programme" not in legacy.parts["moves"].model_dump()  # legacy bytes stay the same


def test_gold_admission_calls_the_programme_check():
    import inspect

    from llm.stowlm import sft_data

    source = inspect.getsource(sft_data.build_gold_example)
    assert "validate_in_port_programme(report, activated=rule_freeze_v1_enabled())" in source
