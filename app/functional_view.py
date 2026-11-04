"""Bay-plan data for the Functional app page (CONTRACT.md, "app/functional_view.py").

``plan_view(call_dir)`` reads ``run/outcome.json`` written by the frozen runner
(``runner.run_packet(out_dir=call_dir/"run")``) and returns what the page draws.
It never draws a plan the checker didn't release (honesty rule 2):

* ``status == "checked_plan"`` and ``passed is True``: the full checker passed (L0)
  and every confirmed instruction was rechecked on the returned plan.
* ``status == "relaxed_plan"`` at L1-L5: drawn with its label from docs/headline.md
  and docs/rule-tiers.md on top, every broken rule named, boxes ashore counted.
* anything else raises ``NoPlan`` with the runner's message and the standing line
  "No checked plan found. That isn't a proof that none exists."

The placements come from replaying the plan's ship moves over the instance's
arrival placements, by slot address only. No engine module is imported here: the
server process stays light and the checker's verdict is read, never recomputed.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIERS_TOML = ROOT / "experiments/mvp/frontier/lab/c5/leader/tiers.toml"
CATALOGUE_TOML = ROOT / "src/stowage/rules/catalogue.toml"

SCHEMA = "stowlm.functional-plan-view/1"
SYNTHETIC = "Synthetic ship and cargo, simulated port"
NO_PLAN = "No checked plan found. That isn't a proof that none exists."
L0_LABEL = "Passes the full checker"
LIFTS_WORDS = "extra crane lifts (rehandles now and projected at later ports, hatch-cover lifts; D15)"
# docs/rule-tiers.md "The ladder"; docs/headline.md "Relaxed plans".
GIVES_UP = {1: "preferences", 2: "stowage practice", 3: "bookings or leaves boxes ashore"}
L4_LABEL = "Breaks a class or statutory limit: needs a person's sign-off"
L5_LABEL = "Not safe to sail: shown only to say what would have to change"
# Okabe-Ito, the planner's POD palette (planner.css --pl-pod-*).
POD_COLOURS = ("#0072b2", "#e69f00", "#009e73", "#cc79a7", "#56b4e9", "#d55e00", "#f0e442")
_SLOT = re.compile(r"^(\d{3})(\d{2})(\d{2})$")
TO_SHIP = {"load", "restow_on", "restow_shift"}
FROM_SHIP = {"discharge", "restow_off", "restow_shift"}


class NoPlan(LookupError):
    """No plan may be drawn for this call; ``str(error)`` is the plain reason."""


def relaxed_label(level: int, *, aboard_pct: float | None = None, partial: bool = False) -> str:
    """The on-screen name of a ladder level (L1-L5), with "partial, N % aboard" when boxes stay ashore."""
    if level in GIVES_UP:
        label = f"Relaxed plan: gives up {GIVES_UP[level]}"
    elif level == 4:
        label = L4_LABEL
    elif level == 5:
        label = L5_LABEL
    else:
        raise ValueError(f"not a relaxed ladder level: {level!r}")
    if partial:
        pct = "?" if aboard_pct is None else _pct(aboard_pct)
        label += f"; partial, {pct} % aboard"
    return label


def _pct(value: float) -> str:
    text = f"{float(value):.1f}"
    return text[:-2] if text.endswith(".0") else text


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise NoPlan("The engine hasn't written an outcome for this call yet.") from None
    except (OSError, ValueError):
        raise NoPlan("The outcome file for this call can't be read. " + NO_PLAN) from None
    if not isinstance(data, dict):
        raise NoPlan("The outcome file for this call isn't a runner outcome. " + NO_PLAN)
    return data


def _rule_index() -> dict[str, dict]:
    """Rule id -> {"title", "tier"}, read from the frozen tier table and catalogue (read-only)."""
    index: dict[str, dict] = {}
    try:
        catalogue = tomllib.loads(CATALOGUE_TOML.read_text())
        for rule in catalogue.get("rules", []):
            if isinstance(rule, dict) and isinstance(rule.get("id"), str):
                index.setdefault(rule["id"], {})["title"] = rule.get("title")
    except (OSError, ValueError):
        pass
    try:
        tiers = tomllib.loads(TIERS_TOML.read_text()).get("tiers", {})
        for tier, body in tiers.items():
            for rule_id in (body or {}).get("rules", []):
                index.setdefault(rule_id, {})["tier"] = tier
    except (OSError, ValueError):
        pass
    return index


def _broken(rule_ids: list, index: dict) -> list[dict]:
    rows = []
    for rule_id in rule_ids:
        rule_id = rule_id if isinstance(rule_id, str) else json.dumps(rule_id, sort_keys=True)
        known = index.get(rule_id, {})
        rows.append({"id": rule_id, "title": known.get("title") or rule_id,
                     # tiers.toml: "A rule ID missing from this file counts as T0 (fail closed)."
                     "tier": known.get("tier") or "T0"})
    return rows


def _parse_slot(slot: str) -> tuple[int, int, int] | None:
    match = _SLOT.match(slot) if isinstance(slot, str) else None
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def _rows_port_to_starboard(rows) -> list[int]:
    port = sorted((r for r in rows if r and r % 2 == 0), reverse=True)
    starboard = sorted(r for r in rows if r % 2 == 1)
    return port + ([0] if 0 in rows else []) + starboard


def _placements(instance: dict, plan: dict) -> dict[str, str]:
    """Container id -> ship slot after this call's moves, from arrival placements and ship moves only."""
    onboard = (instance.get("onboard") or {}).get("placements") or []
    where = {p["container_id"]: p["slot"] for p in onboard
             if isinstance(p, dict) and p.get("container_id") and p.get("slot")}
    for move in plan.get("moves") or []:
        kind, box = move.get("kind"), move.get("container_id")
        if not box:
            continue
        if kind in FROM_SHIP:
            where.pop(box, None)
        if kind in TO_SHIP and move.get("dst"):
            where[box] = move["dst"]
    return where


def _pods(instance: dict, used: set[str]) -> list[dict]:
    calls = (instance.get("rotation") or {}).get("calls") or []
    order: list[str] = []
    for call in calls[1:] if calls else []:
        code = call.get("locode")
        if code and code not in order:
            order.append(code)
    for code in sorted(used):
        if code not in order:
            order.append(code)
    return [{"code": code, "colour": POD_COLOURS[i % len(POD_COLOURS)], "order": i + 1}
            for i, code in enumerate(order) if code in used]


def _bays(instance: dict, where: dict[str, str]) -> list[dict]:
    """Physical (odd) bays as row x tier grids, each holding the boxes that sit on its cells."""
    cells: dict[int, set[tuple[int, int]]] = {}
    reefer_cells: set[tuple[int, int, int]] = set()
    for cell in (instance.get("vessel") or {}).get("cells") or []:
        parsed = _parse_slot(cell.get("id"))
        if parsed is None:
            continue
        bay, row, tier = parsed
        cells.setdefault(bay, set()).add((row, tier))
        if cell.get("reefer_plug"):
            reefer_cells.add(parsed)
    covers = (instance.get("vessel") or {}).get("hatch_covers") or []
    hatch_bays = {bay for cover in covers for bay in cover.get("bays", []) or []}
    containers = {c.get("id"): c for c in instance.get("containers") or [] if isinstance(c, dict)}
    boxes: dict[int, list[dict]] = {bay: [] for bay in cells}
    for box_id, slot in sorted(where.items()):
        parsed = _parse_slot(slot)
        if parsed is None:
            continue
        bay, row, tier = parsed
        container = containers.get(box_id, {})
        dg = container.get("dg") or None
        # An even bay addresses a 40'/45' box spanning the odd bays either side (BAPLIE).
        homes = [bay] if bay % 2 else [bay - 1, bay + 1]
        for home in homes:
            if home not in boxes:
                continue
            boxes[home].append({
                "id": box_id, "slot": slot, "row": row, "tier": tier,
                "size_ft": container.get("length_ft"),
                "span_bay": None if bay % 2 else bay,
                "pod": container.get("pod"),
                "weight_t": round(float(container["gross_mass_kg"]) / 1000, 2)
                if isinstance(container.get("gross_mass_kg"), (int, float)) else None,
                "dg_class": (dg.get("imdg_class") or dg.get("class")) if isinstance(dg, dict) else None,
                "un": dg.get("un_number") if isinstance(dg, dict) else None,
                "reefer": bool(container.get("reefer")),
                "empty": bool(container.get("empty")),
            })
    out = []
    for bay in sorted(cells):
        coords = cells[bay]
        tiers = sorted({tier for _, tier in coords})
        out.append({
            "bay": bay, "label": f"Bay {bay:02d}",
            "rows": _rows_port_to_starboard(sorted({row for row, _ in coords})),
            "deck_tiers": sorted((t for t in tiers if t >= 80), reverse=True),
            "hold_tiers": sorted((t for t in tiers if t < 80), reverse=True),
            "cells": [{"row": row, "tier": tier, "reefer": (bay, row, tier) in reefer_cells}
                      for row, tier in sorted(coords)],
            "hatch": bay in hatch_bays,
            "boxes": boxes[bay],
        })
    return out


def _confirmed_rules(outcome: dict) -> list[dict]:
    rules = outcome.get("rules")
    if not isinstance(rules, list):
        rules = (outcome.get("evidence") or {}).get("runner_rules") or []
    # The runner's post-checker rows (runner_rules from _audit) carry no "confirmed" key: the engine only runs once every
    # voyage reading is confirmed. So a row counts as confirmed unless it says otherwise (fail closed: it must recheck).
    return [r for r in rules if isinstance(r, dict) and r.get("confirmed", True) is not False]


def run_folder(call_dir: Path) -> str:
    """The latest engine attempt's folder, relative to the call: ``runs/<n>`` (plan again on the same call), else ``run``.

    The latest attempt is the highest-numbered one, finished or not: while it runs, nothing from an earlier attempt is
    drawn as if it were current."""
    runs = Path(call_dir) / "runs"
    numbers = sorted(int(p.name) for p in runs.iterdir() if p.is_dir() and p.name.isdigit()) if runs.is_dir() else []
    return f"runs/{numbers[-1]}" if numbers else "run"


def released(call_dir: Path) -> tuple[dict, dict]:
    """The gate every drawn plan passes: ``(outcome, summary)`` for an L0 pass or a named relaxed plan, else ``NoPlan``.

    ``summary`` carries the verdict fields of ``plan_view`` (label, level, broken rules, boxes ashore, lifts, rule
    coverage) without the bay drawing; ``app/functional_planner.py`` builds the planner payloads on the same gate."""
    call_dir = Path(call_dir)
    run = run_folder(call_dir)
    outcome = _read_json(call_dir / run / "outcome.json")
    status = outcome.get("status")
    message = str(outcome.get("message") or "").strip()
    plan, instance = outcome.get("plan"), outcome.get("instance")
    evidence = outcome.get("evidence") if isinstance(outcome.get("evidence"), dict) else {}
    checker = outcome.get("checker") if isinstance(outcome.get("checker"), dict) else evidence.get("checker") or {}
    level = outcome.get("level", evidence.get("level"))
    confirmed = _confirmed_rules(outcome)

    def refuse(reason: str = "") -> NoPlan:
        text = " ".join(part for part in (reason, message) if part)
        return NoPlan((text + " " + NO_PLAN).strip())

    if not isinstance(plan, dict) or not isinstance(instance, dict):
        raise refuse()
    if status == "checked_plan":
        if outcome.get("passed") is not True or checker.get("passed") is not True or level not in (0, None):
            raise refuse("The outcome says checked_plan but the full checker didn't pass it.")
        failed = [r for r in confirmed if (r.get("recheck") or {}).get("passed") is not True]
        if failed or evidence.get("instruction_recheck_passed") is False:
            raise refuse("A confirmed instruction failed its recheck on the returned plan.")
        level, relaxed = 0, False
    elif status == "relaxed_plan":
        if outcome.get("passed") is True or not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 5:
            raise refuse("The relaxed outcome has no ladder level from L1 to L5.")
        relaxed = True
    else:
        raise refuse()

    index = _rule_index()
    broken = _broken(list(outcome.get("broken_rules") or evidence.get("broken_rules") or []), index)
    if relaxed and not broken:
        raise refuse("The relaxed outcome doesn't name the rules it breaks.")
    ashore = [b for b in outcome.get("boxes_ashore") or evidence.get("boxes_ashore") or [] if isinstance(b, str)]
    load_list = [b for b in instance.get("load_list") or [] if isinstance(b, str)]
    total = evidence.get("boxes_total") if isinstance(evidence.get("boxes_total"), int) else len(load_list)
    completion = evidence.get("completion") if isinstance(evidence.get("completion"), dict) else {}
    aboard_pct = completion.get("boxes_aboard_percent")
    if not isinstance(aboard_pct, (int, float)):
        aboard_pct = 100.0 * (total - len(ashore)) / total if total else None
    partial = bool(ashore)
    if relaxed:
        label = relaxed_label(level, aboard_pct=aboard_pct, partial=partial)
    else:
        label = L0_LABEL

    vessel = instance.get("vessel") or {}
    receipt = outcome.get("receipt") if isinstance(outcome.get("receipt"), dict) else {}
    lifts = outcome.get("lifts", evidence.get("lifts"))
    # Rule coverage comes from the package receipt (rule_coverage.py), never a fixed count.
    try:
        package_receipt = json.loads((call_dir / run / "package" / "receipt.json").read_text())
    except (OSError, ValueError):
        package_receipt = {}
    coverage = (package_receipt.get("checker") if isinstance(package_receipt, dict) else None) or receipt.get("checker") or {}
    active = coverage.get("active_rules") if isinstance(coverage, dict) else None
    active = len(active) if isinstance(active, list) else None
    return outcome, {
        "schema": SCHEMA,
        "call_id": call_dir.name,
        "synthetic_label": SYNTHETIC,
        "status": status, "passed": status == "checked_plan", "relaxed": relaxed,
        "level": level, "label": label,
        "full_checker_pass": status == "checked_plan",
        "message": message,
        "broken_rules": broken,
        "boxes_total": total, "boxes_ashore": ashore, "boxes_aboard": total - len(ashore) if total else None,
        "aboard_pct": None if aboard_pct is None else round(float(aboard_pct), 1), "partial": partial,
        "lifts": lifts, "lifts_words": LIFTS_WORDS,
        "active_rules": active,
        "confirmed_instructions": [{"sentence": r.get("sentence"),
                                    "rechecked": (r.get("recheck") or {}).get("passed") is True}
                                   for r in confirmed],
        "first_checked_wall_s": outcome.get("first_checked_wall_s"),
        "wall_s": outcome.get("wall_s"),
        "instance_sha256": outcome.get("instance_sha256"),
        "receipt_plan_sha256": receipt.get("plan_sha256"),
        "ship": {"id": vessel.get("id"), "name": vessel.get("name") or vessel.get("id"), "teu": vessel.get("nominal_teu")},
    }


def plan_view(call_dir: Path) -> dict:
    """Bay-plan data for one call, or ``NoPlan`` when no plan may be drawn."""
    outcome, view = released(call_dir)
    instance, plan = outcome["instance"], outcome["plan"]
    where = _placements(instance, plan)
    containers = {c.get("id"): c for c in instance.get("containers") or [] if isinstance(c, dict)}
    used_pods = {containers.get(b, {}).get("pod") for b in where} - {None}
    return {
        **view,
        "pods": _pods(instance, used_pods),
        "bays": _bays(instance, where),
        "boxes_drawn": len(where),
        "notes": [
            "Bays are physical 20-foot positions, fore to aft by BAPLIE number; rows run port to starboard; tiers bottom to top (hold below 80, deck from 80).",
            "A 40-foot box is drawn in both odd bays it spans and counted once.",
            "Placements are this plan's ship moves replayed over the arrival state; the verdict is the runner's checker evidence, read and not recomputed.",
        ],
    }
