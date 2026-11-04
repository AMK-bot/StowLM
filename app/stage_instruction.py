"""Recorded instruction replan on the stage page (D144): the rule check that flips for the flagged box.

A run folder whose ``config.json`` has ``format: instruction-replan-v1`` records one sentence and its constraint next
to a solver plan made under it (``experiments/mvp/ui/make_dg_replan.py``). Nothing stored about the outcome is trusted:
the violations of the plan before the instruction and of the replan are recomputed here from the recorded constraint
with ``ConstrainedInstance.violations`` each time the bundle is built, and the verdict is the app's own fresh checker.

D99: this is a solver plan. It carries the in-frame label in every mode and is never called StowLM. A plan that fails
the fresh checker or breaks the instruction is not presented: the bundle turns into a refusal card over the start state.

D170, the one exception, an explicit rule (``stowlm_replan``): ``experiments/mvp/runs/stowlm-instruction-binding`` is a
pre-registered StowLM decode under a Spark-parsed sentence, so that folder alone may carry a StowLM label, and only as a
verified clean adapter run of the hero call with no repair. Its record is ``instruction-card.json`` (the folder's
``config.json`` is the eval driver's). Every other folder, ``instruction-replan-dg`` included, stays "not StowLM"; a
StowLM card anywhere else, or a card here that fails the rule, is not presented. The gate above is unchanged for it.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "instruction-replan-v1"
PLANNER = "solver.gold_warmstart.instruction"
LABEL = "Search software replan under the instruction"
IN_FRAME_LABEL = "search software replan under the instruction — not StowLM"
LIVE_SOURCE_KIND = "Live instruction replan"
CARD_FILE = "instruction-card.json"
STOWLM_RUN = "stowlm-instruction-binding"  # D170: the only folder the D99 "not StowLM" label is lifted for
STOWLM_CALL = "random.small.29000201.0.7"
STOWLM_LABEL = "StowLM replan under the instruction"
STOWLM_IN_FRAME_LABEL = "StowLM replan under the instruction · one pre-registered call"
METHOD = ("ConstrainedInstance.violations over every placing move, recomputed on load from the recorded constraint, "
          "plus an app-side re-check of the replayed final ship state")


def recorded_config(metadata: dict) -> dict | None:
    """The run folder's instruction record, or None for every other plan."""
    folder = metadata.get("run_folder")
    if not folder:
        return None
    for name in (CARD_FILE, "config.json"):
        path = Path(folder) / name
        if not path.is_file():
            continue
        try:
            config = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if isinstance(config, dict) and config.get("format") == FORMAT:
            return config
    return None


def stowlm_replan(metadata: dict, instance_id: str | None) -> tuple[bool, str]:
    """(may this record be called StowLM's replan, why) under the D170 rule in the module docstring."""
    from app.model_runs import ADAPTER
    from app.stage_stowlm import RUNS, is_stowlm_run

    folder = Path(str(metadata.get("run_folder") or "")).resolve()
    if folder != (RUNS / STOWLM_RUN).resolve():
        return False, f"a StowLM replan is shown only from experiments/mvp/runs/{STOWLM_RUN} (D170)"
    if not is_stowlm_run(metadata) or (metadata.get("model_run") or {}).get("label") != ADAPTER:
        return False, "this run is not a verified clean StowLM adapter run"
    if instance_id != STOWLM_CALL:
        return False, "this run is not on the pre-registered hero call"
    if metadata.get("repaired"):
        return False, "this plan was repaired"
    return True, "D170: pre-registered StowLM decode under the instruction"


def _before_plan(config: dict, instance) -> tuple[object | None, str | None]:
    """The plan the instruction is checked against first, bound to the same input bytes."""
    from stowage.model import MovePlan

    path = (ROOT / str(config.get("gold_plan"))).resolve()
    if not path.is_relative_to(ROOT / "experiments/mvp/runs") or not path.is_file():
        return None, "the plan before the instruction is not on disk"
    record = json.loads(path.read_text())
    if record.get("input_sha256") != (config.get("input") or {}).get("sha256"):
        return None, "the plan before the instruction belongs to a different input"
    plan = MovePlan.model_validate(record["plan"])
    return (plan, None) if plan.instance_id == instance.id else (None, "different instance id")


def _placement(violation: str) -> tuple[str, str] | None:
    """(box, slot) of one ``ConstrainedInstance.violations`` row: ``<kind> <box> -> <slot>: <reason>``."""
    head = violation.split(":", 1)[0].split()
    return (head[1], head[3]) if len(head) == 4 and head[2] == "->" else None


def _classes(instance, boxes) -> dict:
    from llm.instructions.apply import _dg_classes

    return {b.id: sorted(_dg_classes(b)) for b in instance.containers if b.id in boxes}


def _counts(replay_count) -> dict:
    return {"weighted": replay_count.weighted_nonproductive, "total_lifts": replay_count.total_lifts_now}


def boxes_moved(instance, before_plan, after_plan) -> int:
    """Boxes whose final ship slot differs between two plans, from a replay of each."""
    from stowage.moves import apply_plan

    slots = [{p.container_id: p.slot for p in apply_plan(instance, plan).final_state.ship.placements}
             for plan in (before_plan, after_plan)]
    return sum(slots[0].get(box) != slots[1].get(box) for box in set(slots[0]) | set(slots[1]))


def instruction_block(replay: dict) -> dict | None:
    """The bundle's ``instruction`` block for a recorded instruction replan, else None."""
    from app.instruct import honoured_check
    from llm.instructions.apply import apply, describe
    from llm.instructions.schema import DgKeepAway
    from stowage.model import Instance, MovePlan
    from stowage.moves import apply_plan

    metadata = replay.get("metadata") or {}
    config = recorded_config(metadata)
    if config is None:
        return None
    recorded = config.get("instruction") or {}
    instance = Instance.model_validate(replay["instance"])
    plan = MovePlan.model_validate(replay["plan"]) if replay.get("plan") else None
    constrained = apply(instance, DgKeepAway.model_validate(recorded["constraint"]))
    check = honoured_check(constrained, plan, replay) if plan is not None else None
    before_plan, before_reason = _before_plan(config, instance)
    before = constrained.violations(before_plan) if before_plan is not None else None
    after = check["move_violations"] if check else None
    passed = bool((replay.get("checker") or {}).get("passed"))
    honoured = bool(check and check["honoured"])
    shippable = bool(metadata.get("shippable")) and metadata.get("departure_approved") is True
    stowlm_card = config.get("planner") == "stowlm"
    lifted, lift_reason = stowlm_replan(metadata, instance.id) if stowlm_card else (False, "")
    presented = bool(plan is not None and passed and honoured and shippable and (lifted or not stowlm_card))
    final_slot = {}
    frames = replay.get("frames") or []
    if frames:
        final_slot = {p["container_id"]: p["slot"] for p in frames[-1]["state"]["ship"]["placements"]}
    flagged = []
    if before:
        rows = [row for row in map(_placement, before) if row]
        classes = _classes(instance, {box for box, _ in rows})
        flagged = [{"box": box, "imdg_class": ", ".join(classes.get(box, [])), "before_slot": slot,
                    "after_slot": final_slot.get(box) if presented else None,
                    "before_breaks": True, "after_honoured": presented} for box, slot in rows]
    refusal = None
    if not presented:
        refusal = ("no plan" if plan is None else "the fresh full checker did not pass this plan" if not passed else
                   "this plan breaks the instruction: " + "; ".join((check or {}).get("move_violations", [])
                                                                    + (check or {}).get("state_violations", []))
                   if not honoured else "this plan has no clean shipping approval" if not shippable else lift_reason)
    return {"sentence": recorded.get("sentence"), "constraint": recorded["constraint"],
            "parse": recorded.get("parse"), "applied": describe(constrained),
            "label": STOWLM_LABEL if lifted else LABEL, "racer_label": "StowLM replan" if lifted else "Search software replan",
            "in_frame_label": STOWLM_IN_FRAME_LABEL if lifted else IN_FRAME_LABEL, "not_stowlm": not lifted, "method": METHOD,
            "before_plan": config.get("gold_plan"), "before_reason": before_reason,
            "before_violations": before, "after_violations": after, "state_violations": (check or {}).get(
                "state_violations"), "honoured": honoured, "checker_passed": passed, "shippable": shippable, "presented": presented,
            "refusal": refusal, "flagged": flagged,
            "before_counts": _counts(apply_plan(instance, before_plan).count) if before_plan is not None else None,
            "after_counts": _counts(apply_plan(instance, plan).count) if presented else None,
            "boxes_moved": boxes_moved(instance, before_plan, plan) if presented and before_plan is not None else None,
            "placements_broken_before": len(before) if before is not None else None}


def refused_outcome(block: dict) -> dict:
    """The stage's no-plan card, for a recorded replan that must not be presented."""
    return {"kind": "instruction_replan_refused", "title": "Instruction replan → not shown as a plan",
            "outcome": "not presented", "reason": block["refusal"],
            "note": "No plan is replayed or counted: a recorded replan is shown only when the fresh full checker "
                    "passes it, it has clean shipping approval and the instruction holds on every placing move."}


def shown_gate(passed: bool, shippable: bool, honoured: bool) -> bool:
    """The one presentation gate of an instruction replan: full checker pass AND shippable AND honoured."""
    return bool(passed and shippable and honoured)


def live_replan_refusal(replay: dict) -> str | None:
    """Why a live instruction replan must not be presented, or None. Other plans are not its business.

    ``app.instruct`` registers shown plans only; this holds for anything else carrying the live-replan source kind.
    The verdicts are read from the fresh replay, the honoured flag from the gate recorded when it was computed.
    """
    metadata = replay.get("metadata") or {}
    if metadata.get("source_kind") != LIVE_SOURCE_KIND:
        return None
    gate = metadata.get("instruction_gate") or {}
    if gate.get("honoured") is not True:
        return "the instruction is not honoured by this plan"
    if not (replay.get("checker") or {}).get("passed"):
        return "the fresh full checker did not pass this plan"
    if not (metadata.get("shippable") is True and metadata.get("departure_approved") is True):
        return "this plan has no clean shipping approval"
    return None


def refused_live_outcome(reason: str) -> dict:
    return {"kind": "instruction_replan_refused", "title": "Instruction replan → not shown as a plan",
            "outcome": "not presented", "reason": reason,
            "note": "No plan is replayed or counted: a replan is shown only when the fresh full checker passes it, it "
                    "has clean shipping approval and the instruction holds."}


def find_recorded_plan(demo) -> str | None:
    """Registry id of the recorded instruction replan, if this launch registered one; StowLM's (D170) comes first."""
    rows = demo.catalog()["plans"]
    for row in rows:
        if row.get("run") == STOWLM_RUN:
            return row["id"]
    for row in rows:
        if row.get("planner") == PLANNER:
            return row["id"]
    return None
