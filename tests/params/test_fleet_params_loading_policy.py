"""D40 explicit synthetic loading-manual inputs; no inferred missing limits."""

from pathlib import Path
import tomllib

import pytest

FLEET = Path(__file__).resolve().parents[2] / "params" / "fleet"


def _ship(name):
    return tomllib.loads((FLEET / f"{name}.toml").read_text())


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800", "feeder_1700"])
def test_fleet_declares_signed_trim_pair_with_independent_evidence(name):
    ship = _ship(name)
    limits = ship["operating_limits"]
    assert limits["min_trim_m"] == -limits["max_trim_m"]
    assert limits["max_trim_m"] == pytest.approx(.01 * ship["hull"]["lpp_m"])
    for field in ("min_trim_m", "max_trim_m"):
        evidence = limits["provenance"][field]
        assumption = evidence["assumption"]
        assert assumption.startswith("APPROXIMATION:")
        assert "Absolute aft-minus-forward draught difference" in assumption
        assert "0.01Lpp" in assumption
        assert "synthetic loading-manual planning limit" in assumption


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800", "feeder_1700"])
def test_fleet_declares_zero_bilge_keel_area_and_source(name):
    limits = _ship(name)["operating_limits"]
    assert limits["bilge_keel_area_m2"] == 0.0
    assert limits["bilge_keel"] is False
    evidence = limits["provenance"]["bilge_keel_area_m2"]
    assert evidence["assumption"].startswith("APPROXIMATION:")
    assert "No bilge or bar keels" in evidence["assumption"]
    assert "k=1" in evidence["assumption"]
    source = evidence["source"]
    assert source["document"] == "IMO MSC.267(85), 2008 IS Code"
    assert "Part A 2.3.4, PDF p15" in source["section"]
    assert "table 2.3.4-3, PDF p16" in source["section"]
    assert source["url"].endswith("MSC.267(85).pdf")
