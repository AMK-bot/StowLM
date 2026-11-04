"""Planner payloads for the Functional app (CONTRACT.md Extension B): the take-4 planner, live.

``payload(call_dir, name)`` builds, from one call's own files, the documents the planner page reads in live mode
(``GET /api/functional/calls/<id>/planner/<name>``). Where a demo file exists (the demo app's static ``<name>.json`` documents)
the shape is the same, built with the same ``app/planner_data.py`` builders, so ``planner-bayplan.js`` and the shell
render them unchanged.

* ``ship``, ``boxes``, ``plan-before`` (the arrival state), ``package`` and ``state`` exist as soon as the call has a
  synthetic instance (assembled, else base).
* ``plan-after``, ``moves``, ``meters`` and ``checks`` pass ``functional_view.released`` first: an L0 full-checker
  pass, or a relaxed plan with its level and broken rules named. Anything else raises ``NoPlan`` (the route answers
  409 with its text).
* Meters are the checker's own measured values for this plan at departure (GM and the minimum it was checked
  against, list and trim, draught, hull stress), the plan's move count and the receipt's active rule count. A value
  the checker didn't record is null, never borrowed from the demo's hero.

Nothing here imports the engine or recomputes a verdict; ``stowage.conventions`` (slot addresses) is the only
engine-side import, through ``planner_data``.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from types import SimpleNamespace

from app.functional_view import LIFTS_WORDS, SYNTHETIC, NoPlan, _placements, released, run_folder

NAMES = ("ship", "boxes", "plan-before", "plan-after", "moves", "meters", "checks", "package", "state")
GATED = frozenset({"plan-after", "moves", "meters", "checks"})
PACKAGE_FILES = ("bay-plan.html", "crane-sequence.csv", "receipt.json", "note-to-master.md", "package.json")
SCHEMA = "stowlm.functional-planner/1"

__all__ = ["NAMES", "GATED", "NoPlan", "payload"]


_RELEASED: dict[str, tuple] = {}


def _released(call_dir: Path) -> tuple[dict, dict]:
    """``functional_view.released``, cached per call on the outcome file's (mtime, size): the gate still runs on every
    new or rewritten outcome, and a large outcome is parsed once instead of once per planner document."""
    path = call_dir / run_folder(call_dir) / "outcome.json"
    try:
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return released(call_dir)
    hit = _RELEASED.get(str(path))
    if hit and hit[0] == key:
        return hit[1]
    result = released(call_dir)
    _RELEASED.clear()  # one call's outcome at a time; payloads for a call arrive together
    _RELEASED[str(path)] = (key, result)
    return result


def _read(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _file_record(call_dir: Path, *names: str, notes: list[str] | None = None) -> dict:
    files = []
    for name in names:
        path = call_dir / name
        if path.is_file():
            files.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {"call_id": call_dir.name, "files": files, "notes": list(notes or []), "label": SYNTHETIC}


def _instance(call_dir: Path) -> tuple[dict, str]:
    for name in ("assembled-instance.json", "base-instance.json"):
        data = _read(call_dir / name)
        if isinstance(data, dict) and data.get("vessel"):
            return data, name
    raise NoPlan("This call has no synthetic instance yet. Create the call and wait for its case.")


def _full_teu(instance: dict) -> int | None:
    vessel = instance.get("vessel") or {}
    for text in (vessel.get("slice_of"), vessel.get("id")):
        match = re.search(r"feeder_(\d+)", str(text or ""))
        if match:
            return int(match.group(1))
    return None


def _ship(call_dir: Path) -> dict:
    from app.planner_data import _plan_slots, build_ship

    instance, name = _instance(call_dir)
    case = _read(call_dir / "case.json") or {}
    teu = _full_teu(instance)
    boxes = len(instance.get("load_list") or [])
    fill = case.get("fill")
    words = ", ".join(part for part in ("Synthetic feeder", f"{teu} TEU" if teu else None) if part)
    words += f" · {boxes} boxes" + (f" · {round(100 * fill)} % fill" if isinstance(fill, (int, float)) else "")
    ship = build_ship(instance, source=_file_record(call_dir, name, "case.json"), words=words)
    ship["ship"]["section_teu"] = ship["ship"]["teu"]
    ship["ship"]["teu"] = teu or ship["ship"]["section_teu"]
    ship["ship"]["synthetic_label"] = SYNTHETIC
    onboard = SimpleNamespace(placements=[SimpleNamespace(**p) for p in (instance.get("onboard") or {}).get("placements") or []])
    ship["initial_plan"] = {
        "plan_id": f"{instance.get('id')}.arrival", "label": "Arrival state before this call's cargo moves",
        "slots": _plan_slots(onboard, instance),
        "counts": {"boxes": len(onboard.placements), "total_moves": 0},
        "source": _file_record(call_dir, name, notes=["The call's arrival placements, from its own synthetic instance."]),
    }
    return ship


def _boxes(call_dir: Path) -> dict:
    from app.planner_data import build_boxes

    instance, name = _instance(call_dir)
    return build_boxes(instance, source=_file_record(call_dir, name))


def _plan_before(call_dir: Path) -> dict:
    ship = _ship(call_dir)
    arrival = ship["initial_plan"]
    return {**arrival, "meters": None, "rule": None}


# ---------------------------------------------------------------- gated: the released plan
def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _departure_part(checker: dict, call_index: int, kind: str) -> tuple[str | None, dict]:
    """The checker part for this call's departure condition (``condition:<service>:call-<i>:<port>:departure:<kind>``)."""
    parts = checker.get("parts") or {}
    for name, part in parts.items():
        if (name.startswith("condition:") and f":call-{call_index}:" in name and name.endswith(f":departure:{kind}")
                and isinstance(part, dict)):
            return name, part
    return None, {}


def _meters(outcome: dict, summary: dict) -> dict:
    from app.planner_data import _limits

    instance, plan = outcome["instance"], outcome["plan"]
    checker = outcome.get("checker") if isinstance(outcome.get("checker"), dict) else {}
    ci = int(instance.get("call_index") or 0)
    stab_name, stab = _departure_part(checker, ci, "stability")
    str_name, strength = _departure_part(checker, ci, "strength")
    ok = lambda part: bool(part) and part.get("ran") is True and not part.get("error")  # noqa: E731
    m = (stab.get("metrics") or {}) if ok(stab) else {}
    margins = (stab.get("margins") or {}) if ok(stab) else {}
    gm = m.get("gm_m") if _finite(m.get("gm_m")) else None
    gm_min = gm - margins["gm_m"] if gm is not None and _finite(margins.get("gm_m")) else None
    heel = abs(math.degrees(m["list_rad"])) if _finite(m.get("list_rad")) else None
    trim = m.get("trim_m") if _finite(m.get("trim_m")) else None
    drafts = [m[k] for k in ("draught_aft_m", "draught_fwd_m") if _finite(m.get(k))]
    draught = max(drafts) if drafts else None
    # Utilisation is metrics.max_*_ratio (actual/permissible); margins.*_ratio is 1 - that, never the meter.
    sm = (strength.get("metrics") or {}) if ok(strength) else {}
    ratios = [sm[k] for k in ("max_sf_ratio", "max_bm_ratio") if _finite(sm.get(k))]
    hull = 100 * max(ratios) if ratios else None
    limits = _limits(instance)
    return {
        "gm_m": gm, "gm_min_m": gm_min, "heel_deg": heel, "heel_max_deg": limits.get("heel_max_deg"),
        "trim_m": trim, "trim_min_m": limits.get("trim_min_m"), "trim_max_m": limits.get("trim_max_m"),
        "draught_m": draught, "draught_limit_m": limits.get("draught_m"), "hull_pct": hull,
        "moves": len(plan.get("moves") or []), "lifts": summary.get("lifts"), "lifts_words": LIFTS_WORDS,
        "active_rules": summary.get("active_rules"),
        "passed": summary.get("passed"), "label": summary.get("label"),
        "source": {"checker_parts": [n for n in (stab_name, str_name) if n],
                   "notes": ["Measured by the full checker for this plan at departure: GM, list, trim and draught from the "
                             "stability part (minimum GM = GM minus its checked margin), hull stress = the larger of the "
                             "shear-force and bending-moment utilisation ratios (max_sf_ratio, max_bm_ratio) from the strength part's metrics. Draught and list limits are the "
                             "instance's operating limits. A value the checker didn't record is null.",
                             "Rule count from the package receipt's active rules."]},
    }


def _plan_after(call_dir: Path, outcome: dict, summary: dict) -> dict:
    from app.planner_data import slot_record

    instance, plan = outcome["instance"], outcome["plan"]
    where = _placements(instance, plan)
    slots = [{**slot_record(slot, instance), "box_id": box} for box, slot in sorted(where.items())]
    count = dict((outcome.get("checker") or {}).get("count") or {})
    count.update(boxes=len(slots), total_moves=len(plan.get("moves") or []), forced=0, forced_box_ids=[],
                 boxes_ashore=len(summary.get("boxes_ashore") or []))
    rules = summary.get("confirmed_instructions") or []
    return {"plan_id": plan.get("id"), "label": summary["label"], "status": summary["status"], "level": summary["level"],
            "slots": slots, "counts": count, "meters": _meters(outcome, summary),
            "rule": {"confirmed": rules} if rules else None,
            "source": _file_record(call_dir, run_folder(call_dir) + "/outcome.json", notes=[
                "Final slots are this plan's ship moves replayed over the arrival state; the verdict is the runner's "
                "checker evidence in the attempt's outcome.json, read and not recomputed."])}


def _moves(call_dir: Path, outcome: dict) -> dict:
    from app.planner_data import _moves as build_moves, _plan_slots

    instance, plan = outcome["instance"], outcome["plan"]
    native = SimpleNamespace(moves=[SimpleNamespace(**move) for move in plan.get("moves") or []])
    onboard = SimpleNamespace(placements=[SimpleNamespace(**p) for p in (instance.get("onboard") or {}).get("placements") or []])
    return {"moves": build_moves(native, set(), instance), "plan_id": plan.get("id"),
            "initial_slots": _plan_slots(onboard, instance),
            "source": _file_record(call_dir, run_folder(call_dir) + "/outcome.json", notes=[
                "Every lift of the released plan in native order, including yard relocations and hatch covers."])}


def _checks(call_dir: Path, outcome: dict, summary: dict) -> dict:
    checker = outcome.get("checker") if isinstance(outcome.get("checker"), dict) else {}
    parts = []
    for name, part in (checker.get("parts") or {}).items():
        if not isinstance(part, dict):
            continue
        parts.append({"name": name, "ran": part.get("ran") is True, "error": part.get("error"),
                      "violations": len(part.get("violations") or [])})
    violations = []
    for row in (checker.get("violations") or [])[:100]:
        if isinstance(row, dict):
            violations.append({key: row.get(key) for key in ("rule_id", "rule", "message", "severity", "subject") if key in row})
    receipt = outcome.get("receipt") if isinstance(outcome.get("receipt"), dict) else {}
    versions = ((checker.get("provenance") or {}).get("versions") or {})
    return {"passed": checker.get("passed") is True and summary["status"] == "checked_plan",
            "status": summary["status"], "level": summary["level"], "label": summary["label"],
            "message": summary.get("message"), "broken_rules": summary["broken_rules"],
            "boxes_ashore": summary["boxes_ashore"], "aboard_pct": summary["aboard_pct"], "partial": summary["partial"],
            "active_rules": summary.get("active_rules"), "checker_version": versions.get("checker"),
            "confirmed_instructions": summary["confirmed_instructions"],
            "parts": parts, "parts_ran": sum(p["ran"] for p in parts), "violations": violations,
            "violation_count": len(checker.get("violations") or []),
            "lifts": summary.get("lifts"), "lifts_words": LIFTS_WORDS,
            "receipt_plan_sha256": receipt.get("plan_sha256"),
            "source": {"files": [run_folder(call_dir) + "/outcome.json", run_folder(call_dir) + "/package/receipt.json"],
                       "notes": ["The runner's own checker report for this plan; nothing here is recomputed."]}}


def _package(call_dir: Path) -> dict:
    run = run_folder(call_dir)
    folder = call_dir / run / "package"
    files = []
    for name in PACKAGE_FILES:
        path = folder / name
        if path.is_file():
            data = path.read_bytes()
            files.append({"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                          "url": f"/api/functional/calls/{call_dir.name}/file/{run}/package/{name}"})
    outcome = call_dir / run / "outcome.json"
    return {"call_id": call_dir.name, "files": files, "attempt_folder": run,
            "outcome_url": f"/api/functional/calls/{call_dir.name}/file/{run}/outcome.json" if outcome.is_file() else None,
            "built": bool(files), "label": SYNTHETIC}


def _state(call_dir: Path) -> dict:
    instance = next((name for name in ("assembled-instance.json", "base-instance.json") if (call_dir / name).is_file()), None)
    outcome = _read(call_dir / run_folder(call_dir) / "outcome.json")
    try:
        _, summary = _released(call_dir)
        why, label = None, summary["label"]
    except NoPlan as error:
        summary, why, label = None, str(error), None
    return {"schema": SCHEMA, "call_id": call_dir.name, "label": SYNTHETIC,
            "instance": instance and instance.split("-")[0],
            "outcome_status": outcome.get("status") if isinstance(outcome, dict) else None,
            "plan_released": summary is not None, "plan_label": label, "why": why,
            "names": list(NAMES), "gated": sorted(GATED)}


def payload(call_dir: Path, name: str) -> dict:
    """One planner document for this call; ``NoPlan`` when it can't be built (gated names: no released plan)."""
    call_dir = Path(call_dir)
    if name not in NAMES:
        raise KeyError(name)
    if name == "ship":
        return _ship(call_dir)
    if name == "boxes":
        return _boxes(call_dir)
    if name == "plan-before":
        return _plan_before(call_dir)
    if name == "package":
        return _package(call_dir)
    if name == "state":
        return _state(call_dir)
    outcome, summary = _released(call_dir)
    if name == "plan-after":
        return _plan_after(call_dir, outcome, summary)
    if name == "moves":
        return _moves(call_dir, outcome)
    if name == "meters":
        return _meters(outcome, summary)
    return _checks(call_dir, outcome, summary)
