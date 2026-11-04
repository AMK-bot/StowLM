"""Recorded instruction replan (D144): the rule check is recomputed on load, and a bad plan is never presented."""
import json
from pathlib import Path

import pytest

from app.inputs import load_inputs
from app.instruct import boxes_without_a_slot
from app.stage_extras import BINDING_RUN, INSTRUCTION_ENTRY, INSTRUCTION_LABEL, INSTRUCTION_RUN, instruction_inputs
from app.stage_instruction import (IN_FRAME_LABEL, PLANNER, STOWLM_IN_FRAME_LABEL, STOWLM_LABEL, boxes_moved,
                                   find_recorded_plan, instruction_block, refused_outcome, recorded_config)

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not (INSTRUCTION_RUN / INSTRUCTION_ENTRY).is_file(), reason="run folder not on disk")


@pytest.fixture(scope="module")
def recorded():
    from stowage.moves import apply_plan

    loaded = load_inputs(run_folder=INSTRUCTION_RUN, entry=INSTRUCTION_ENTRY)
    final = apply_plan(loaded.instance, loaded.plan).final_state.ship.placements
    return {"metadata": {"run_folder": str(INSTRUCTION_RUN), "planner": loaded.metadata.get("planner"),
                         "shippable": True, "departure_approved": True},
            "instance": loaded.instance.model_dump(mode="json"), "plan": loaded.plan.model_dump(mode="json"),
            "checker": {"passed": True},
            "frames": [{"state": {"ship": {"placements": [{"container_id": p.container_id, "slot": p.slot}
                                                          for p in final]}}}]}


def test_block_flips_the_flagged_box_from_a_fresh_computation(recorded):
    block = instruction_block(recorded)
    assert block["before_violations"] == [
        "load QZXU0000040 -> 0030382: instruction: QZXU0000040 may not be stowed at 0030382"]
    assert block["after_violations"] == [] and block["honoured"] and block["presented"]
    assert block["flagged"] == [{"box": "QZXU0000040", "imdg_class": "8", "before_slot": "0030382",
                                 "after_slot": "0010382", "before_breaks": True, "after_honoured": True}]
    assert block["sentence"] and block["method"] and block["constraint"]["imdg_class"] == "8"
    assert block["after_counts"]["weighted"] == block["before_counts"]["weighted"] == 37


def test_violations_come_from_the_recorded_constraint_not_a_stored_verdict(recorded, tmp_path):
    config = json.loads((INSTRUCTION_RUN / "config.json").read_text())
    config["instruction"]["constraint"]["away_from"]["bay"] = 1  # the replan's slot is now the forbidden one
    (tmp_path / "config.json").write_text(json.dumps(config))
    block = instruction_block({**recorded, "metadata": {**recorded["metadata"], "run_folder": str(tmp_path)}})
    assert block["after_violations"] and not block["honoured"] and not block["presented"]
    assert "breaks the instruction" in block["refusal"]
    assert all(row["after_slot"] is None for row in block["flagged"]) and block["after_counts"] is None


def test_failed_checker_is_not_presented(recorded):
    block = instruction_block({**recorded, "checker": {"passed": False}})
    assert not block["presented"] and block["after_counts"] is None and "checker" in block["refusal"]
    card = refused_outcome(block)
    assert "not shown as a plan" in card["title"] and card["reason"] == block["refusal"]


def test_unshippable_recorded_replan_is_not_presented(recorded):
    metadata = {**recorded["metadata"], "shippable": False, "departure_approved": False}
    block = instruction_block({**recorded, "metadata": metadata})
    assert not block["presented"] and block["after_counts"] is None and "shipping approval" in block["refusal"]


def test_other_runs_have_no_block_and_no_label():
    assert recorded_config({"run_folder": str(ROOT / "experiments/mvp/runs/showcase-v2-gold")}) is None
    assert instruction_block({"metadata": {}}) is None


def test_d99_label_in_every_mode_and_never_stowlm(recorded):
    assert "not StowLM" in IN_FRAME_LABEL and instruction_block(recorded)["in_frame_label"] == IN_FRAME_LABEL
    assert "StowLM" not in INSTRUCTION_LABEL and "stowlm" not in PLANNER
    source = (ROOT / "app/stage.py").read_text()
    assert 'preview_label=instruction["in_frame_label"], preview_label_all_modes=True' in source
    assert "(VIDEO && !m.preview_label_all_modes)" in (ROOT / "app/static/stage.js").read_text()
    assert '"solver.gold_warmstart.instruction": "Search software replan under the instruction"' in source
    assert 'if not instruction["not_stowlm"]:  # D170: the two-step rival ran without this instruction' in source


def test_showcase_registration_labels_the_replan():
    inputs, notes = instruction_inputs()
    assert not notes and inputs[0].metadata["label"] == INSTRUCTION_LABEL
    assert inputs[0].metadata["planner"] == PLANNER and "model_run" not in inputs[0].metadata


# ------------------------------------------------------------------ D170: the one StowLM exception
@pytest.fixture(scope="module")
def binding():
    from stowage.moves import apply_plan

    loaded = load_inputs(run_folder=BINDING_RUN, entry=INSTRUCTION_ENTRY)
    final = apply_plan(loaded.instance, loaded.plan).final_state.ship.placements
    return {"metadata": {**loaded.metadata, "run_folder": str(BINDING_RUN), "shippable": True, "departure_approved": True},
            "instance": loaded.instance.model_dump(mode="json"), "plan": loaded.plan.model_dump(mode="json"),
            "checker": {"passed": True},
            "frames": [{"state": {"ship": {"placements": [{"container_id": p.container_id, "slot": p.slot}
                                                          for p in final]}}}]}


def test_binding_numbers_are_computed_from_the_plans():
    """85, 67, +18, 13 boxes, 2 placements: replayed from the two plans, never read from the run's own report."""
    from llm.instructions.apply import apply
    from llm.instructions.schema import DgKeepAway
    from stowage.model import MovePlan
    from stowage.moves import apply_plan

    loaded = load_inputs(run_folder=BINDING_RUN, entry=INSTRUCTION_ENTRY)
    card = json.loads((BINDING_RUN / "instruction-card.json").read_text())
    record = json.loads((BINDING_RUN / "before-plan.json").read_text())
    before = MovePlan.model_validate(record["plan"])
    hero = json.loads((ROOT / "experiments/mvp/runs/stowlm-showcase" / INSTRUCTION_ENTRY).read_text())["plan"]
    assert record["plan"]["moves"] == hero["moves"]  # the plan before is StowLM's hero plan, move for move
    assert apply_plan(loaded.instance, loaded.plan).count.weighted_nonproductive == 85
    assert apply_plan(loaded.instance, before).count.weighted_nonproductive == 67
    constrained = apply(loaded.instance, DgKeepAway.model_validate(card["instruction"]["constraint"]))
    assert len(constrained.violations(before)) == 2 and constrained.violations(loaded.plan) == []
    assert boxes_moved(loaded.instance, before, loaded.plan) == 13
    assert (loaded.plan.moves != before.moves) and not loaded.metadata.get("repaired")


def test_binding_block_lifts_the_label_for_this_folder_only(binding, recorded):
    block = instruction_block(binding)
    assert block["presented"] and block["not_stowlm"] is False and block["label"] == STOWLM_LABEL
    assert block["in_frame_label"] == STOWLM_IN_FRAME_LABEL and "not StowLM" not in block["in_frame_label"]
    assert (block["after_counts"]["weighted"], block["before_counts"]["weighted"]) == (85, 67)
    assert block["boxes_moved"] == 13 and block["placements_broken_before"] == 2 and block["after_violations"] == []
    assert block["before_plan"] == "experiments/mvp/runs/stowlm-instruction-binding/before-plan.json"
    assert {row["box"] for row in block["flagged"]} == {"QZXU0000040"} and len(block["flagged"]) == 2
    solver = instruction_block(recorded)  # instruction-replan-dg keeps its label
    assert solver["not_stowlm"] is True and solver["in_frame_label"] == IN_FRAME_LABEL and "not StowLM" in IN_FRAME_LABEL


def test_a_stowlm_card_anywhere_else_is_not_presented(binding, tmp_path):
    for name in ("instruction-card.json", "before-plan.json"):
        (tmp_path / name).write_text((BINDING_RUN / name).read_text())
    block = instruction_block({**binding, "metadata": {**binding["metadata"], "run_folder": str(tmp_path)}})
    assert not block["presented"] and block["not_stowlm"] is True and "D170" in block["refusal"]
    assert block["in_frame_label"] == IN_FRAME_LABEL and block["after_counts"] is None


@pytest.mark.parametrize("change, word", [
    ({"checker": {"passed": False}}, "checker"),
    ({"metadata_over": {"shippable": False}}, "shipping approval"),
    ({"metadata_over": {"departure_approved": False}}, "shipping approval"),
    ({"metadata_over": {"repaired": True}}, "repaired"),
    ({"metadata_over": {"model_run": {}}}, "verified clean"),
])
def test_binding_gate_fails_closed(binding, change, word):
    replay = {**binding, **{k: v for k, v in change.items() if k != "metadata_over"}}
    replay["metadata"] = {**binding["metadata"], **change.get("metadata_over", {})}
    block = instruction_block(replay)
    assert not block["presented"] and word in block["refusal"] and block["after_counts"] is None
    assert "not shown as a plan" in refused_outcome(block)["title"]


def test_binding_breaking_the_instruction_is_not_presented(binding, monkeypatch):
    import app.stage_instruction as module

    card = json.loads((BINDING_RUN / "instruction-card.json").read_text())
    card["instruction"]["constraint"]["away_from"]["bay"] = 3  # the replan's slots are now the forbidden ones
    monkeypatch.setattr(module, "recorded_config", lambda metadata: card)
    block = instruction_block(binding)
    assert block["after_violations"] and not block["honoured"] and not block["presented"]
    assert "breaks the instruction" in block["refusal"]


def test_superseded_run_is_never_registered():
    names = [Path(i.metadata["run_folder"]).name for i in instruction_inputs()[0]]
    assert names == ["instruction-replan-dg", "stowlm-instruction-binding"]


def test_recorded_lookup_prefers_the_stowlm_replan():
    class Demo:
        plans = [{"id": "plan-8", "planner": PLANNER, "run": "instruction-replan-dg"},
                 {"id": "plan-9", "planner": "stowlm", "run": "stowlm-instruction-binding"}]

        def catalog(self):
            return {"plans": self.plans}

    assert find_recorded_plan(Demo()) == "plan-9"
    Demo.plans = Demo.plans[:1]
    assert find_recorded_plan(Demo()) == "plan-8"


def test_a_box_with_no_slot_of_its_length_is_found(recorded):
    from llm.instructions.apply import apply
    from llm.instructions.schema import DgKeepAway
    from stowage.model import Instance

    instance = Instance.model_validate(recorded["instance"])
    class3 = apply(instance, DgKeepAway(type="dg_keep_away", imdg_class="3", min_bays=1,
                                        away_from={"kind": "bay", "bay": 1}))
    assert class3.capacity_shortfall() is None and list(boxes_without_a_slot(class3)) == ["QZXU0000117"]
    class8 = apply(instance, DgKeepAway(type="dg_keep_away", imdg_class="8", min_bays=1,
                                        away_from={"kind": "bay", "bay": 3}))
    assert boxes_without_a_slot(class8) == {}
