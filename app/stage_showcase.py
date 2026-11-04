"""State of the three showcase calls for the video director (D158, logic-demo P1-2): GET /api/stage/showcase.

The hero order is fixed before 9.2 (hero 29000201 whatever StowLM does, then 29000022, 29000102). For each call this
module reports, from the run folders as they are at request time:
  - StowLM state: ``none`` (no run for that call), ``failed`` (run exists; dead end / time limit / rejected / unreadable)
    or ``ok`` (a shipped plan exists on disk; the director still needs it in the catalogue, and the fresh replay plus
    full checker in the stage bundle still decide);
  - two-step and lower-bound numbers (recorded, labelled as such), and why a number is missing.

Fail-closed: anything unreadable is a failure or a dash, never a pass. Nothing here judges legality or fills a result
in. Sealed runs are not read: only ``showcase-*`` and ``stowlm-showcase*`` folders are touched (D155, D157).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.stage_stowlm import FINAL_RUN_PREFIX, NOT_SHIPPED, RUNS, SHOWCASE_INSTANCES

SHIPS = {"random.small.29000201.0.7": "feeder_1700", "random.small.29000022.0.7": "feeder_500",
         "random.small.29000102.0.7": "feeder_800"}
BOUNDS = {"random.small.29000201.0.7": "showcase-v2-gold-1700-attempt1/bound.json",
          "random.small.29000022.0.7": "showcase-v2-gold-500-attempt1/bound.json",
          "random.small.29000102.0.7": "showcase-v2-800-attempt4/bound.json"}
TWOSTEP_RUN = "showcase-v2-twostep"
GOLD_RUN = "showcase-v2-gold"
# docs/decisions.md (feeder_800 showcase): four bounded attempts, all blocked at the constructor; not a proof of infeasibility.
NO_GOLD_REASON = "no approved gold plan: bounded solver attempts found no legal heat prefix"


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _failed(kind: str, result: dict | None) -> dict:
    result = result or {}
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    depths = metrics.get("dead_end_depths") or []
    return {"state": "failed", "kind": kind, "label": NOT_SHIPPED.get(kind, kind),
            "title": ("StowLM dead end → not shown as a plan" if kind == "dead_end"
                      else "Checker rejected StowLM's plan → not shown as a plan" if kind == "rejected"
                      else f"StowLM {NOT_SHIPPED.get(kind, kind)} → not shown as a plan"),
            "reason": result.get("message"), "decoded_actions": metrics.get("n_actions"),
            "dead_end_depth": depths[0] if depths else None, "wall_time_s": result.get("wall_time_s")}


def stowlm_state(instance_id: str, runs: Path = RUNS) -> dict:
    """The call's StowLM run state. An ok result in any ``stowlm-showcase*`` folder wins; otherwise the first failure."""
    failed = None
    for folder in sorted(p for p in runs.glob(f"{FINAL_RUN_PREFIX}*") if p.is_dir()):
        run_dir = folder / "instances" / instance_id
        path = run_dir / "result.json"
        if not path.is_file():
            continue
        result = _load(path)
        if result is None:
            failed = failed or _failed("error", None)
            continue
        status = result.get("status")
        if status != "ok" or result.get("plan") is None:
            failed = failed or _failed(status if status and status != "ok" else "error", result)
            continue
        outcome = _load(run_dir / "outcome.json")
        if outcome is not None and outcome.get("checker_passed") is False:
            failed = failed or _failed("rejected", result)
            continue
        count = (outcome or {}).get("count") if outcome is not None and outcome.get("checker_passed") is True else None
        if count is None and outcome is not None and outcome.get("checker_passed") is True:
            report = _load(run_dir / "report.json") or {}  # 9.2 outcome files carry no count; the run's own report does
            count = report.get("count") if report.get("passed") is True else None
        moves = count.get("weighted_nonproductive") if isinstance(count, dict) else None
        return {"state": "ok", "run": folder.name, "moves": moves}
    return failed or {"state": "none"}


def _twostep(instance_id: str, runs: Path) -> dict:
    outcome = _load(runs / TWOSTEP_RUN / "instances" / instance_id / "outcome.json")
    if outcome is None:
        return {"moves": None, "reason": "no two-step record"}
    count = outcome.get("count") if isinstance(outcome.get("count"), dict) else {}
    if outcome.get("checker_passed") is True and outcome.get("departure_approved") is True:
        return {"moves": count.get("weighted_nonproductive"), "reason": None}
    status = outcome.get("planner_status")
    return {"moves": None, "reason": NOT_SHIPPED.get(status, status or "no plan")}


def _gold(instance_id: str, runs: Path) -> dict:
    manifest = _load(runs / GOLD_RUN / "manifest.json") or {}
    entries = manifest.get("entries") if isinstance(manifest.get("entries"), list) else []
    row = next((e for e in entries if isinstance(e, dict) and e.get("instance_id") == instance_id), None)
    if row and row.get("checker_passed") is True and row.get("departure_approved") is True:
        return {"approved": True, "reason": None}
    return {"approved": False, "reason": NO_GOLD_REASON}


def _bound(instance_id: str, runs: Path) -> float | None:
    data = _load(runs / BOUNDS[instance_id]) or {}
    value = data.get("weighted")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def showcase_status(runs: Path = RUNS) -> dict:
    calls = []
    for iid in SHOWCASE_INSTANCES:
        calls.append({"instance_id": iid, "short": iid.split(".")[2], "ship": SHIPS[iid], "stowlm": stowlm_state(iid, runs),
                      "twostep": _twostep(iid, runs), "gold": _gold(iid, runs), "lower_bound": _bound(iid, runs)})
    return {"order": list(SHOWCASE_INSTANCES), "calls": calls,
            "note": "recorded run folders, read at request time; shown plans are still replayed and freshly checked"}
