"""The model's total weighted objective includes later-call projections."""

from stowage.model import Instance, MovePlan
from stowage.model.fixtures import build_instance
from stowage.moves import apply_plan


def test_weighted_objective_can_be_positive_with_zero_current_lifts():
    payload = build_instance(layover=True).model_dump(mode="json")
    payload["load_list"] = []
    payload["discharge_list"] = []
    placements = payload["onboard"]["placements"]
    placements[0]["slot"], placements[1]["slot"] = "0010182", "0010184"
    bottom, top = (p["container_id"] for p in placements)
    for box in payload["containers"]:
        if box["id"] == bottom:
            box["pod"] = "THBKK"
        elif box["id"] == top:
            box["pod"] = "THSGZ"
    instance = Instance.model_validate(payload)
    replay = apply_plan(instance, MovePlan(id="projection-only", instance_id=instance.id, call_index=0))
    assert replay.legal
    assert replay.count.total_lifts_now == 0
    assert sum(f.restow_lifts for f in replay.count.future) == 2
    assert sum(f.hatch_cover_lifts for f in replay.count.future) == 0
    assert replay.count.weighted_nonproductive == 2
