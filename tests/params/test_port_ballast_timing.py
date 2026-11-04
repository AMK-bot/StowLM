"""D74 B2/T18 raw evidence exists even for non-operational next-call profiles."""

import tomllib
from urllib.parse import urlparse

import pytest

from stowage.params import params_dir
from stowage.ports import list_ports


@pytest.mark.parametrize("name", list_ports())
def test_all_port_files_declare_positive_labelled_per_crane_timing(name):
    raw = tomllib.loads((params_dir("ports") / f"{name}.toml").read_text())
    expected = 20 if name in {"small_feeder", "cat_lai_like"} else 25
    for field, value, unit, bounds in (
        ("gross_moves_per_hour", expected, "moves/h/crane", "20-35"),
        ("pre_operation_allowance_h", 1.0, "h", "0.25-1.5"),
        ("post_operation_allowance_h", 1.0, "h", "0.25-1.0"),
    ):
        record = raw["quay_crane"][field]
        assert record["value"] == value > 0
        assert record["unit"] == unit
        assert record["assumption"].startswith("APPROXIMATION:")
        for label in ("synthetic", "UNVERIFIED", "Q23", "Q30", bounds):
            assert label in record["assumption"]
    depth = raw["berth_depth_m"]
    assert depth["value"] > 0 and depth["unit"] == "m"
    assert raw["berth_depth_assumption"].startswith("APPROXIMATION:")
    assert "No tide or UKC increment" in raw["berth_depth_assumption"]
    if name in {"laem_chabang_like", "singapore_like"}:
        assert urlparse(depth["source"]["url"]).hostname in {"hutchisonports.co.th", "www.globalpsa.com"}
        assert "source" in depth and "assumption" not in depth
    else:
        assert depth["assumption"].startswith("APPROXIMATION:") and "synthetic" in depth["assumption"]
