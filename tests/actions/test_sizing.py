"""Keep the action-space sizing evidence aligned with its explicit assumptions."""
from pathlib import Path
import re
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_documented_medium_ceiling_and_budget_match_current_ladder():
    """Regression: the old 120-box / 1,010-token row undersized medium."""
    source = (ROOT / "src/stowage/generate/random_yard.py").read_text()
    medium = int(re.search(r'"medium": SizeRung\((\d+),', source).group(1))
    assert medium == 150
    readme = (ROOT / "experiments/action-space/README.md").read_text()
    assert f"| medium | {medium} | 1,260 |" in readme


def test_sizing_report_distinguishes_ceilings_actuals_and_measured_tokens():
    sizing = runpy.run_path(str(ROOT / "experiments/action-space/sizing.py"))
    report = sizing["report"]()
    assert {name: row["ceiling"] for name, row in report["rungs"].items()} == {
        "tiny": 6, "small": 36, "medium": 150, "full": 2000}
    assert report["rungs"]["full"]["ceiling_tokens"] == 16800
    assert report["direct_planning_decision"] == {"loads": 1000, "tokens": 8400}
    readme = (ROOT / "experiments/action-space/README.md").read_text()
    for name, row in report["rungs"].items():
        assert row["documented_feeder_500_loads"] <= row["ceiling"]
        assert (f'| {name} | {row["ceiling"]:,} | {row["ceiling_tokens"]:,} | '
                f'{row["documented_feeder_500_loads"]:,} | '
                f'{row["documented_feeder_500_tokens"]:,} |') in readme
    measured = report["recorded_dense_measurements"]
    assert [(row["loads"], row["output_tokens_including_eos"]) for row in measured] == [
        (400, 2801), (800, 5601), (400, 2801), (800, 5601)]
    assert all(row["output_tokens_including_eos"] <= row["planning_tokens_excluding_eos"] + 1
               for row in measured)


@pytest.mark.parametrize("loads, expected", [(0, 0), (6, 51), (13, 110), (53, 446),
                                            (150, 1260), (189, 1588), (1000, 8400)])
def test_planning_estimate_uses_exact_ceiling_arithmetic(loads, expected):
    sizing = runpy.run_path(str(ROOT / "experiments/action-space/sizing.py"))
    assert sizing["planning_tokens"](loads) == expected
