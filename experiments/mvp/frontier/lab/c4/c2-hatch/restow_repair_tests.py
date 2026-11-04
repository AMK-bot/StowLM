"""Physical synthetic tests for temporary placement and a one-lift ship shift."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from bound_tests import fixture
from restow_repair import repair
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.model import Instance
from stowage.moves import apply_plan


def two_box_deck_fixture():
    data = fixture().model_dump()
    data["containers"] = data["containers"][:2]
    ids = [box["id"] for box in data["containers"]]
    data["load_list"] = ids
    data["yard"]["stacks"][0]["boxes"] = ids
    data["objective"].update(relocate=2, restow=1)
    bottom = next(cell for cell in data["vessel"]["cells"] if cell["id"] == "0010182")
    upper = dict(bottom, id="0010184")
    data["vessel"]["cells"].append(upper)
    next(stack for stack in data["vessel"]["stacks"]
         if stack["id"] == "00101D")["cell_ids"].append("0010184")
    return Instance.model_validate(data)


def test_one_direct_shift_preserves_yard_order_and_saves_weighted_lifts():
    instance = two_box_deck_fixture()
    lower, upper = instance.load_list
    assignment = {lower: "0010182", upper: "0010184"}
    result = repair(instance, assignment, [upper, lower], {"ship_restowed": [upper]},
                    budget_s=3, max_temporary_slots=20)
    assert result.plan is not None, (result.status, result.rejected)
    replay = apply_plan(instance, result.plan)
    assert replay.legal
    assert [action.box for action in result.actions] == [upper, lower, upper]
    assert sum(move.kind == "restow_shift" for move in result.plan.moves) == 1
    assert not any(move.kind == "relocate" for move in result.plan.moves)
    load_once = get_action_space("boxslot").expand(instance,
        [Place(lower, assignment[lower]), Place(upper, assignment[upper])])
    assert result.replay_lifts < apply_plan(instance, load_once).count.weighted_nonproductive
    assert not result.full_checker_pass
    assert "not_a_global_bound" in result.family


def test_unsupported_order_without_flag_is_unknown_not_infeasible():
    instance = two_box_deck_fixture()
    lower, upper = instance.load_list
    result = repair(instance, {lower: "0010182", upper: "0010184"},
                    [upper, lower], budget_s=2)
    assert result.plan is None
    assert result.status.startswith("unknown_")


def test_load_once_witness_uses_no_temporary_domain_or_repeat():
    instance = two_box_deck_fixture()
    lower, upper = instance.load_list
    result = repair(instance, {lower: "0010182", upper: "0010184"},
                    [lower, upper], budget_s=2)
    assert result.plan is not None
    assert len(result.actions) == len(instance.load_list)
    assert not any(move.kind.startswith("restow") for move in result.plan.moves)


def test_flag_and_work_limits_are_unknown():
    instance = two_box_deck_fixture()
    lower, upper = instance.load_list
    assignment = {lower: "0010182", upper: "0010184"}
    assert repair(instance, assignment, [upper, lower], [upper],
                  max_flagged=0).status == "unknown_flag_limit"
    limited = repair(instance, assignment, [lower, upper], max_nodes=0)
    assert limited.plan is None
    assert limited.status == "unknown_work_limit"
