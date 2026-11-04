"""Deterministic 2D planner data from the v6 hero's persisted evidence.

Slot addresses retain the engine's BAPLIE bay/row/tier identifiers. In
particular, an even bay is an address for a spanning box, not another physical
20-foot cell. No geometry or cargo properties are guessed by this adapter.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Iterable

from stowage.conventions import cells_of_slot, parse_slot, rows_port_to_starboard

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "app/static/planner-data"
NAMES = ("ship", "boxes", "plan-before", "plan-after", "moves", "stress")


def source_record(paths: Iterable[str | Path], *, notes: list[str] | None = None) -> dict:
    """Record the bytes actually read, in portable, deterministic path order."""
    resolved = {Path(path).resolve() if Path(path).is_absolute() else (ROOT / path).resolve()
                for path in paths}
    files = [{"path": path.relative_to(ROOT).as_posix(),
              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
             for path in sorted(resolved, key=lambda p: p.relative_to(ROOT).as_posix())]
    result = {"files": files}
    if notes is not None:
        result["notes"] = list(notes)
    return result


def slot_record(slot_id: str | None, instance: dict | None = None) -> dict | None:
    """Convert a ship address, preserving even-bay spanning-box addresses.

    With an instance, require every occupied physical cell to exist. Yard and
    quay addresses should be passed as None by move adapters.
    """
    if slot_id is None:
        return None
    addr = parse_slot(slot_id)
    if instance is not None:
        cells = {cell["id"] for cell in instance["vessel"]["cells"]}
        if not set(cells_of_slot(slot_id)) <= cells:
            raise ValueError(f"slot {slot_id} is outside the hero's physical cells")
    return {"bay": addr.bay, "row": addr.row, "tier": addr.tier, "deck": addr.on_deck}


def _limits(instance: dict) -> dict:
    """Display the instance's operating limits; this does not issue a verdict."""
    hull = instance["vessel"].get("hull") or {}
    overrides = instance.get("rule_overrides") or {}

    def tightened(value, rule, parameter, *, floor=False):
        override = overrides.get(rule, {}).get(parameter)
        candidates = [n for n in (value, override) if isinstance(n, (int, float))
                      and not isinstance(n, bool) and math.isfinite(n)]
        return (max(candidates) if floor else min(candidates)) if candidates else None

    ship_draught = tightened(hull.get("max_draught_m"), "iscode.loading.draft", "max_draft")
    port_draught = tightened(instance.get("port", {}).get("max_draught_m"), "port.call.draft", "max_draft")
    draughts = [n for n in (ship_draught, port_draught) if n is not None]
    max_list = tightened(hull.get("max_list_rad"), "iscode.loading.list", "max_absolute_list")
    return {
        "draught_m": min(draughts) if draughts else None,
        "ship_draught_m": ship_draught,
        "port_draught_m": port_draught,
        "gm_min_m": tightened(hull.get("min_gm_m"), "iscode.loading.gm", "minimum_gm", floor=True),
        "heel_max_deg": math.degrees(max_list) if max_list is not None else None,
        "trim_min_m": tightened(hull.get("min_trim_m"), "iscode.loading.trim", "minimum", floor=True),
        "trim_max_m": tightened(hull.get("max_trim_m"), "iscode.loading.trim", "maximum"),
    }


def build_ship(instance: dict, *, source: dict, words: str | None = None) -> dict:
    """Build schematic sections for physical bays and valid spanning addresses.

    ``position`` is the mean physical longitudinal coordinate, metres forward
    of AP, matching stage.build_geometry's x_m. Array order retains the source
    bay numbering (fore to aft for this hero), including gaps in its slice.
    ``cells`` prevents a rectangular UI grid from inventing hull-edge slots.
    """
    vessel = instance["vessel"]
    cells = {cell["id"]: cell for cell in vessel["cells"]}
    by_bay: dict[int, dict[tuple[int, int], dict]] = {}
    for cell in cells.values():
        addr = parse_slot(cell["id"])
        by_bay.setdefault(addr.bay, {})[(addr.row, addr.tier)] = cell
    physical_bays = sorted(by_bay)
    # A 40'/45' address exists only where both odd-bay supports exist and can
    # accept a spanning box. This deliberately does not bridge gaps in slices.
    for aft in physical_bays:
        fore = aft + 2
        if fore not in by_bay:
            continue
        spanning = {}
        for coordinate in sorted(by_bay[aft].keys() & by_bay[fore].keys()):
            left, right = by_bay[aft][coordinate], by_bay[fore][coordinate]
            accepts = [size for size in (40, 45)
                       if left.get(f"accepts_{size}") and right.get(f"accepts_{size}")]
            if accepts:
                spanning[coordinate] = {
                    "lcg_m": (left["lcg_m"] + right["lcg_m"]) / 2,
                    "reefer_plug": left.get("reefer_plug", False) or right.get("reefer_plug", False),
                    **{f"accepts_{size}": size in accepts for size in (20, 40, 45)},
                }
        if spanning:
            by_bay[aft + 1] = spanning
    covers = vessel.get("hatch_covers", [])
    hatch_bays = {bay for cover in covers for bay in cover.get("bays", [])}
    bays = []
    for bay, coordinates in sorted(by_bay.items()):
        rows = rows_port_to_starboard(sorted({row for row, tier in coordinates}))
        tiers = sorted({tier for row, tier in coordinates})
        samples = list(coordinates.values())
        bays.append({
            "bay": bay, "label": f"Bay {bay:02d}",
            "position": sum(cell["lcg_m"] for cell in samples) / len(samples),
            "length_ft": 20 if bay % 2 else 40,
            "rows": rows,
            "deck_tiers": [tier for tier in tiers if tier >= 80],
            "hold_tiers": [tier for tier in tiers if tier < 80],
            "hatch": bay in hatch_bays,
            "cells": [{"row": row, "tier": tier,
                       "accepts": [size for size in (20, 40, 45) if cell.get(f"accepts_{size}")],
                       "reefer": bool(cell.get("reefer_plug"))}
                      for row in rows for tier in tiers if (cell := coordinates.get((row, tier))) is not None],
        })
    notes = [
        "Bay identifiers are the source BAPLIE BBB numbers, preserved with slice gaps; array order is numeric bay order (fore to aft on this hero). position is metres forward of the aft perpendicular (AP), not a renumbered index.",
        "Odd bays are physical 20-foot positions. An even bay addresses one 40-foot (or supported 45-foot) box spanning the odd bays either side: bay 02 occupies bays 01 and 03 at the same row and tier. Even-bay sections overlap their odd-bay supports and must not be counted as extra capacity.",
        "Rows follow BAPLIE: 00 is the centreline when present, even rows are port and odd rows starboard, increasing outward. rows arrays run physically port to starboard (for this hero: 06,04,02,01,03,05); transverse metres are positive to port.",
        "Tiers increase bottom to top: hold below 80, deck from 80 upward (82 is this hero's first deck tier). cells lists only actual physical supports or valid paired supports; unused row/tier grid intersections are not slots.",
        "hatch reports the instance's hatch-cover membership. Operating limits are display values from the instance and its tightening overrides; an unknown port draught remains null and no full-checker verdict is inferred.",
    ]
    source = {**source, "notes": [*(source.get("notes") or []), *notes]}
    ship_words = words or vessel.get("name") or vessel["id"]
    stacks = {stack["id"]: stack for stack in vessel.get("stacks", [])}
    hatch_covers = [{"id": cover["id"], "bays": list(cover.get("bays", [])),
                     "rows": rows_port_to_starboard(sorted({stacks[stack_id]["row"]
                         for stack_id in [*cover.get("deck_stack_ids", []), *cover.get("hold_stack_ids", [])]
                         if stack_id in stacks})),
                     "position": cover.get("lcg_m")}
                    for cover in sorted(covers, key=lambda cover: cover["id"])]
    return {"ship": {"words": ship_words, "teu": vessel["nominal_teu"],
                     "synthetic_label": "Synthetic ships and cargo, simulated port"},
            "bays": bays, "hatch_covers": hatch_covers,
            "row_order": "port_to_starboard", "limits": _limits(instance), "source": source}


def build_boxes(instance: dict, *, source: dict) -> dict:
    """Keep all instance containers, including cargo outside this call's load list."""
    from app.stage import build_pods

    boxes = {}
    for container in sorted(instance["containers"], key=lambda row: row["id"]):
        dg = container.get("dg")
        boxes[container["id"]] = {
            "size_ft": container["length_ft"],
            "weight_t": container["gross_mass_kg"] / 1000,
            "pod": container["pod"],
            "dg_class": dg.get("imdg_class") if dg else None,
            "reefer": bool(container.get("reefer")),
            "iso": container.get("iso_type"),
        }
    pods = [{"code": pod["locode"], "name_words": pod["label"], "colour": pod["color"]}
            for pod in build_pods(instance)]
    return {"boxes": boxes, "pods": pods, "source": source}


def persisted_regate(config: dict) -> tuple[dict, Path]:
    """Reuse a recorded full check bound to these inputs; never launch regating.

    This is provenance of a persisted check, not a fresh checker/epoch claim.
    Its path and bytes are included in the output so that distinction survives.
    """
    from app import v6
    from experiments.mvp.pipeline.contract.schema import sha256_json

    expected = {
        "format": v6.REGATE_FORMAT,
        "instance_sha256": config["instance_sha256"],
        "instance_file_sha256": config["file_sha256"],
        "plan_sha256": {name: config[f"{name}_sha256"] for name in ("before", "after")},
        "rule_set_sha256": sha256_json(v6.regate_rules(config)),
        "lattice_entries": v6.scoped_catalogue(config),
        "catalogue_pin": config.get("catalogue_sha256"),
    }
    for path in sorted(v6.REGATE_CACHE.glob("*.json")):
        memo = json.loads(path.read_text())
        if any(memo.get("parts", {}).get(key) != value for key, value in expected.items()):
            continue
        result = memo.get("result", {})
        entries = result.get("entries", {})
        if result.get("summary", {}).get("errors") or not all(
            entries.get(name, {}).get("passed") is True
            and entries[name].get("plan_sha256") == config[f"{name}_sha256"]
            for name in ("before", "after")
        ):
            continue
        return result, path
    raise ValueError("No persisted successful regate matches the hero instance, plans and rule; build no data until v6 has checked them")


def _replay(instance, plan):
    """The stage's physical move engine, without expensive checker/physics work."""
    from stowage.moves import apply_plan

    result = apply_plan(instance, plan)
    if not result.legal:
        raise ValueError(f"Cannot export illegal move sequence {plan.id}: {result.violations}")
    replay = {"frames": [{"state": {"ship": result.final_state.ship.model_dump(mode="json")},
                           "counts": result.count.model_dump(mode="json")}]} 
    return result, replay


def _plan_slots(ship, raw_instance: dict) -> list[dict]:
    return [{**slot_record(p.slot, raw_instance), "box_id": p.container_id}
            for p in sorted(ship.placements, key=lambda p: p.container_id)]


def _departure_meters(instance, plan, regate_path: Path) -> dict:
    """Read the recorded terminal frame bound to this plan's stored regate.

    The current stage epoch can differ from the recording. Preserve the
    recorded epoch and label rather than recomputing or claiming a live check.
    """
    from app import stage_physics

    digests = stage_physics.input_digests(instance, plan)
    path = stage_physics.cache_path(digests)
    cache = json.loads(path.read_text())
    recorded_epoch = json.loads(regate_path.read_text())["parts"]["physics_epoch"]
    physics = cache.get("physics") or {}
    frames = physics.get("frames")
    if (cache.get("version") != stage_physics.PHYSICS_VERSION
            or cache.get("complete") is not True
            or any(cache.get(key) != value for key, value in digests.items())
            or cache.get("instance_id") != instance.id or cache.get("plan_id") != plan.id
            or cache.get("physics_epoch") != recorded_epoch
            or physics.get("n_lifts") != len(plan.moves)
            or not stage_physics._frames_ok(frames, len(plan.moves))):
        raise ValueError(f"Stored physics is not bound to the recorded regate for {plan.id}")
    departure = next((frame for frame in frames if frame["step"] == len(plan.moves)), None)
    measured = ("gm_m", "heel_deg", "trim_m", "draught_aft_m", "draught_fwd_m",
                "max_draught_m", "max_sf_ratio", "max_bm_ratio")
    if (departure is None or departure.get("error") is not None
            or departure.get("criteria") != "departure: sea-going IS Code"
            or not all(stage_physics._number(departure.get(key)) for key in measured)):
        raise ValueError(f"Stored physics has no measured departure frame for {plan.id}")
    source = source_record([path, regate_path], notes=[
        "Recorded departure physics at the final move, with the plan's departure ballast. The stored physics epoch matches the persisted regate; this is not a new check under the current code.",
        "draught_m is the deeper of the recorded aft and forward draughts; hull_pct is 100 times the greater recorded shear-force or bending-moment utilization ratio.",
    ])
    source.update(label="Recorded departure physics", step=departure["step"],
                  instance_sha256=digests["instance_sha256"], plan_sha256=digests["plan_sha256"],
                  physics_epoch=cache["physics_epoch"])
    return {"gm_m": departure["gm_m"], "heel_deg": departure["heel_deg"],
            "trim_m": departure["trim_m"],
            "draught_m": max(departure["draught_aft_m"], departure["draught_fwd_m"]),
            "draught_limit_m": departure["max_draught_m"],
            "hull_pct": 100 * max(departure["max_sf_ratio"], departure["max_bm_ratio"]),
            "source": source}


def _moves(plan, forced: set[str], raw_instance: dict) -> list[dict]:
    """Keep every lift, including yard work and hatch covers, in native order."""
    out = []
    from_ship = {"discharge", "restow_off", "restow_shift"}
    to_ship = {"load", "restow_on", "restow_shift"}
    for index, move in enumerate(plan.moves):
        box = getattr(move, "container_id", None)
        row = {"i": index, "kind": move.kind, "box_id": box,
               "from": slot_record(move.src, raw_instance) if move.kind in from_ship else None,
               "to": slot_record(move.dst, raw_instance) if move.kind in to_ship else None,
               "forced": box in forced}
        if move.kind.startswith("hatch_cover_"):
            row["cover_id"] = move.cover_id
        # Null ship endpoints mean off-ship. Preserve the native yard/quay
        # addresses separately so yard rehandles remain inspectable.
        row["src"] = getattr(move, "src", None)
        row["dst"] = getattr(move, "dst", None)
        out.append(row)
    return out


def _stress(common_paths: list[Path], raw_instance: dict) -> dict:
    from app import v6, v6_sandbox as bench
    from stowage.model import Instance, MovePlan

    payload, demo = v6.sandbox(), bench.read_demo()
    path = bench.source_path(demo)
    source = json.loads(path.read_text())
    instance = Instance.model_validate(source["instance"])
    plan = MovePlan.model_validate(source["plan"])
    applied, _ = _replay(instance, plan)
    origins = {p.container_id: p.slot for p in applied.final_state.ship.placements}
    cases = [{"id": case["kind"], "box_id": case["box"],
              "from": slot_record(origins[case["box"]], source["instance"]),
              "to": slot_record(case["slot"], source["instance"]),
              "fast": case["fast"], "full": case["verdict"],
              "rule": case["verdict"]["outcome"]["rule_id"], "label": case["name"],
              "checked_on": case["checked_on"]}
             for case in payload["cases"]]
    fixture = bench.FIXTURES / "demo.json"
    if not fixture.is_file():
        fixture = bench.FIXTURES / "demo190.json"
    hero_row = bench.configured_hero() or {}
    base_paths = [ROOT / hero_row["base_plan"]] if hero_row.get("base_plan") else []
    hero_boxes = {box["id"]: box for box in raw_instance["containers"]}
    overrides = {}
    for box in source["instance"]["containers"]:
        changed = {key: box.get(key) for key in ("length_ft", "gross_mass_kg", "pod", "dg", "reefer", "iso_type")
                   if box.get(key) != hero_boxes[box["id"]].get(key)}
        if changed:
            overrides[box["id"]] = changed
    return {"cases": cases, "label": payload["label"], "plan_id": plan.id,
            "slots": _plan_slots(applied.final_state.ship, source["instance"]),
            "cargo_overrides": overrides, "challenge_note": payload["challenge_note"],
            "derived": payload["derived"],
            "source": source_record([*common_paths, fixture, path, *base_paths], notes=[
                "Prepared drags start from the sandbox source's departure slots, not the rule-constrained after plan. slots supplies that exact starting layout; cargo_overrides supplies its disclosed declaration changes.",
                bench.CHALLENGE_TAG,
                "fast and full preserve v6's recorded adapters, including deciding rules, diagnostic rows and recording labels; they are not newly run checks.",
            ])}


def build_data() -> dict[str, dict]:
    """Build all six contract documents from the same source selection as v6."""
    from app import v6
    from experiments.mvp.pipeline.contract.schema import sha256_json
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    from stowage.io import load_instance, load_plan

    config = v6.hero()
    if not config.get("after_plan") or not config.get("instruction"):
        raise ValueError("The hero has no stored plan under its selected instruction")
    instance_path = ROOT / config["instance"]
    raw_instance = json.loads(instance_path.read_text())
    instance = load_instance(instance_path)
    if sha256_json(instance.model_dump(mode="json")) != config["instance_sha256"]:
        raise ValueError("The hero instance content differs from its source")
    plans = {name: load_plan(config[f"{name}_plan"]) for name in ("before", "after")}
    for name, plan in plans.items():
        if sha256_json(plan.model_dump(mode="json")) != config[f"{name}_sha256"]:
            raise ValueError(f"The {name} plan differs from its lattice source")
    regate, regate_path = persisted_regate(config)
    facts = v6.change_facts()
    applied, replays = {}, {}
    for name, plan in plans.items():
        applied[name], replays[name] = _replay(instance, plan)
        if len(plan.moves) != regate["entries"][name]["moves"]:
            raise ValueError(f"The {name} timeline length differs from its persisted regate")
    answer = validate_answer(json.dumps(config["instruction"]["rule"]), context_from_instance(instance))
    forced = v6._forced_boxes(instance, answer, replays["before"])
    if forced is None:
        raise ValueError("The hero's rule cannot identify its forced boxes")
    moved = v6.moved_boxes(replays["before"], replays["after"])
    more = sorted(set(moved) - set(forced))
    if (forced != facts["forced_ids"] or len(moved) != facts["moved"]
            or len(more) != facts["rearranged"] or not set(forced) <= set(moved)):
        raise ValueError("The exported placements disagree with the v6 replan facts")

    common = [v6.HERO_FILE, ROOT / config["hero_source"], instance_path,
              v6.LATTICE_STORE / "catalogue.json"]
    if config.get("radio"):
        common.extend(path for path in v6._radio_paths(config["radio"]) if path.is_file())
    named = (config.get("instructions") or {}).get(config["hero_slot"])
    if named and named != v6.SELECTED:
        common.append(ROOT / named)
    plan_paths = [Path(config[f"{name}_plan"]) for name in ("before", "after")]
    stored_paths = [v6.LATTICE_STORE / f"{config['keys'][name]}.json" for name in ("before", "after")]
    plan_sources = source_record([*common, *plan_paths, *stored_paths, ROOT / facts["source"], regate_path], notes=[
        "Final slots are replayed with the same physical move engine used by stage; the full-check verdict comes from the persisted regate bound to these inputs. No fresh checker or physics run is claimed.",
        facts["caveat"],
    ])
    ship = build_ship(raw_instance, source=source_record(common), words=config["ship"])
    # The sliced instance's nominal capacity differs from its parent vessel's
    # display capacity. Keep both explicit rather than giving two values one name.
    _, full_teu = v6._ship_kind(config["envelope"]["numbers"])
    ship["ship"]["section_teu"] = ship["ship"]["teu"]
    ship["ship"]["teu"] = full_teu or ship["ship"]["section_teu"]
    ship["source"]["notes"].append("ship.teu is the parent ship capacity from the hero envelope; section_teu is the sliced instance's nominal capacity. Only the source slice's bays are shown.")
    ship["initial_plan"] = {
        "plan_id": f"{instance.id}.arrival", "label": "Arrival state before this call's cargo moves",
        "slots": _plan_slots(instance.onboard, raw_instance),
        "counts": {"boxes": len(instance.onboard.placements), "total_moves": 0},
        "source": source_record([instance_path], notes=[
            "Exact onboard arrival placements in the displayed decision bays, before either plan executes. This hero's decision bays start empty. Fixed background cargo remains aboard outside those bays and is already included in the stored physics.",
            "Containers in the yard that are outside this call's load list are not onboard background cargo; no slots or background counts are inferred for them.",
        ]),
    }
    result = {"ship": ship, "boxes": build_boxes(raw_instance, source=source_record(common))}
    for name in ("before", "after"):
        count = applied[name].count.model_dump(mode="json")
        count.update(boxes=len(applied[name].final_state.ship.placements), total_moves=len(plans[name].moves),
                     forced=len(forced), moved=len(moved), moved_more=len(more),
                     forced_box_ids=forced, moved_box_ids=moved, moved_more_box_ids=more)
        label = config["before_note"] if name == "before" else f"Stored plan, computed on {facts['plan_date']} · {facts['producer']}"
        meters = _departure_meters(instance, plans[name], regate_path)
        source = {**plan_sources, "files": source_record(
            [ROOT / file["path"] for file in [*plan_sources["files"], *meters["source"]["files"]]]
        )["files"]}
        result[f"plan-{name}"] = {"plan_id": plans[name].id, "label": label,
                                  "slots": _plan_slots(applied[name].final_state.ship, raw_instance),
                                  "counts": count, "meters": meters,
                                  "rule": config["instruction"], "source": source}
    result["moves"] = {"moves": _moves(plans["after"], set(forced), raw_instance),
                       "plan_id": plans["after"].id,
                       "initial_slots": _plan_slots(instance.onboard, raw_instance),
                       "source": {**plan_sources, "notes": [*plan_sources["notes"],
                           "i is zero-based, matching stage.build_steps. All native move kinds remain in order, including yard relocations; null from/to means no ship endpoint. src/dst retain native yard/quay addresses.",
                           "Hatch moves have box_id/from/to null and cover_id identifying ship.hatch_covers; they each count as one move. Playback begins at initial_slots (the call arrival), not plan-before's departure slots.",
                       ]}}
    result["stress"] = _stress(common, raw_instance)
    return result


def write_data(output_dir: str | Path = OUTPUT_DIR) -> dict[str, int]:
    """Write stable compact UTF-8 JSON; return per-file byte sizes."""
    data = build_data()
    encoded = {name: (json.dumps(data[name], ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
               for name in NAMES}
    if sum(map(len, encoded.values())) >= 2_000_000:
        raise ValueError("Planner data exceeds its 2 MB budget")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for name, content in encoded.items():
        path = output / f"{name}.json"
        path.write_bytes(content)
        sizes[path.name] = len(content)
    return sizes


def main() -> None:
    for name, size in write_data().items():
        print(f"{name}: {size:,} bytes")


if __name__ == "__main__":
    main()
