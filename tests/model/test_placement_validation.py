"""Vessel-aware arrival placement contracts, independent of checker policy."""

import pytest
from pydantic import ValidationError

from stowage.model import Instance
from stowage.model.fixtures import build_instance


def layover_payload(length_ft=20):
    payload = build_instance(layover=True).model_dump(mode="json")
    if length_ft != 20:
        onboard = {p["container_id"] for p in payload["onboard"]["placements"]}
        for box in payload["containers"]:
            if box["id"] in onboard:
                box.update(length_ft=length_ft, iso_type="42G1" if length_ft == 40 else "L5G1",
                           height_m=2.591 if length_ft == 40 else 2.896)
        for placement in payload["onboard"]["placements"]:
            placement["slot"] = "002" + placement["slot"][3:]
    return payload


def test_onboard_placements_reject_unsupported_stack():
    payload = layover_payload()
    payload["onboard"]["placements"][0]["slot"] = "0030102"
    with pytest.raises(ValidationError, match=r"onboard\.placements.*unsupported.*0010104"):
        Instance.model_validate(payload)


@pytest.mark.parametrize("length_ft", [40, 45])
@pytest.mark.parametrize("missing_bay", ["001", "003"])
def test_long_onboard_placement_requires_both_support_columns(length_ft, missing_bay):
    payload = layover_payload(length_ft)
    placement = payload["onboard"]["placements"][0]
    placement["slot"] = ("003" if missing_bay == "001" else "001") + "0102"
    box = next(b for b in payload["containers"] if b["id"] == placement["container_id"])
    box.update(length_ft=20, iso_type="22G1", height_m=2.591)
    with pytest.raises(ValidationError, match=r"onboard\.placements.*unsupported.*" + missing_bay + "0104"):
        Instance.model_validate(payload)


@pytest.mark.parametrize("length_ft,bay", [(20, "001"), (40, "001"), (40, "003"),
                                         (45, "001"), (45, "003")])
def test_onboard_placements_require_each_cell_length_capability(length_ft, bay):
    payload = layover_payload(length_ft)
    cell = next(c for c in payload["vessel"]["cells"] if c["id"] == bay + "0102")
    field = f"accepts_{length_ft}"
    cell[field] = False
    with pytest.raises(ValidationError, match=r"onboard\.placements.*" + field + ".*" + cell["id"]):
        Instance.model_validate(payload)


@pytest.mark.parametrize("length_ft", [20, 40, 45])
def test_supported_onboard_placements_validate(length_ft):
    assert len(Instance.model_validate(layover_payload(length_ft)).onboard.placements) == 2


@pytest.mark.parametrize("length_ft", [20, 40, 45])
def test_onboard_deck_stack_begins_at_hatch_independently_of_hold(length_ft):
    payload = layover_payload(length_ft)
    for placement in payload["onboard"]["placements"]:
        placement["slot"] = placement["slot"][:-2] + str(int(placement["slot"][-2:]) + 80)
    assert len(Instance.model_validate(payload).onboard.placements) == 2


def test_hold_cargo_does_not_fill_gap_above_hatch():
    payload = layover_payload()
    payload["onboard"]["placements"][-1]["slot"] = "0010184"
    with pytest.raises(ValidationError, match=r"onboard\.placements.*unsupported.*0010184"):
        Instance.model_validate(payload)


@pytest.mark.parametrize("length_ft", [40, 45])
def test_long_onboard_placement_can_rest_on_two_supported_20ft_boxes(length_ft):
    payload = layover_payload(length_ft)
    placement = payload["onboard"]["placements"][0]
    placement["slot"] = "0010102"
    first = next(b for b in payload["containers"] if b["id"] == placement["container_id"])
    first.update(length_ft=20, iso_type="22G1", height_m=2.591)
    cid = payload["load_list"].pop()
    stack = next(s for s in payload["yard"]["stacks"] if cid in s["boxes"])
    stack["boxes"].remove(cid)
    if not stack["boxes"]:
        payload["yard"]["stacks"].remove(stack)
    payload["onboard"]["placements"].append(dict(slot="0030102", container_id=cid))
    assert len(Instance.model_validate(payload).onboard.placements) == 3


def test_onboard_support_uses_vessel_cell_sequence_instead_of_tier_arithmetic():
    payload = layover_payload()
    for cell in payload["vessel"]["cells"]:
        if cell["id"] == "0010104":
            cell["id"] = "0010106"
    stack = next(s for s in payload["vessel"]["stacks"] if s["id"] == "00101H")
    stack["cell_ids"][-1] = "0010106"
    payload["onboard"]["placements"][-1]["slot"] = "0010106"
    assert len(Instance.model_validate(payload).onboard.placements) == 2
