"""T18 ship rates and source limits after the D74 parameter amendment."""

from pathlib import Path
import tomllib

import pytest


FLEET = Path(__file__).resolve().parents[2] / "params" / "fleet"
VALUES = {
    "feeder_500": (500, 600, 2, [200, 500], [200, 2200]),
    "feeder_800": (700, 1000, 2, [300, 700], [200, 2200]),
    "feeder_1700": (1000, 1500, 3, [400, 1000], [200, 2200]),
}
DAMEN_SPEC = "https://www.marine-marchande.net/Voyage/Bruno/Henrike-Schepers/specification-container-feeders-800.pdf"
FRAMO_BROCHURE = "https://www.framo.com/media/azejuddh/framo-anti-heeling_system-brochure-small.pdf"


@pytest.mark.parametrize("name", VALUES)
def test_t18_ballast_rates_are_explicit_positive_and_labelled(name):
    params = tomllib.loads((FLEET / f"{name}.toml").read_text())
    system = params["ballast_system"]
    pump, heel, cranes, pump_range, heel_range = VALUES[name]
    assert (system["ballast_pump_m3_h"], system["heel_transfer_m3_h"], system["max_cranes"]) == (pump, heel, cranes)
    assert all(system[key] > 0 for key in ("ballast_pump_m3_h", "heel_transfer_m3_h", "max_cranes"))
    assert system["ballast_pump_range_m3_h"] == pump_range
    assert system["heel_transfer_range_m3_h"] == heel_range
    assert pump_range[0] <= pump <= pump_range[1]
    assert heel_range[0] <= heel <= heel_range[1]
    assert system["assumption"].startswith("APPROXIMATION: synthetic; Q23")
    assert "UNVERIFIED" in system["assumption"]
    assert "Q30" in system["assumption"]
    assert "Damen CF800" in system["assumption"] and "1120 m3/h" in system["assumption"]
    assert "Framo" in system["assumption"] and "200-2200 m3/h" in system["assumption"]
    assert "not vessel-certified" in system["assumption"]
    assert "gravity draining is not modelled (conservative)" in system["assumption"]
    assert "9-10" not in system["assumption"]
    assert "D74" in system["provenance"] and "B.2" in system["provenance"]
    assert "logic-d74.md" in system["provenance"]
    assert DAMEN_SPEC in system["provenance"]
    assert FRAMO_BROCHURE in system["provenance"]


def test_readme_distinguishes_commercial_context_from_selected_synthetic_rates():
    text = (FLEET / "README.md").read_text()
    assert DAMEN_SPEC in text and FRAMO_BROCHURE in text
    assert "9-10h sizing rule is withdrawn" in text
    assert "not vessel-certified" in text
    assert "gravity draining is not modelled (conservative)" in text
