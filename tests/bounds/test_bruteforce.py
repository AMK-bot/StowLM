"""Tiny hand-solved checks for exhaustive search and honest search caps."""

import pytest

from stowage.bounds.bruteforce import brute_force_optimum
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves.engine import count_moves


def inversion_instance():
    data = build_instance().model_dump()
    # Isolated move/static-rule geometry, with explicit synthetic CSC evidence.
    # No fixed background cargo is part of this relaxed, non-physics oracle.
    data["vessel"]["fixed_loads"] = []
    boxes = data["containers"][1:3]
    a, b = [box["id"] for box in boxes]
    for box, pod in zip(boxes, ("THBKK", "THSGZ")):
        box.update(pod=pod, dg=None, special_stowage=[], max_gross_mass_kg=30000)
    data.update(containers=boxes, load_list=[], discharge_list=[])
    data["yard"]["stacks"] = []
    data["onboard"]["placements"] = [dict(slot="0010182", container_id=a),
                                            dict(slot="0010184", container_id=b)]
    block = data["port"]["yard"]["blocks"][0]
    block.update(n_bays=1, n_rows=1)
    data["port"]["yard"]["blocks"] = [block]
    stacks = [s for s in data["vessel"]["stacks"] if s["id"] in ("00101D", "00301D")]
    for stack in stacks:
        stack["hatch_cover_id"] = None
    cells = {cid for stack in stacks for cid in stack["cell_ids"]}
    data["vessel"].update(stacks=stacks, hatch_covers=[])
    data["vessel"]["cells"] = [cell for cell in data["vessel"]["cells"] if cell["id"] in cells]
    return Instance.model_validate(data)


def test_search_continues_after_first_complete_departure():
    instance = inversion_instance()
    result = brute_force_optimum(instance, time_limit_s=None)
    assert result.status == "optimal"
    assert result.objective == 1
    assert [move.kind for move in result.plan.moves] == ["restow_shift"]
    assert count_moves(instance, result.plan).weighted_nonproductive == result.objective


def test_cap_never_claims_incumbent_is_exact():
    result = brute_force_optimum(inversion_instance(), max_nodes=1, time_limit_s=None)
    assert result.status == "limit"
    assert result.objective == 2  # a valid departure, but its later restow can be improved
    assert result.expanded_nodes == 1


def test_correctness_search_can_disable_wall_clock_cap(monkeypatch):
    import stowage.bounds.bruteforce as oracle

    clock = iter(range(100000))
    monkeypatch.setattr(oracle, "perf_counter", lambda: next(clock) * 1000.0)
    result = brute_force_optimum(inversion_instance(), time_limit_s=None)
    assert result.status == "optimal"
    assert result.objective == 1


def test_wall_clock_timeout_reports_limit_without_claiming_optimum(monkeypatch):
    import stowage.bounds.bruteforce as oracle

    clock = iter(range(100000))
    monkeypatch.setattr(oracle, "perf_counter", lambda: next(clock) * 1000.0)
    result = brute_force_optimum(inversion_instance(), time_limit_s=0.001)
    assert result.status == "limit"
    assert result.objective is None
    assert result.expanded_nodes == 0


def test_simulator_gate_moves_cannot_remove_blockers_for_free():
    instance = inversion_instance()
    a, b = [p.container_id for p in instance.onboard.placements]
    data = instance.model_dump()
    data["onboard"]["placements"] = []
    data["load_list"] = [a]
    data["yard"]["stacks"] = [dict(key="A-001-01", length_ft=20, boxes=[a, b])]
    # There is no alternate yard location and no way to load the non-loading b.
    result = brute_force_optimum(Instance.model_validate(data), time_limit_s=None)
    assert result.status == "infeasible"
    assert result.objective is None and result.plan is None


def test_optional_departure_static_rule_checker():
    instance = inversion_instance()
    instance.rule_overrides["expert.e6.heavy_under_deck"] = {"heavy_threshold": 30_000}
    result = brute_force_optimum(instance, check_static_rules=True, time_limit_s=None)
    assert result.status == "optimal"
    assert result.objective == 1


@pytest.mark.parametrize("bad", [-1, float("inf"), float("nan")])
def test_invalid_weights_rejected(bad):
    instance = inversion_instance()
    instance.objective.restow = bad
    with pytest.raises(ValueError, match="nonnegative"):
        brute_force_optimum(instance)
