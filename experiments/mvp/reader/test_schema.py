"""Contract tests use hand-authored examples, never the sealed evaluation set."""
import json

import pytest

from experiments.mvp.reader.schema import (
    CloseBay, DgKeepAway, MaxTiers, OnePortPerHatch, SCHEMA_VERSION,
    answer_envelope, canonicalize, context_from_instance, messages, refusal,
    system_prompt, validate_answer,
)


CONTEXT = {"id": "test-synthetic", "bays": [1, 2, 3],
           "max_tiers": {"hold": 4, "deck": 6}, "has_accommodation": True,
           "hatch_covers": ["HC-P", "HC-S"], "ports": ["SGSIN", "MYPKG"]}


@pytest.mark.parametrize("constraint", [
    CloseBay(type="close_bay", bay=2),
    MaxTiers(type="max_tiers", bay=None, zone="hold", max_tiers=0),
    DgKeepAway(type="dg_keep_away", imdg_class="2.1", min_bays=2,
               away_from={"kind": "bay", "bay": 1}),
    OnePortPerHatch(type="one_port_per_hatch"),
])
def test_strict_roundtrip(constraint):
    assert validate_answer(json.dumps(answer_envelope(constraint)), CONTEXT) == constraint


@pytest.mark.parametrize("payload", [
    {"type": "close_bay", "bay": 9},
    {"type": "max_tiers", "bay": 1, "zone": "deck", "max_tiers": 7},
    {"type": "max_tiers", "bay": 1, "zone": "hold", "max_tiers": -1},
    {"type": "max_tiers", "bay": 1, "zone": "hold", "max_tiers": True},
    {"type": "dg_keep_away", "imdg_class": "3.6", "min_bays": 1,
     "away_from": {"kind": "bay", "bay": 1}},
    {"type": "one_port_per_hatch", "allow_mixing": True},
    {"type": "open_bay", "bay": 1},
    {"type": "one_port_per_hatch", "scope": "deck"},
])
def test_unrepresentable_or_out_of_context(payload):
    with pytest.raises(ValueError):
        validate_answer(json.dumps({"schema_version": SCHEMA_VERSION, "constraint": payload}), CONTEXT)


def test_no_repairs_and_no_duplicate_keys():
    raw = json.dumps(answer_envelope(CloseBay(type="close_bay", bay=1)))
    for invalid in ["```json\n" + raw + "\n```", raw + raw,
                    raw.replace('"bay": 1', '"bay": 1, "bay": 3')]:
        with pytest.raises(ValueError):
            validate_answer(invalid, CONTEXT)


def test_contextual_anchor_and_zone_and_bay_specific_limits():
    absent = {**CONTEXT, "has_accommodation": False}
    dg = DgKeepAway(type="dg_keep_away", imdg_class="8", min_bays=1,
                    away_from={"kind": "accommodation"})
    with pytest.raises(ValueError):
        validate_answer(json.dumps(answer_envelope(dg)), absent)
    narrow = {**CONTEXT, "max_tiers_by_bay": {"1": {"hold": 2}}}
    cap = MaxTiers(type="max_tiers", bay=1, zone="hold", max_tiers=3)
    with pytest.raises(ValueError):
        validate_answer(json.dumps(answer_envelope(cap)), narrow)


def test_refusal_prose_only_is_ignored():
    expected = answer_envelope(refusal("loosening"))
    actual = json.loads(json.dumps(expected))
    actual["refusal"]["reason"] = "No permission to relax."
    assert canonicalize(expected) == canonicalize(actual)
    actual["refusal"]["code"] = "ambiguous"
    assert canonicalize(expected) != canonicalize(actual)


def test_identical_prompt_builder_for_all_arms():
    result = messages("Close bay 1.", CONTEXT)
    assert result[0] == {"role": "system", "content": system_prompt()}
    assert result[1]["content"].endswith("Sentence: Close bay 1.")
    contract = json.load(open("experiments/mvp/reader/contract.json"))
    assert contract["system_prompt"] == system_prompt()


def test_context_derived_from_physical_cells_not_declared_capacity():
    raw = {"id": "synthetic-test", "call_index": 0,
           "rotation": {"calls": [{"index": 0, "locode": "THLCH"}, {"index": 1, "locode": "SGSIN"}]},
           "vessel": {"cells": [{"id": "0010102"}, {"id": "0030102"}],
                      "stacks": [{"bay": b, "on_deck": False,
                                  "cell_ids": [f"{b:03d}0102", f"{b:03d}0104"]} for b in [1, 3]],
                      "hatch_covers": [{"id": "HC", "bays": [1, 2, 3]}], "exclusion_regions": []}}
    context = context_from_instance(raw)
    assert context["bays"] == [1, 2, 3]
    assert context["max_tiers"] == {"hold": 1, "deck": 0}
    assert context["ports"] == ["SGSIN"]
    # Odd 3 and 5 can be adjacent across a hatch boundary without physical bay 4.
    raw["vessel"]["cells"].append({"id": "0050102"})
    raw["vessel"]["stacks"].append({"bay": 5, "on_deck": False, "cell_ids": ["0050102"]})
    assert context_from_instance(raw)["bays"] == [1, 2, 3, 5]
