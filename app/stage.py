"""Build the ``stage-v1`` bundle for the demo "stage" page (contract: experiments/mvp/ui/CONTRACT.md).

The stage page animates a crane plan lift by lift. Everything it shows comes from a replay that
``app.backend.build_replay`` already produced: the move engine supplied every reached state and lift count,
and a fresh full checker run supplied the verdict. This module only *re-shapes* that evidence for the browser:

* geometry: ship cells, hatch covers, hull and yard stacks, straight from the instance;
* steps: one entry per crane lift with the engine's running counts;
* rules: the demo rule list (experiments/mvp/rules.json) ticked from the checker report;
* comparison/KPI: StowLM vs the two-step baseline and the lower bound, valued with the impact.py mid scenario.

Nothing here judges legality or physics; a missing number is ``null`` plus a reason.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from stowage.conventions import cells_of_slot, parse_slot

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "experiments/mvp/rules.json"
KPI_PATH = Path(__file__).with_name("kpi_config.json")
PRIZE_PATH = Path(__file__).with_name("prize_config.json")
VERSION = "stage-v1"
SCOPE_LINE = "Simulated port, synthetic ships, demo rule set (rule-freeze off)."

# Display names only; LOCODEs in the corpus label synthetic scenarios, not real terminals.
PORT_LABELS = {"THLCH": "Laem Chabang", "THBKK": "Bangkok", "SGSIN": "Singapore", "MYPKG": "Port Klang",
               "MYTPP": "Tanjung Pelepas", "VNSGN": "Ho Chi Minh City", "VNCMT": "Cai Mep", "HKHKG": "Hong Kong",
               "CNSHA": "Shanghai", "CNSHK": "Shekou", "IDJKT": "Jakarta", "PHMNL": "Manila", "USLAX": "Los Angeles",
               "USLGB": "Long Beach", "JPTYO": "Tokyo", "KRPUS": "Busan", "TWKHH": "Kaohsiung"}
# A fixed palette keyed by rotation order (colour-blind friendly-ish, distinct on dark/light backgrounds).
PALETTE = ["#2b7bb9", "#e07b39", "#3aa17e", "#9b59b6", "#d4a017", "#c0392b", "#17a2b8", "#7f8c8d"]
HOME_COLOR = "#1f3a5f"  # Thai home port (THLCH-like) gets its own deep navy

# docs/headline.md. A tight table cell may say "Two-step baseline" with this on the same panel.
TWO_STEP_LABEL = "A simple two-step planner, modelled from one field expert's description"
PLANNER_LABELS = {"stowlm": "StowLM · Muse Glimmer 30B (LoRA)", "solver.scalable": "Scalable solver",
                  "solver.exact": "Exact solver", "solver.gold_warmstart": "Search software · two-step warm start + LNS",
                  "solver.gold_warmstart.instruction": "Search software replan under the instruction",
                  "baseline.twostep": TWO_STEP_LABEL,
                  "ui-placeholder": "Placeholder · deterministic first-fit", "supplied-plan": "Supplied plan",
                  "glimmer.base-mask": "Base Muse Glimmer 30B + feasibility mask (no fine-tuning)",
                  "muse-spark-zero-shot": "Muse Spark zero-shot (Meta API)"}

FAMILY_INFO = {  # rules.json family -> (slug, label, checker part that evaluates it)
    "IS Code stability": ("stability", "Stability (IS Code)", "stability"),
    "IACS strength": ("strength", "Hull strength (IACS)", "strength"),
    "SOLAS visibility": ("visibility", "Bridge visibility (SOLAS)", "visibility"),
    "lashing": ("lashing", "Lashing (IACS member rules)", "lashing"),
    "IMDG": ("dg", "Dangerous goods (IMDG)", "rules"),
    "expert": ("expert", "Field-expert rules", "rules"),
    "operational": ("operational", "Operations & moves", None),
}
_NONPRODUCTIVE = {"relocate", "restow_off", "restow_on", "restow_shift", "hatch_cover_off", "hatch_cover_on"}


def load_kpi_config(path: Path = KPI_PATH) -> dict:
    return json.loads(Path(path).read_text())


def load_prize(path: Path = PRIZE_PATH) -> dict | None:
    """The size-of-the-prize line (labelled assumptions, not a result); a missing or broken file shows no line."""
    try:
        prize = json.loads(Path(path).read_text())
        return {key: prize[key] for key in ("kind", "text", "low_usd", "mid_usd", "high_usd", "avoided_pct",
                                            "export_boxes", "source")}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def stage_counts(count: dict | None) -> dict | None:
    """Map an engine ``MoveCount`` dump onto the contract's count keys (no arithmetic beyond sums)."""
    if not count:
        return None
    future = count.get("future")  # None = not projected at this step (lite replay keyframes only)
    loads, discharges = count.get("loads", 0), count.get("discharges", 0)
    rehandles, restows, hatch = count.get("relocations", 0), count.get("restow_lifts", 0), count.get("hatch_cover_lifts", 0)
    total = count.get("total_lifts_now", loads + discharges + rehandles + restows + hatch)
    return {"loads": loads, "discharges": discharges, "yard_rehandles": rehandles, "restows": restows,
            "hatch_lifts": hatch, "total_lifts": total,
            "projected_later_restows": None if future is None else sum(f.get("restow_lifts", 0) for f in future),
            "projected_later_hatch_lifts": None if future is None else sum(f.get("hatch_cover_lifts", 0) for f in future),
            "weighted_nonproductive": count.get("weighted_nonproductive")}


# ---------------------------------------------------------------- geometry
def _yard_key_parts(key: str) -> dict:
    match = re.fullmatch(r"(.+)-(\d+)-(\d+)", key)
    if not match:
        return {"block": None, "bay": None, "row": None}
    return {"block": match[1], "bay": int(match[2]), "row": int(match[3])}


def build_geometry(instance: dict, frames: list[dict], yard_keys: dict | None = None) -> dict:
    vessel = instance["vessel"]
    stacks = {s["id"]: s for s in vessel.get("stacks", [])}
    slots = []
    for cell in vessel.get("cells", []):
        addr = parse_slot(cell["id"])
        stack = stacks.get(cell.get("stack_id"), {})
        slots.append({"id": cell["id"], "bay": addr.bay, "row": addr.row, "tier": addr.tier,
                      "on_deck": bool(stack.get("on_deck", addr.tier >= 80)), "stack_id": cell.get("stack_id"),
                      "x_m": cell.get("lcg_m"), "y_m": cell.get("tcg_m"), "z_m": cell.get("vcg_base_m"),
                      "hatch_cover_id": stack.get("hatch_cover_id"), "reefer_plug": bool(cell.get("reefer_plug")),
                      "accepts": [n for n in (20, 40, 45) if cell.get(f"accepts_{n}")]})
    covers = []
    for cover in vessel.get("hatch_covers", []):
        rows = sorted({stacks[s]["row"] for s in [*cover.get("deck_stack_ids", []), *cover.get("hold_stack_ids", [])]
                       if s in stacks})
        covers.append({"id": cover["id"], "bays": cover.get("bays", []), "rows": rows,
                       "x_m": cover.get("lcg_m"), "y_m": cover.get("tcg_m"), "z_m": cover.get("vcg_m"),
                       "deck_stack_ids": cover.get("deck_stack_ids", []),
                       "hold_stack_ids": cover.get("hold_stack_ids", [])})
    hull = vessel.get("hull") or {}
    hull_out = {key: hull.get(key) for key in ("loa_m", "lpp_m", "beam_m", "depth_m", "ap_x_m", "fp_x_m",
                                                "design_draught_m", "max_draught_m")}
    hull_out["stations_x_m"] = [s["x_m"] for s in hull.get("stations", [])] or None
    hull_out["bridge"] = vessel.get("bridge")
    # Every yard stack that holds a box in any reached frame (the state lists non-empty stacks only).
    keys = {}
    for stack in instance.get("yard", {}).get("stacks", []):
        keys[stack["key"]] = stack["length_ft"]
    for frame in frames:
        for stack in (frame.get("state") or {}).get("yard", {}).get("stacks", []):
            keys.setdefault(stack["key"], stack["length_ft"])
    for key, length in (yard_keys or {}).items():  # lite replay: keys reached between stored states
        keys.setdefault(key, length)
    port = instance.get("port", {})
    yard = {"stacks": [{"key": k, **_yard_key_parts(k), "length_ft": keys[k]} for k in sorted(keys)],
            "max_tiers": port.get("yard_max_tiers"),
            "blocks": [{k: b.get(k) for k in ("id", "n_bays", "n_rows", "max_tiers", "x_m", "y_m", "reefer_rows",
                                               "dg_block", "truck_lane_side")}
                       for b in (port.get("yard") or {}).get("blocks", [])]}
    return {"slots": slots, "hatch_covers": covers, "hull": hull_out, "yard": yard}


def build_pods(instance: dict) -> list[dict]:
    calls = (instance.get("rotation") or {}).get("calls", [])
    current = instance.get("call_index", 0)
    home = (instance.get("port") or {}).get("locode")
    seen, pods = set(), []
    used = {c["pod"] for c in instance.get("containers", []) if c.get("pod")}
    ordered = [c for c in calls if c["index"] > current] + [c for c in calls if c["index"] <= current]
    for call in ordered:
        code = call["locode"]
        if code in seen:
            continue
        seen.add(code)
        pods.append({"locode": code, "label": PORT_LABELS.get(code, code) + " (simulated)", "call_index": call["index"]})
    for code in sorted(used - seen):  # POD outside the declared rotation: still coloured, no call index
        pods.append({"locode": code, "label": PORT_LABELS.get(code, code) + " (simulated)", "call_index": None})
    colour = 0
    for pod in pods:
        if pod["locode"] in {home, "THLCH"}:
            pod["color"] = HOME_COLOR
        else:
            pod["color"] = PALETTE[colour % len(PALETTE)]
            colour += 1
    return pods


def build_boxes(instance: dict) -> dict:
    boxes = {}
    for c in instance.get("containers", []):
        dg = c.get("dg")
        boxes[c["id"]] = {"pod": c.get("pod"), "pol": c.get("pol"), "length_ft": c.get("length_ft"),
                          "height_m": c.get("height_m"),
                          "mass_t": round(c["gross_mass_kg"] / 1000, 3) if c.get("gross_mass_kg") is not None else None,
                          "weight_class": c.get("weight_class"), "empty": c.get("empty"), "reefer": c.get("reefer"),
                          "dg": ({"class": dg.get("imdg_class"), "un": dg.get("un_number")} if dg else None),
                          "iso_type": c.get("iso_type"), "oog": c.get("oog") is not None,
                          "in_load_list": c["id"] in set(instance.get("load_list", []))}
    return boxes


def _placements(ship: dict) -> list[dict]:
    return [{"box": p["container_id"], "slot": p["slot"], "cells": list(cells_of_slot(p["slot"]))}
            for p in ship.get("placements", [])]


def build_steps(frames: list[dict]) -> list[dict]:
    steps = []
    for index, frame in enumerate(frames[1:]):
        move = frame["move"] or {}
        kind = move.get("kind")
        src, dst = move.get("src"), move.get("dst")
        if kind == "restow_off":
            dst = "quay"
        elif kind == "restow_on":
            src = "quay"
        elif kind in {"hatch_cover_off", "hatch_cover_on"}:
            src = move.get("cover_id") if kind == "hatch_cover_off" else (move.get("dst") or "quay")
            dst = move.get("dst", "quay") if kind == "hatch_cover_off" else move.get("cover_id")
        slot = dst if kind in {"load", "restow_on", "restow_shift"} else src if kind in {"discharge", "restow_off"} else None
        steps.append({"i": index, "kind": kind, "box": move.get("container_id"), "cover": move.get("cover_id"),
                      "src": src, "dst": dst, "slot_cells": list(cells_of_slot(slot)) if slot else None,
                      "nonproductive": kind in _NONPRODUCTIVE, "counts": stage_counts(frame["counts"])})
    return steps


# ---------------------------------------------------------------- rules
def _part_ran(part: dict | None) -> bool:
    return bool(part and part.get("ran") and not part.get("error"))


def build_rules(checker: dict, instance: dict, final_ship: dict | None, rules_path: Path = RULES_PATH) -> dict:
    """Tick the demo rule list from the bound checker report.

    pass = its checker part ran and the report holds no violation of that rule id; fail = at least one violation;
    not_run = the part did not run (or the report is unbound); n/a = IMDG rules with no dangerous goods aboard.
    """
    rules = json.loads(Path(rules_path).read_text())
    report = checker.get("report") if checker.get("bound") else None
    parts = (report or {}).get("parts", {})
    violations: dict[str, int] = {}
    for v in (report or {}).get("violations", []):
        violations[v["rule_id"]] = violations.get(v["rule_id"], 0) + 1
    boxes = {c["id"]: c for c in instance.get("containers", [])}
    dg_aboard = any(boxes.get(p["container_id"], {}).get("dg") for p in (final_ship or {}).get("placements", []))
    families: dict[str, dict] = {}
    known = set()
    for rule in rules:
        known.add(rule["id"])
        slug, label, part_name = FAMILY_INFO.get(rule["family"], (rule["family"].lower().replace(" ", "_"),
                                                                   rule["family"], None))
        # Operational rules span the move engine and the static rules; they ran when both parts ran.
        ran = (report is not None and (_part_ran(parts.get(part_name)) if part_name else
               _part_ran(parts.get("moves")) and _part_ran(parts.get("rules"))))
        count = violations.get(rule["id"], 0)
        if count:
            status = "fail"
        elif not ran:
            status = "not_run"
        elif slug == "dg" and not dg_aboard:
            status = "n/a"
        else:
            status = "pass"
        family = families.setdefault(slug, {"family": slug, "label": label, "source_family": rule["family"],
                                            "rules": []})
        family["rules"].append({"id": rule["id"], "description": rule["description"], "hardness": rule["hardness"],
                                "source": rule.get("source"), "status": status, "violations": count})
    extra = sorted(set(violations) - known)
    if extra:
        families["other"] = {"family": "other", "label": "Other checker findings", "source_family": "checker",
                             "rules": [{"id": rid, "description": "Reported by the checker (not in the demo list)",
                                        "hardness": "hard", "source": "checker", "status": "fail",
                                        "violations": violations[rid]} for rid in extra]}
    for family in families.values():
        statuses = {r["status"] for r in family["rules"]}
        family["status"] = ("fail" if "fail" in statuses else "not_run" if "not_run" in statuses else
                            "pass" if "pass" in statuses else "n/a")
        family["passed"] = sum(r["status"] == "pass" for r in family["rules"])
        family["total"] = len(family["rules"])
    return {"source": str(Path(rules_path).relative_to(ROOT)) if Path(rules_path).is_relative_to(ROOT) else str(rules_path),
            "bound": bool(report), "families": list(families.values())}


# ---------------------------------------------------------------- securing (lashing)
# Checker lashing metric key -> display label. Each value is the checker's worst utilisation over all stacks for that
# check (load / allowable; 1.0 = the limit; fittings and rods are already against 85 % of SWL).
SECURING_CHECKS = {
    "max_fitting_ratio": "Twistlocks & corner fittings",
    "max_corner_tension_ratio": "Corner lifting (tension)",
    "max_lashing_ratio": "Lashing rods",
    "max_racking_ratio": "Racking, transverse",
    "max_longitudinal_racking_ratio": "Racking, longitudinal",
    "max_corner_post_ratio": "Corner posts (compression)",
    "max_cell_guide_ratio": "Cell guides (below deck)",
}
_PHASE_ORDER = {"departure": 0, "mid_voyage": 1, "arrival": 2}


def build_securing(checker: dict) -> dict:
    """Securing utilisations from the bound checker report's lashing parts (one per sampled loading condition).

    The checker reports, per check and per condition, the worst ratio over all stacks; it does not report a ratio
    per stack, so ``stacks`` is null with a reason. Nothing is recomputed here.
    """
    report = checker.get("report") if checker.get("bound") else None
    parts = (report or {}).get("parts", {})
    conditions, not_run = [], []
    for key, part in parts.items():
        if not (key.startswith("condition:") and key.endswith(":lashing")):
            continue
        if not _part_ran(part):
            not_run.append(f"{key.removeprefix('condition:').removesuffix(':lashing')}: "
                           f"{(part or {}).get('error') or 'part did not run'}")
            continue
        metrics = part.get("metrics") or {}
        phase = next((k.removeprefix("condition.phase: ") for k in metrics if k.startswith("condition.phase: ")), None)
        conditions.append({"id": key.removeprefix("condition:").removesuffix(":lashing"), "phase": phase,
                           "ratios": {k: metrics[k] for k in SECURING_CHECKS if isinstance(metrics.get(k), (int, float))}})
    if not conditions and _part_ran(parts.get("lashing")):  # legacy report without per-condition parts
        metrics = parts["lashing"].get("metrics") or {}
        conditions.append({"id": "departure", "phase": "departure",
                           "ratios": {k: metrics[k] for k in SECURING_CHECKS if isinstance(metrics.get(k), (int, float))}})
    conditions.sort(key=lambda c: (_PHASE_ORDER.get(c["phase"], 3), c["id"]))
    base = {"source": "checker report: parts['condition:<id>:lashing'].metrics (max_*_ratio keys)",
            "limit": 1.0, "method": "IRS container-securing method (synthetic lashing arrangement, D54/D56)",
            "stacks": None, "stacks_reason": "the checker reports the worst ratio per check over all stacks, not per stack"}
    if not conditions:
        return {**base, "available": False, "state": "not_checked",
                "reason": "lashing not evaluated (checker unbound or part did not run)",
                "checks": [], "conditions": [], "max_ratio": None, "passed": None}
    checks = []
    for key, label in SECURING_CHECKS.items():
        values = [(c["ratios"][key], c) for c in conditions if key in c["ratios"]]
        if not values:
            continue
        ratio, worst = max(values, key=lambda v: v[0])
        checks.append({"key": key, "label": label, "ratio": ratio, "limit": 1.0, "passed": ratio <= 1.0 + 1e-10,
                       "governing_phase": worst["phase"],
                       "by_condition": [{"phase": c["phase"], "ratio": c["ratios"].get(key)} for c in conditions]})
    checks.sort(key=lambda c: -c["ratio"])
    lashing = parts.get("lashing") or {}
    found = [v for part in [lashing, *(p for k, p in parts.items() if k.startswith("condition:") and k.endswith(":lashing"))]
             for v in (part or {}).get("violations") or []]
    rules = sorted({str(v.get("rule_id")) for v in found if v.get("rule_id")})
    messages = [v.get("message", "") for v in found]
    condition_rows = [{"id": c["id"], "phase": c["phase"]} for c in conditions]
    if not checks:  # the part ran but stopped before computing ratios (e.g. invalid stack geometry): show why
        return {**base, "available": False, "checks": [], "max_ratio": None, "passed": False,
                "conditions": condition_rows, "violations": len(found), "violation_rules": rules,
                "state": "violation" if found else "not_checked",
                "reason": "lashing part reported no securing ratios" + (f": {messages[0]}" if messages else "")}
    over = [c for c in checks if not c["passed"]]
    missing = ([] if _part_ran(lashing) else [f"lashing: {lashing.get('error') or 'part did not run'}"]) + not_run
    if over:
        state, reason = "over_limit", f"{over[0]['label']} at {over[0]['ratio']:.2f} × the limit"
    elif found:
        state, reason = "violation", messages[0] or None
    elif missing:
        state, reason = "not_checked", "; ".join(missing)
    else:
        state, reason = "ok", None
    return {**base, "available": True, "reason": reason, "state": state, "checks": checks,
            "conditions": condition_rows, "max_ratio": checks[0]["ratio"], "passed": state == "ok",
            "violations": len(found), "violation_rules": rules}


# ---------------------------------------------------------------- comparison & KPI
def lower_bound_entry(instance_model) -> tuple[dict | None, str | None]:
    try:
        from stowage.bounds import lower_bound
        lb = lower_bound(instance_model)  # same call as experiments/mvp/showcase_tools/lower_bounds.py
    except Exception as error:  # bounds need valid weights; report why it is missing
        return None, f"lower bound unavailable: {type(error).__name__}: {error}"
    now = lb.relocations + lb.restow_lifts + lb.hatch_cover_lifts
    productive = len(instance_model.load_list) + len(instance_model.discharge_list)
    return ({"label": "Lower bound (proven floor, not necessarily reachable)", "nonproductive": now,
             "projected_later": lb.future_restow_lifts + lb.future_hatch_cover_lifts,
             "yard_rehandles": lb.relocations, "restows": lb.restow_lifts, "hatch_lifts": lb.hatch_cover_lifts,
             "projected_later_restows": lb.future_restow_lifts,
             "projected_later_hatch_lifts": lb.future_hatch_cover_lifts,
             # Every load/discharge is one productive lift; nonproductive lifts are bounded component-wise.
             "total_lifts": productive + now, "weighted_nonproductive": lb.weighted,
             "note": lb.gap_note,
             "method": "stowage.bounds.lower_bound (same function and defaults as the showcase lower-bounds.json)"},
            None)


def compute_kpi(candidate: dict | None, baseline: dict | None, config: dict | None = None) -> dict | None:
    """Value the baseline-minus-StowLM move difference with the impact.py mid scenario.

    Mirrors ``impact.compare_counts``: yard moves = yard rehandles; quay moves = restow lifts (each via-quay restow
    is already counted as off + on lifts), including projected later-port restows. Hatch-cover lifts (now +
    projected later ports) are priced as one quay-crane move each (D85; ``hatch_*`` in kpi_config.json).
    Negative numbers mean StowLM did *more* work than the baseline. Every value is for this slice's own load list,
    not a whole call; the ``usd_per_call`` / ``thb_per_call`` key names are kept for the bundle contract only.
    """
    if not candidate or not baseline:
        return None
    config = config or load_kpi_config()
    yard = baseline["yard_rehandles"] - candidate["yard_rehandles"]
    quay = ((baseline["restows"] + baseline["projected_later_restows"]) -
            (candidate["restows"] + candidate["projected_later_restows"]))
    total = baseline["total_lifts"] - candidate["total_lifts"]
    later = ((baseline["projected_later_restows"] + baseline["projected_later_hatch_lifts"]) -
             (candidate["projected_later_restows"] + candidate["projected_later_hatch_lifts"]))
    hatch = ((baseline["hatch_lifts"] + baseline["projected_later_hatch_lifts"]) -
             (candidate["hatch_lifts"] + candidate["projected_later_hatch_lifts"]))
    usd = (yard * config["yard_usd_per_move"] + quay * config["quay_usd_per_move"] +
           hatch * config["hatch_usd_per_move"])
    crane_hours = (quay * config["quay_minutes_per_move"] + hatch * config["hatch_minutes_per_move"]) / 60
    return {"scenario": config["scenario"], "moves_saved": total + later, "lifts_saved_now": total,
            "later_lifts_saved": later, "quay_moves_saved": quay, "yard_moves_saved": yard,
            "hatch_moves_saved": hatch,
            "crane_hours": round(crane_hours, 4),
            "yard_equipment_hours": round(yard * config["yard_minutes_per_move"] / 60, 4),
            "berth_hours": round(crane_hours * config["berth_critical_path_fraction"], 4),
            "usd_per_call": round(usd, 2), "thb_per_call": round(usd * config["thb_per_usd"], 2),
            "thb_per_usd": config["thb_per_usd"], "thb_rate_note": config.get("thb_per_usd_note"),
            "basis": ("this slice only (its own load list, not a whole call); valued with the docs/impact.md mid "
                      "scenario; constants in app/kpi_config.json"),
            "scale": "this slice", "prize": load_prize(), "vs": "baseline"}


# ---------------------------------------------------------------- x-ray
def _override_fields(row: dict) -> dict:
    """Per-decision mask evidence recorded in the trace (box/bay/row/tier first tokens); nothing is estimated.

    ``overridden`` = the model's own top-1 token was illegal at one or more decision positions of this action
    (recorded ``decisions[].overridden``); null when the trace has no per-decision evidence.
    """
    decisions = row.get("decisions")
    if not isinstance(decisions, list):
        return {"overridden": None, "decisions": None, "n_actions_scope": row.get("n_actions_scope"),
                "format_override": row.get("format_override")}
    compact = [{"field": d.get("field"), "overridden": d.get("overridden"), "n_allowed": d.get("n_allowed"),
                "n_legal": d.get("n_legal"), "top1": ((d.get("top_k") or [{}])[0]).get("text"),
                "top1_legal": ((d.get("top_k") or [{}])[0]).get("legal")} for d in decisions]
    flags = [d["overridden"] for d in compact if d["overridden"] is not None]
    return {"overridden": any(flags) if flags else None, "decisions": compact,
            "n_actions_scope": row.get("n_actions_scope"), "format_override": row.get("format_override")}


def build_xray(replay: dict, steps: list[dict], *, legal_slots=None) -> dict:
    model = (replay.get("metadata") or {}).get("model_run") or {}
    trace = model.get("trace") or []
    if not trace:
        reason = ("No decoding trace: this plan was not produced by StowLM" if not model else
                  model.get("reason") or "Model run has no trace rows")
        return {"available": False, "reason": reason, "steps": []}
    rows, cursor = [], 0
    for row in trace:
        chosen = row.get("chosen") or {}
        action = chosen.get("action_id") if isinstance(chosen.get("action_id"), dict) else {}
        box, slot = action.get("box"), action.get("slot")
        plan_step = None
        if box and slot:
            # Map the decoded action onto the expanded crane lift that places that box in that slot.
            for i in range(cursor, len(steps)):
                if steps[i]["box"] == box and steps[i]["kind"] in {"load", "restow_on", "restow_shift"} and steps[i]["dst"] == slot:
                    plan_step, cursor = i, i + 1
                    break
        rows.append({"step": row.get("step"), "event": row.get("event"), "n_legal": row.get("n_legal"),
                     "n_actions": row.get("n_actions"),
                     "chosen": {"box": box, "slot": slot, "op": action.get("op"), "text": (chosen.get("text") or "").strip()},
                     "chosen_logprob": row.get("chosen_logprob"),
                     "chosen_logprob_recorded": row.get("chosen_logprob_recorded"),
                     "chosen_logprob_unmasked": row.get("chosen_logprob_unmasked"),
                     "top_k": row.get("top_k") or [], "top_k_kind": "first-token distribution before masking",
                     "legal_slots": (legal_slots or {}).get(row.get("step")), "plan_step": plan_step,
                     **_override_fields(row)})
    # Backtrack / rewind rows (future traces): place each on the timeline at the next decoded action's lift.
    for i, r in enumerate(rows):
        if r["event"] in ("backtrack", "rewind"):
            nxt = next((x["plan_step"] for x in rows[i + 1:] if x["plan_step"] is not None), None)
            r["at_plan_step"] = nxt if nxt is not None else len(steps)
    from app.stage_stowlm import backtracks
    return {"available": True, "reason": None, "label": model.get("label"), "steps": rows,
            "backtracks": (backtracks({**(replay.get("metadata") or {}), "planner_result": replay.get("planner_result")})
                           if model.get("planner_kind") == "stowlm_adapter" else None),
            "legal_slots_note": None if legal_slots else "legal_slots not computed for this trace (null)"}


# ---------------------------------------------------------------- bundle
PLACEHOLDER_SUFFIX = ".ui-placeholder"  # app.backend.make_demo_plan's plan id suffix


def is_placeholder(replay: dict) -> bool:
    return str((replay.get("plan") or {}).get("id") or "").endswith(PLACEHOLDER_SUFFIX)


def _planner_id(metadata: dict, replay: dict | None = None) -> str:
    if replay is not None and is_placeholder(replay):
        return "ui-placeholder"
    model = metadata.get("model_run") or {}
    if model.get("verified") and model.get("planner_kind") == "base_glimmer_mask":
        return "glimmer.base-mask"  # untuned base model under the mask: not StowLM
    from app.stage_stowlm import is_stowlm_run
    if is_stowlm_run(metadata):  # the same test as FINAL_RULE's first clause
        return "stowlm"
    planner = metadata.get("planner") or "supplied-plan"
    if model and (model.get("producer_conflicts") or planner.startswith("stowlm")):
        return "unverified-run"  # a self-claimed or contradicted model run never gets a model's display name
    return planner


def _source_kind(metadata: dict, planner: str) -> str:
    if metadata.get("run_folder") or metadata.get("model_run"):
        return "run-folder"
    if metadata.get("source_kind") == "Live run_planner":
        return "live"
    return "placeholder" if planner == "ui-placeholder" else "supplied"


def build_stage_bundle(replay: dict, *, plan_id: str | None = None, physics: dict | None = None,
                       physics_status: str | None = None, baseline: dict | None = None,
                       baseline_reason: str | None = None, lower_bound: dict | None = None,
                       lower_bound_reason: str | None = None, kpi_config: dict | None = None,
                       legal_slots: dict | None = None) -> dict[str, Any]:
    """Re-shape one validated replay into the stage bundle (see CONTRACT.md).

    ``baseline`` is ``{"label", "counts", "checker_passed"}`` (counts in contract keys) or None with a reason.
    ``physics`` is a block from ``app.stage_physics`` or None while it is pending.
    """
    instance = replay["instance"]
    metadata = replay.get("metadata") or {}
    frames = replay.get("frames") or []
    checker = replay.get("checker") or {}
    planner = _planner_id(metadata, replay)
    placeholder = planner == "ui-placeholder"
    final_ship = frames[-1]["state"]["ship"] if frames else None
    port = instance.get("port") or {}
    explanation = replay.get("explanation") or {}
    meta = {"plan_id": plan_id, "instance_id": instance.get("id"),
            "plan_ref": (replay.get("plan") or {}).get("id"),
            "vessel_label": metadata.get("vessel_label"), "rung": instance.get("size_class"),
            "port": {"locode": port.get("locode"),
                     "label": PORT_LABELS.get(port.get("locode"), port.get("locode") or "?") + " (simulated)"},
            "planner": planner,
            "planner_label": (metadata.get("label") if planner == "stowlm" and metadata.get("label") else None)
                             or metadata.get("planner_label") or PLANNER_LABELS.get(planner, metadata.get("label") or planner),
            "source": _source_kind(metadata, planner),
            "checker_passed": bool(checker.get("passed")),
            # A UI placeholder plan is a render-scale dev aid: never presented as approved, whatever the checker says.
            "departure_approved": bool(metadata.get("departure_approved")) and not placeholder,
            "placeholder": placeholder,
            "repaired": bool(metadata.get("repaired")),
            "notice": ("PLACEHOLDER PLAN (deterministic first-fit, dev only; not a planner output, never approved). "
                       if placeholder else "") + (metadata.get("notice") or ""),
            "validation_status": metadata.get("validation_status"),
            "physical_replay_legal": metadata.get("physical_replay_legal"),
            "lite_replay": bool(metadata.get("lite_replay")),
            "planner_status": metadata.get("planner_status") or (replay.get("planner_result") or {}).get("status"),
            "executed_lifts": metadata.get("executed_moves"), "planned_lifts": metadata.get("planned_moves"),
            "shut_out": len(metadata.get("shut_out") or []) if metadata.get("shut_out") is not None else None,
            "optimality_status": metadata.get("optimality_status"),
            # "= lower bound (optimal)" needs an exact solver's own proof; a plan on the floor alone says "reaches the floor".
            "proved_optimal": planner == "solver.exact" and metadata.get("optimality_status") == "OPTIMAL",
            "slice_boxes": len(instance.get("load_list") or []),
            "scope_line": SCOPE_LINE}
    from app.stage_stowlm import stage_meta
    meta.update(stage_meta({**metadata, "planner_result": replay.get("planner_result") or metadata.get("planner_result")},
                           instance))
    outcome = meta.get("model_outcome")
    from app.stage_instruction import instruction_block, live_replan_refusal, refused_live_outcome, refused_outcome
    instruction = instruction_block(replay)
    live_refusal = live_replan_refusal(replay)
    if live_refusal is not None:
        outcome = meta["model_outcome"] = refused_live_outcome(live_refusal)
    if instruction is not None:  # D144 + D99: labelled in frame in every mode; "StowLM" only under the D170 rule
        meta.update(preview_label=instruction["in_frame_label"], preview_label_all_modes=True)
        if not instruction["presented"]:
            outcome = meta["model_outcome"] = refused_outcome(instruction)
        if not instruction["not_stowlm"]:  # D170: the two-step rival ran without this instruction, so no column compares them
            baseline, baseline_reason = None, "two-step ran without this instruction: different constraints, not compared"
    if outcome is not None:
        # StowLM run without a shippable plan (dead end, finisher time limit, no plan): never replayed or counted as
        # a plan. Only the starting world is drawn under the dead-end card.
        meta["notice"] = f"{outcome['title']}. " + (meta.get("notice") or "")
        frames = frames[:1]
    steps = build_steps(frames) if frames and outcome is None else []
    final_counts = stage_counts(frames[-1]["counts"]) if frames and outcome is None else None
    stowlm_entry = {"label": instruction["racer_label"] if instruction is not None else meta["planner_label"], "counts": final_counts, "checker_passed": meta["checker_passed"],
                    "planner": planner}
    from app.comparison import racer_evidence
    primary = racer_evidence(replay) if outcome is None and replay.get("plan") and frames else None
    if primary is not None:
        stowlm_entry.update(primary)
    reasons = {"baseline": baseline_reason if baseline is None else None,
               "lower_bound": lower_bound_reason if lower_bound is None else None}
    if outcome is not None:
        reasons["stowlm"] = outcome["title"]
    recorded = meta.get("recorded_twostep")
    if baseline is None and recorded and recorded.get("weighted_nonproductive") is None:
        reasons["baseline"] = (f"{reasons['baseline'] or 'no replayable two-step plan'}; recorded two-step run: "
                               f"status {recorded.get('plan_status')}, no score ({recorded['source']})")
    if baseline is None and recorded and recorded.get("weighted_nonproductive") is not None:
        reasons["baseline"] = (f"{reasons['baseline'] or 'no replayable two-step plan'}; recorded two-step score "
                               f"{recorded['weighted_nonproductive']:g} weighted lifts "
                               f"({'passed' if recorded.get('passed') else 'not passed'}) in {recorded['source']}, "
                               "not replayed or re-checked here")
    kpi, kpi_reason = None, None
    if placeholder:
        kpi_reason = "no KPI for a placeholder plan"
    elif outcome is not None:
        kpi_reason = ("no KPI: this replan is not presented" if instruction is not None or live_refusal else
                      "no KPI: StowLM produced no shippable plan on this run")
    elif baseline is None:
        kpi_reason = "no baseline plan for this instance"
    elif not (meta["checker_passed"] and baseline.get("checker_passed")):
        kpi_reason = "KPI is shown only when both plans pass the full checker"
    elif primary is None or not primary["eligible"]:
        kpi_reason = "KPI not priced; comparison is diagnostic only. This plan: " + "; ".join(
            (primary or {}).get("ineligible_reasons") or ["no shipping evidence"])
    elif baseline.get("eligible") is not True:
        kpi_reason = "KPI not priced; comparison is diagnostic only. Baseline: " + "; ".join(
            baseline.get("ineligible_reasons") or ["no shipping evidence"])
    else:
        kpi = compute_kpi(final_counts, baseline.get("counts"), kpi_config)
    reasons["kpi"] = kpi_reason
    diagnostic = kpi is None and baseline is not None and final_counts is not None
    initial_state = frames[0]["state"] if frames else None
    return {
        "version": VERSION,
        "meta": meta,
        "geometry": build_geometry(instance, frames, replay.get("yard_keys")),
        "pods": build_pods(instance),
        "boxes": build_boxes(instance),
        "initial": ({"ship": _placements(initial_state["ship"]),
                     "yard": [{"key": s["key"], "boxes": list(s["boxes"])} for s in initial_state["yard"]["stacks"]],
                     "covers_off": list(initial_state["ship"].get("covers_off", [])),
                     "quay_buffer": list(initial_state["yard"].get("quay_buffer", []))} if initial_state else None),
        "steps": steps,
        "physics": physics,
        "physics_status": physics_status or ("ready" if physics else "pending"),
        "comparison": {"stowlm": stowlm_entry, "baseline": baseline, "lower_bound": lower_bound, "reasons": reasons,
                       "diagnostic": diagnostic},
        "rules": build_rules(checker, instance, final_ship),
        "securing": build_securing(checker),
        "instruction": instruction,
        "xray": (build_xray(replay, steps, legal_slots=legal_slots) if outcome is None else
                 {"available": False, "steps": [],
                  "reason": f"{outcome['title']}: no plan to x-ray (the decoded prefix is not replayed)"}),
        "explanation": {"source": ("spark-cached" if explanation.get("source") == "spark" else
                                   "template" if explanation.get("source") == "template" else "none"),
                        "text": explanation.get("text"), "model": explanation.get("model"), "per_box": {},
                        "live_available": False,
                        "live_reason": "Live Muse Spark calls are off in the stage view (offline demo; cached text only)"},
        "kpi": kpi,
    }
