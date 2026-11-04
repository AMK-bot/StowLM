"""Geometry and objective edge cases outside the seeded single-cell corpus."""

import pytest

from stowage.bounds import lower_bound
from stowage.model import Instance
from stowage.model.fixtures import build_instance


def test_spanning_blocker_requires_access_to_both_covers_and_counts_once():
    data = build_instance(layover=True).model_dump()
    a, b, c, d = [box["id"] for box in data["containers"][:4]]
    data["yard"]["stacks"] = []
    data["yard"]["quay_buffer"] = []
    data["load_list"] = []
    data["discharge_list"] = [a]
    for box in data["containers"]:
        box["pod"] = "THLCH" if box["id"] == a else "THBKK"
        box.update(length_ft=40 if box["id"] == b else 20, height_m=2.591,
                   iso_type="42G1" if box["id"] == b else "22G1")
    # A spanning through-box obstructs the target's hold stack. Its other
    # half sits under another cover, whose deck box must also be shifted.
    data["onboard"]["placements"] = [
        dict(slot="0010102", container_id=a),
        dict(slot="0020104", container_id=b),
        dict(slot="0030102", container_id=c),
        dict(slot="0010282", container_id=d),
    ]
    for stack in data["vessel"]["stacks"]:
        if stack["id"] == "00301H":
            stack["hatch_cover_id"] = "HC-02-2"
    first, second = data["vessel"]["hatch_covers"]
    first["hold_stack_ids"].remove("00301H")
    second["hold_stack_ids"].append("00301H")
    bound = lower_bound(Instance.model_validate(data))
    assert bound.restow_lifts == 2
    assert bound.hatch_cover_lifts == 4
    assert bound.weighted == 6


@pytest.mark.parametrize("key", ["relocate", "restow", "hatch_cover", "future_restow", "future_hatch_cover"])
@pytest.mark.parametrize("value", [-1.0, float("inf"), float("nan")])
def test_invalid_objective_weights_fail_explicitly(key, value):
    instance = build_instance()
    instance.objective = instance.objective.model_copy(update={key: value})
    with pytest.raises(ValueError, match="finite nonnegative"):
        lower_bound(instance)
