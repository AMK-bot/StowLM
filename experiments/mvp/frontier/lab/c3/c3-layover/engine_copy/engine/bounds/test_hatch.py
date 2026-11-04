# Copied from experiments/mvp/frontier/lab/c1/fx-exact/test_bound.py; engine integration changes are local.
"""Bounds checked against exhaustive move-engine optima and stored evidence."""
from pathlib import Path
import sys

import pytest

from .hatch import lower_bound, _floor_scaled
from stowage.bounds.bruteforce import brute_force_optimum
from stowage.io import load_instance
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1


def fixture(*, initially_off=False, current=1, future=1):
    """Three ordinary loads, two deck cells and two cells under one cover.

    Geometry pattern adapted from tests/bounds/test_forced_hold.py; production
    models and exhaustive move engine are reused without modification.
    """
    data = build_instance(layover=True).model_dump()
    boxes = data["containers"][:3]
    for box in boxes:
        box.update(length_ft=20, iso_type="22G1", height_m=2.591,
            tare_kg=2200, gross_mass_kg=12000, max_gross_mass_kg=30000,
            reefer=False, reefer_setpoint_c=None, dg=None, oog=None,
            special_stowage=[], direct_delivery=False, pod="THBKK")
    ids = [box["id"] for box in boxes]
    data.update(containers=boxes, load_list=ids, discharge_list=[], stage="A")
    stacks = [s for s in data["vessel"]["stacks"]
              if s["id"] in {"00101D", "00301D", "00101H"}]
    for stack in stacks:
        stack["hatch_cover_id"] = "HC-02-1"
        stack["cell_ids"] = stack["cell_ids"][:1 if stack["on_deck"] else 2]
    cells = {cell for stack in stacks for cell in stack["cell_ids"]}
    cover = data["vessel"]["hatch_covers"][0]
    cover.update(bays=[1, 3], hold_stack_ids=["00101H"],
                 deck_stack_ids=["00101D", "00301D"])
    data["vessel"].update(stacks=stacks, hatch_covers=[cover], fixed_loads=[])
    data["vessel"]["cells"] = [c for c in data["vessel"]["cells"] if c["id"] in cells]
    block = data["port"]["yard"]["blocks"][0]
    block.update(n_bays=2, n_rows=1, max_tiers=4, allowed_lengths_ft=[20])
    data["port"]["yard"]["blocks"] = [block]
    data["yard"].update(stacks=[dict(key="A-001-01", length_ft=20, boxes=ids)], quay_buffer=[])
    data["onboard"].update(placements=[], covers_off={"HC-02-1": "quay"} if initially_off else {})
    data["objective"].update(hatch_cover=current, future_hatch_cover=future)
    return Instance.model_validate(data)


@pytest.mark.parametrize("freeze", [False, True])
@pytest.mark.parametrize("off,current,future,expected", [
    (False, 1, 1, 4), (True, 1, 1, 3), (False, 1.5, .25, 3.5),
    (False, 0, 1, 2), (False, 1, 0, 2)])
def test_exact_relaxation_matches_exhaustive_oracle(freeze, off, current, future, expected):
    with rule_freeze_v1(enabled=freeze):
        instance = fixture(initially_off=off, current=current, future=future)
        oracle = brute_force_optimum(instance, max_nodes=10000, time_limit_s=None)
        result = lower_bound(instance, budget_s=2)
        assert oracle.status == "optimal"
        assert oracle.objective == pytest.approx(expected)
        assert result.status == "optimal"
        assert result.lower_bound <= oracle.objective
        assert result.lower_bound == pytest.approx(expected)


def test_early_timeout_returns_bound_not_incumbent():
    instance = fixture()
    result = lower_bound(instance, budget_s=.001)
    assert result.lower_bound >= result.counting_lower_bound
    # A time-limited solver must retain the existing bound and distinguish its
    # lower bound from its incumbent.
    assert result.status in {"unknown", "feasible", "optimal"}
    if result.relaxation_incumbent is not None:
        assert result.hatch_lower_bound <= result.relaxation_incumbent


def test_fractional_objective_rounds_down_exactly():
    from fractions import Fraction
    for value in [0.0004999999999999999, 0.1, 1.001, 1.23456789]:
        assert Fraction(_floor_scaled(value), 1000) <= Fraction(value)


def test_initial_arrival_uses_existing_counting_bound():
    instance = build_instance(layover=True)
    result = lower_bound(instance, budget_s=1)
    assert result.status == "counting_only"
    assert result.lower_bound == result.counting_lower_bound


@pytest.mark.parametrize("freeze,expected", [(False, 4), (True, 0)])
def test_e6_bottom_witness_matches_static_oracle(freeze, expected):
    data = fixture().model_dump()
    data["containers"] = data["containers"][:2]
    for box in data["containers"]:
        box["gross_mass_kg"] = 22000
    data["load_list"] = [b["id"] for b in data["containers"]]
    data["yard"]["stacks"][0]["boxes"] = data["load_list"]
    with rule_freeze_v1(enabled=freeze):
        instance = Instance.model_validate(data)
        oracle = brute_force_optimum(instance, max_nodes=10000, time_limit_s=None,
                                    check_static_rules=True)
        result = lower_bound(instance, budget_s=2)
        assert oracle.status == "optimal"
        assert result.lower_bound <= oracle.objective
        assert result.lower_bound == pytest.approx(expected)
        assert ("0 safe bottom-slot" in result.note) == freeze


def test_lateral_oog_disables_e6_cuts():
    data = fixture().model_dump()
    data["containers"] = data["containers"][:2]
    for box in data["containers"]:
        box["gross_mass_kg"] = 22000
    data["containers"][0]["oog"] = {"over_width_port_m": .1}
    data["load_list"] = [b["id"] for b in data["containers"]]
    data["yard"]["stacks"][0]["boxes"] = data["load_list"]
    with rule_freeze_v1(enabled=False):
        result = lower_bound(Instance.model_validate(data), budget_s=2)
    assert "0 safe bottom-slot" in result.note
    assert result.lower_bound == 0


@pytest.mark.parametrize("supplied_tables", [False, True])
def test_inactive_dg_retains_counting_bound_before_using_unary_tables(monkeypatch, supplied_tables):
    from . import hatch
    from stowage.bounds import lower_bound as counting_lower_bound
    data = fixture().model_dump()
    reference = next(box.dg for box in build_instance().containers if box.dg is not None)
    data["containers"][0]["dg"] = reference.model_dump()
    instance = Instance.model_validate(data)
    # A table build must not run, and caller-supplied potentially overrestrictive
    # tables must not be consulted either. DG geometry is not passed as a unary
    # impossibility certificate under the inactive rule setting.
    def forbidden_build(instance):
        raise AssertionError("inactive DG packing must defer to counting")
    monkeypatch.setattr(hatch, "build_tables", forbidden_build)
    with rule_freeze_v1(enabled=False):
        base = counting_lower_bound(instance)
        result = lower_bound(instance, budget_s=.001,
                             tables=object() if supplied_tables else None)
    assert result.status == "counting_only"
    assert result.lower_bound == base.weighted
    assert result.relaxation_assignment is None
    assert result.variables == 0 and result.constraints == 0
    assert "supported upper-tier" in result.note
