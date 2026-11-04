"""Real StowLM run folders on the stage page (task 10.2 prep, 29 Oct): preview labels, the final-showcase rule,
dead-end / no-plan cards, backtracking evidence and recorded two-step reference numbers.

Everything here reads the registered plan's own server-side metadata (the run folder the CLI named, never a request
path). Nothing judges legality: plans are still replayed by the move engine and freshly checked by the full checker
in ``app.backend.build_replay``; stored verdicts and counts are never used as results.

FINAL-SHOWCASE RULE (D99, explicit): a StowLM plan is the *final showcase* — and loses the in-frame PREVIEW label —
only when ALL hold:
  1. the planner is StowLM (verified clean-adapter model run: ``planner_kind == "stowlm_adapter"``, label ``ADAPTER``,
     result planner and trace consistent with the manifest, app/model_runs.py);
  2. its run folder resolves to ``experiments/mvp/runs/<name>`` with ``<name>`` starting ``stowlm-showcase``;
  3. its instance id is one of the three small Laem Chabang showcase calls (``SHOWCASE_INSTANCES``).
Anything else that is StowLM (pilot runs, validation maps, other folders) keeps a PREVIEW label in every mode.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.model_runs import ADAPTER, UNVERIFIED

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "experiments/mvp/runs"
FINAL_RUN_PREFIX = "stowlm-showcase"
# experiments/mvp/corpus/showcase/{feeder_1700,feeder_500,feeder_800}-small.json ids (task 9.2 inputs).
SHOWCASE_INSTANCES = ("random.small.29000201.0.7", "random.small.29000022.0.7", "random.small.29000102.0.7")
FINAL_RULE = ("final showcase = verified clean StowLM adapter run in experiments/mvp/runs/stowlm-showcase* on one of the three "
              "small Laem Chabang showcase calls (app/stage_stowlm.py); every other StowLM run is a PREVIEW")
NOT_SHIPPED = {"dead_end": "dead end", "timeout": "finisher time limit", "infeasible": "no feasible plan",
               "error": "error"}


def _model(metadata: dict) -> dict:
    return (metadata or {}).get("model_run") or {}


def is_stowlm_run(metadata: dict) -> bool:
    model = _model(metadata)
    return (bool(model.get("verified")) and model.get("planner_kind") == "stowlm_adapter"
            and model.get("label") != UNVERIFIED)


def _run_folder(metadata: dict) -> Path | None:
    folder = (metadata or {}).get("run_folder")
    if folder:
        return Path(folder).resolve()
    source = (metadata or {}).get("source_path")
    if source:
        parts = Path(source).resolve().parts
        if "instances" in parts[1:]:
            return Path(*parts[:len(parts) - 1 - parts[::-1].index("instances")])
    return None


def final_showcase(metadata: dict, instance_id: str | None) -> tuple[bool, str]:
    """(is final, why) under FINAL_RULE."""
    if not is_stowlm_run(metadata):
        return False, "not a verified StowLM run"
    if _model(metadata).get("label") != ADAPTER:
        return False, "not a clean StowLM adapter run"
    folder = _run_folder(metadata)
    if folder is None or not folder.is_relative_to(RUNS.resolve()):
        return False, "run folder is not under experiments/mvp/runs"
    rel = folder.relative_to(RUNS.resolve()).parts
    if not rel or not rel[0].startswith(FINAL_RUN_PREFIX):
        return False, f"run folder is not experiments/mvp/runs/{FINAL_RUN_PREFIX}*"
    if instance_id not in SHOWCASE_INSTANCES:
        return False, "instance is not one of the three small Laem Chabang showcase calls"
    return True, "final StowLM showcase run"


def _split_word(metadata: dict) -> str:
    text = " ".join(str(f.get("path", "")) for f in (metadata or {}).get("source_files") or [])
    text += " " + str((metadata or {}).get("source_path") or "")
    if "/corpus/val/" in text or "/val15" in text or "/val/" in text:
        return "a validation map"
    if "/corpus/train/" in text:
        return "a training map"
    return "a non-showcase map"


def preview_label(metadata: dict, instance_id: str | None) -> str | None:
    """In-frame PREVIEW label for a StowLM plan that is not the final showcase run; None otherwise."""
    if not is_stowlm_run(metadata):
        return None
    final, _ = final_showcase(metadata, instance_id)
    if final:
        return None
    folder = _run_folder(metadata)
    path = str(folder or "")
    which = "pilot 2" if "pilot2" in path else "pilot" if "pilot" in path else "a pre-final StowLM run"
    where = ("the showcase call" if instance_id in SHOWCASE_INSTANCES else _split_word(metadata))
    return f"PREVIEW: {which} on {where}, not the final model"


# ---------------------------------------------------------------- backtracking
def backtracks(metadata: dict) -> dict | None:
    """Backtracking evidence of a StowLM run, from the trace when it records rewinds, else the result's metrics.

    The pilot-2 traces (contract w2) keep only the surviving decision path (events ``action`` / ``end_of_plan``);
    rewinds are counted in ``result.metrics.gen_backtracks`` / ``gen_rewound_actions`` (one entry per sample).
    A trace row with event ``backtrack`` or ``rewind`` (future traces) is listed with its step.
    """
    model = _model(metadata)
    if not model:
        return None
    trace = model.get("trace") or []
    events = [{"step": r.get("step"), "event": r.get("event"), "to_step": r.get("to_step", r.get("rewind_to")),
               "n_rewound": r.get("n_rewound", r.get("rewound_actions"))}
              for r in trace if r.get("event") in ("backtrack", "rewind")]
    metrics = ((metadata.get("planner_result") or {}).get("metrics") or {})
    sample = metrics.get("chosen_sample") if isinstance(metrics.get("chosen_sample"), int) else 0

    def pick(key):
        value = metrics.get(key)
        if isinstance(value, list) and value:
            return value[sample] if 0 <= sample < len(value) else value[0]
        return value if isinstance(value, (int, float)) else None
    count, rewound = pick("gen_backtracks"), pick("gen_rewound_actions")
    mode = metrics.get("gen_decode_mode")
    if count is None and not events:
        return {"count": None, "rewound_actions": None, "events": [], "decode_mode": mode,
                "source": None, "note": "this run records no backtracking evidence"}
    return {"count": count if count is not None else len(events), "rewound_actions": rewound, "events": events,
            "decode_mode": mode,
            "source": "trace.jsonl backtrack events" if events else "result.json metrics.gen_backtracks / gen_rewound_actions",
            "note": None if events else "the trace keeps only the surviving path; rewinds are counted, not placed on the timeline"}


# ---------------------------------------------------------------- dead end / no shipped plan
def outcome_card(metadata: dict, instance: dict) -> dict | None:
    """Honest card for a StowLM run that produced no shippable plan (status not ``ok`` or no plan); else None."""
    if not is_stowlm_run(metadata):
        return None
    result = metadata.get("planner_result") or {}
    status = metadata.get("planner_status") or result.get("status")
    if status == "ok" and result.get("plan") is not None:
        return None
    metrics = result.get("metrics") or {}
    model = _model(metadata)
    depths = metrics.get("dead_end_depths") or []
    loads = len(instance.get("load_list") or []) if isinstance(instance.get("load_list"), list) else None
    failures = metrics.get("candidate_failures") or {}
    reason = result.get("message") or (next(iter(failures.values()), None) if isinstance(failures, dict) else None)
    kind = NOT_SHIPPED.get(status, status or "no plan")
    title = ("StowLM dead end → not shown as a plan" if status == "dead_end" else
             f"StowLM {kind} → not shown as a plan")
    trace = model.get("trace") or []
    return {"kind": status or "no_plan", "title": title, "outcome": (model.get("outcome") or {}).get("label") or kind,
            "reason": reason,
            "failure_detail": (next(iter(failures.values()), None) if isinstance(failures, dict) and failures else None),
            "decoded_actions": metrics.get("n_actions"),
            "dead_end_depth": depths[0] if depths else None,
            "load_list": loads,
            "trace_rows": sum(1 for r in trace if r.get("event") == "action"),
            "partial_moves_dropped": model.get("dead_end_partial_moves"),
            "backtracks": backtracks(metadata),
            "wall_time_s": result.get("wall_time_s"),
            "note": ("No plan is replayed or counted: the run did not deliver a plan that the full checker could pass. "
                     "Numbers on this card are the run's own records (result.json / trace.jsonl), not checker results.")}


# ---------------------------------------------------------------- recorded two-step reference (numbers only)
def recorded_twostep(metadata: dict, instance_id: str | None) -> dict | None:
    """A recorded two-step score next to the run (``<run>/../../<parent>-twostep/<instance_id>.json``), numbers only.

    Pilot-2 val15 keeps live TwoStepPlanner scores (weighted lifts, pass) without the plan, so it cannot be replayed
    or re-checked here: it is shown as a recorded reference, never as the baseline result.
    """
    folder = _run_folder(metadata)
    if folder is None or not instance_id:
        return None
    path = folder.parent.parent / f"{folder.parent.name}-twostep" / f"{instance_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if data.get("instance_id") != instance_id:
        return None
    return {"weighted_nonproductive": data.get("weighted_nonproductive"), "passed": data.get("passed"),
            "plan_status": data.get("plan_status"),
            "source": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "note": "recorded number only: the two-step plan was not saved, so it is not replayed or re-checked here"}


def stage_meta(metadata: dict, instance: dict) -> dict:
    """Additive ``meta`` fields for the stage bundle."""
    iid = instance.get("id")
    final, why = final_showcase(metadata, iid)
    stowlm = is_stowlm_run(metadata)
    return {"stowlm_run": stowlm, "final_showcase": final, "final_showcase_reason": why,
            "final_rule": FINAL_RULE, "preview_label": preview_label(metadata, iid),
            "model_outcome": outcome_card(metadata, instance), "backtracks": backtracks(metadata) if stowlm else None,
            "recorded_twostep": recorded_twostep(metadata, iid) if stowlm else None}


# ---------------------------------------------------------------- registering whole StowLM run folders
def shipped_entries(folder: str | Path, *, include_unshipped: bool = False) -> list[str]:
    """Run-relative ``instances/<id>/result.json`` entries of a StowLM run folder whose result status is ``ok``.

    The status is only a filter for the picker; every registered plan is still replayed and freshly checked when shown
    (a stored ``ok`` that fails the fresh checker is shown as REJECTED BY CHECKER, never as a result).
    """
    root = Path(folder).resolve()
    out = []
    for path in sorted(root.glob("instances/*/result.json")):
        try:
            status = json.loads(path.read_text()).get("status")
        except (OSError, ValueError, AttributeError):
            continue
        if status == "ok" or include_unshipped:
            out.append(str(path.relative_to(root)))
    return out


def hero_order(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(run, entry) pairs with the three showcase calls in the fixed D158 hero order first, the rest as they came."""
    def rank(pair: tuple[str, str]) -> int:
        parts = Path(pair[1]).parts
        instance = parts[1] if len(parts) > 1 else ""
        return SHOWCASE_INSTANCES.index(instance) if instance in SHOWCASE_INSTANCES else len(SHOWCASE_INSTANCES)
    return sorted(pairs, key=rank)


def showcase_run_folders(runs: Path = RUNS) -> list[Path]:
    """Final StowLM showcase run folders present on disk (``experiments/mvp/runs/stowlm-showcase*`` with a manifest)."""
    return sorted(p for p in runs.glob(f"{FINAL_RUN_PREFIX}*") if p.is_dir() and (p / "manifest.json").is_file())
