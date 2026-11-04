"""The 2D view is a compact projection of the same recorded hero as v6.

These tests read the persisted plans and recorded verdicts. They never start the
server, launch regating, or invoke a live checker.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import planner_data, v6
from stowage.conventions import cells_of_slot

ROOT = Path(__file__).resolve().parents[2]
NAMES = ("ship", "boxes", "plan-before", "plan-after", "moves", "stress")


@pytest.fixture(scope="module")
def data():
    # Runtime projections ship independently of the private development caches.
    # Missing or malformed public documents must fail instead of being skipped.
    return {name: json.loads((ROOT / "app/static/planner-data" / f"{name}.json").read_text())
            for name in NAMES}


def _require_recorded_sources(*documents):
    """Skip only exact withheld recordings declared by the shipped projection."""
    paths = {record["path"] for document in documents for record in document["source"]["files"]}
    withheld = {path for path in paths
                if Path(path).parent == Path("experiments/mvp/ui/cache/regate")
                or (Path(path).parent == Path("experiments/mvp/ui/cache")
                    and path.endswith(".physics.json"))}
    assert withheld, "recording-dependent checks must name their source files"
    missing = sorted(path for path in withheld if not (ROOT / path).is_file())
    if missing:
        pytest.skip("Development recorded-input check requires " + ", ".join(missing)
                    + "; persisted hero regate/physics runtime caches are deliberately not shipped "
                    "in the public copy; prepared planner JSON is shipped")


@pytest.fixture(scope="module")
def hero_sources():
    config = v6.hero()
    instance = json.loads((ROOT / config["instance"]).read_text())
    plans = {name: json.loads(Path(config[f"{name}_plan"]).read_text()) for name in ("before", "after")}
    return config, instance, plans


def _coordinates(slot_id):
    return {"bay": int(slot_id[:3]), "row": int(slot_id[3:5]), "tier": int(slot_id[5:])}


def _is_ship_slot(slot_id, cells):
    if not isinstance(slot_id, str) or len(slot_id) != 7 or not slot_id.isdigit():
        return False
    return set(cells_of_slot(slot_id)) <= cells


def _final_slots(instance, plan):
    """Fold physical destinations independently of the builder/stage timeline."""
    cells = {cell["id"] for cell in instance["vessel"]["cells"]}
    slots = {placement["container_id"]: placement["slot"] for placement in instance["onboard"]["placements"]}
    for move in plan["moves"]:
        box = move.get("container_id")
        if box is None:
            continue
        if _is_ship_slot(move.get("dst"), cells):
            slots[box] = move["dst"]
        elif _is_ship_slot(move.get("src"), cells):
            slots.pop(box, None)
    return slots


def _persisted_regate(config):
    """Find the app's recorded full-check result bound to these exact plans."""
    matches = []
    for path in sorted(v6.REGATE_CACHE.glob("*.json")):
        result = json.loads(path.read_text()).get("result", {})
        entries = result.get("entries", {})
        if all(entries.get(name, {}).get("plan_sha256") == config[f"{name}_sha256"]
               and entries[name].get("passed") is True for name in ("before", "after")):
            matches.append(result)
    assert matches, "hero requires its persisted regate; do not start heavy computation in this test"
    return matches[-1]


def test_counts_match_the_actual_v6_payload(data, hero_sources, monkeypatch):
    _require_recorded_sources(data["plan-before"], data["plan-after"])
    config, _, plans = hero_sources
    regate = _persisted_regate(config)
    facts = v6.change_facts()  # validates ship, rule, plan hashes and both lattice entries
    ids = {name: plans[name]["id"] for name in plans}
    # Only the launch gate is supplied from its persisted result. The real v6
    # replan builder still resolves and binds its own facts and recorded reading.
    gate = {"status": "shown", "before": {**regate["entries"]["before"], "plan_id": ids["before"]},
            "after": {**regate["entries"]["after"], "plan_id": ids["after"]},
            "forced_boxes": facts["forced_ids"], "boxes_moved": facts["moved"], "gauges": regate.get("gauges")}
    monkeypatch.setattr(v6.HERO, "gate", lambda service: gate)
    service = SimpleNamespace(catalog=lambda: {"plans": [{"id": ident, "label": f"v6:{name}"} for name, ident in ids.items()]})
    payload = v6.replan(service, recorded=True)
    assert payload["gate"]["status"] == "shown" and payload["facts"]
    counts = data["plan-after"]["counts"]
    assert counts["forced"] == payload["facts"]["forced"] == len(payload["gate"]["forced_boxes"])
    assert counts["moved_more"] == payload["facts"]["rearranged"]
    assert counts["total_moves"] == payload["gate"]["after"]["moves"] == len(plans["after"]["moves"])
    assert len(data["moves"]["moves"]) == counts["total_moves"]
    assert {row["box_id"] for row in data["moves"]["moves"] if row["forced"]} == set(payload["facts"]["forced_ids"])
    assert len(data["stress"]["cases"]) == len(v6.sandbox()["cases"])


def test_bay_geometry_and_final_plans_preserve_native_slots(data, hero_sources):
    _, instance, plans = hero_sources
    ship = data["ship"]
    assert ship["row_order"] == "port_to_starboard"
    assert ship["source"]["notes"]
    bays = {bay["bay"]: bay for bay in ship["bays"]}
    assert len(bays) == len(ship["bays"])
    for cell in instance["vessel"]["cells"]:
        slot = _coordinates(cell["id"])
        bay = bays[slot["bay"]]
        assert slot["row"] in bay["rows"]
        assert slot["tier"] in bay["deck_tiers"] + bay["hold_tiers"]
    # Row arrays must follow the transverse geometry, not numerical row order.
    transverse = {}
    for cell in instance["vessel"]["cells"]:
        transverse.setdefault(_coordinates(cell["id"])["row"], []).append(cell["tcg_m"])
    row_position = {row: sum(values) / len(values) for row, values in transverse.items()}
    physical_bays = {_coordinates(cell["id"])["bay"] for cell in instance["vessel"]["cells"]}
    for number, bay in bays.items():
        assert bay["rows"] == sorted(bay["rows"], key=lambda row: row_position[row], reverse=True)
        if number not in physical_bays:
            assert number - 1 in physical_bays and number + 1 in physical_bays
            assert bay["length_ft"] == 40
    covers = {cover["id"]: cover for cover in instance["vessel"]["hatch_covers"]}
    assert {cover["id"] for cover in ship["hatch_covers"]} == set(covers)
    for cover in ship["hatch_covers"]:
        assert cover["bays"] == covers[cover["id"]]["bays"]
    for number, bay in bays.items():
        assert bay["hatch"] is any(number in cover["bays"] for cover in covers.values())
    for name, plan in plans.items():
        rows = data[f"plan-{name}"]["slots"]
        assert len(rows) == len({row["box_id"] for row in rows})
        actual = {row["box_id"]: {key: row[key] for key in ("bay", "row", "tier")} for row in rows}
        assert actual == {box: _coordinates(slot) for box, slot in _final_slots(instance, plan).items()}
        assert all(isinstance(row["deck"], bool) for row in rows)
        assert data[f"plan-{name}"]["plan_id"] == plan["id"]
        assert data[f"plan-{name}"]["label"]


def test_every_cargo_declaration_is_preserved(data, hero_sources):
    _, instance, _ = hero_sources
    boxes = data["boxes"]["boxes"]
    assert set(boxes) == {box["id"] for box in instance["containers"]}
    for original in instance["containers"]:
        row = boxes[original["id"]]
        assert row["size_ft"] == original["length_ft"]
        assert row["weight_t"] == pytest.approx(original["gross_mass_kg"] / 1000)
        assert row["pod"] == original["pod"]
        assert row["dg_class"] == (original["dg"]["imdg_class"] if original["dg"] else None)
        assert row["reefer"] is original["reefer"]
        assert row["iso"] == original["iso_type"]
    assert {pod["code"] for pod in data["boxes"]["pods"]} >= {box["pod"] for box in boxes.values()}


def test_moves_keep_recorded_order_and_slots(data, hero_sources):
    _, instance, plans = hero_sources
    cells = {cell["id"] for cell in instance["vessel"]["cells"]}
    rows = data["moves"]["moves"]
    for index, (row, original) in enumerate(zip(rows, plans["after"]["moves"])):
        assert row["i"] == index
        assert row["kind"] == original["kind"]
        assert row["box_id"] == original.get("container_id")
        assert isinstance(row["forced"], bool)
        if original["kind"].startswith("hatch_cover_"):
            assert row["cover_id"] == original["cover_id"]
        for field, source in (("from", "src"), ("to", "dst")):
            if _is_ship_slot(original.get(source), cells):
                assert {key: row[field][key] for key in ("bay", "row", "tier")} == _coordinates(original[source])
            elif original["kind"].startswith("hatch_cover_"):
                # A hatch move can use its cover's representative ship cell.
                assert row[field] is None or all(key in row[field] for key in ("bay", "row", "tier"))
            else:
                assert row[field] is None


def test_prepared_drags_keep_the_v6_recorded_verdicts(data, hero_sources):
    _, instance, plans = hero_sources
    original_slots = _final_slots(instance, plans["before"])
    expected = {case["kind"]: case for case in v6.sandbox()["cases"]}
    actual = {case["id"]: case for case in data["stress"]["cases"]}
    assert set(actual) == set(expected)
    for ident, case in expected.items():
        row = actual[ident]
        assert row["box_id"] == case["box"]
        assert {key: row["to"][key] for key in ("bay", "row", "tier")} == _coordinates(case["slot"])
        assert {key: row["from"][key] for key in ("bay", "row", "tier")} == _coordinates(original_slots[case["box"]])
        assert row["label"]
        assert row["fast"] == case["fast"]
        assert row["full"] == case["verdict"]
        assert row["rule"] == case["verdict"]["outcome"]["rule_id"]


def test_all_sources_are_hashed_and_output_is_deterministic_and_compact(data, tmp_path):
    _require_recorded_sources(*data.values())
    assert set(data) == set(NAMES)
    for name, document in data.items():
        sources = document["source"]["files"]
        assert sources, name
        assert len({source["path"] for source in sources}) == len(sources)
        for source in sources:
            assert hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest() == source["sha256"]
    first, second = tmp_path / "first", tmp_path / "second"
    planner_data.write_data(first)
    planner_data.write_data(second)
    assert {p.name for p in first.iterdir()} == {f"{name}.json" for name in NAMES}
    sizes = []
    for name in NAMES:
        raw = (first / f"{name}.json").read_bytes()
        assert raw == (second / f"{name}.json").read_bytes()
        assert json.loads(raw) == data[name]
        sizes.append(len(raw))
    assert sum(sizes) < 2_000_000


def test_arrival_layout_uses_the_actual_onboard_state(data, hero_sources):
    config, instance, _ = hero_sources
    arrival = data["ship"]["initial_plan"]
    expected = {p["container_id"]: _coordinates(p["slot"]) for p in instance["onboard"]["placements"]}
    actual = {row["box_id"]: {key: row[key] for key in ("bay", "row", "tier")} for row in arrival["slots"]}
    assert actual == expected
    assert len(arrival["slots"]) == len(expected) == arrival["counts"]["boxes"]
    assert arrival["counts"]["total_moves"] == 0
    assert arrival["plan_id"] and arrival["label"]
    assert all(row["deck"] is (row["tier"] >= 80) for row in arrival["slots"])
    assert arrival["slots"] == data["moves"]["initial_slots"]
    files = arrival["source"]["files"]
    assert config["instance"] in {record["path"] for record in files}
    for record in files:
        assert hashlib.sha256((ROOT / record["path"]).read_bytes()).hexdigest() == record["sha256"]


@pytest.mark.parametrize("name", ["before", "after"])
def test_departure_meters_match_bound_recorded_physics(data, hero_sources, name):
    _require_recorded_sources(data[f"plan-{name}"])
    config, _, plans = hero_sources
    meters = data[f"plan-{name}"]["meters"]
    source = meters["source"]
    assert source["instance_sha256"] == config["instance_sha256"]
    assert source["plan_sha256"] == config[f"{name}_sha256"]
    assert source["step"] == len(plans[name]["moves"])
    assert source["label"] == "Recorded departure physics"
    assert source["notes"]
    for item in source["files"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    recorded_physics = [item for item in source["files"] if item["path"].endswith(".physics.json")]
    assert len(recorded_physics) == 1
    record = recorded_physics[0]
    raw = (ROOT / record["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == record["sha256"]
    cache = json.loads(raw)
    assert cache["complete"] is True
    assert cache["instance_sha256"] == source["instance_sha256"]
    assert cache["plan_sha256"] == source["plan_sha256"]
    assert cache["physics_epoch"] == source["physics_epoch"]
    assert cache["physics"]["n_lifts"] == len(plans[name]["moves"])
    frames = [frame for frame in cache["physics"]["frames"] if frame["step"] == len(plans[name]["moves"])]
    assert len(frames) == 1
    frame = frames[0]
    assert not frame["error"] and frame["criteria"].startswith("departure")
    for key in ("gm_m", "heel_deg", "trim_m"):
        assert meters[key] == pytest.approx(frame[key])
    assert meters["draught_m"] == pytest.approx(max(frame["draught_aft_m"], frame["draught_fwd_m"]))
    assert meters["draught_limit_m"] == pytest.approx(frame["max_draught_m"])
    assert meters["hull_pct"] == pytest.approx(100 * max(frame["max_sf_ratio"], frame["max_bm_ratio"]))
    # A recorded epoch must agree with the bound recorded regate, and may be
    # different from today's sources. No computation or fresh-pass claim occurs.
    regates = [json.loads((ROOT / item["path"]).read_text())
               for item in data[f"plan-{name}"]["source"]["files"]
               if Path(item["path"]).parent == Path("experiments/mvp/ui/cache/regate")]
    assert regates and any(memo["parts"]["physics_epoch"] == source["physics_epoch"] for memo in regates)


@pytest.mark.parametrize("field", ["instance_sha256", "plan_sha256", "physics_epoch", "complete"])
def test_stale_or_incomplete_meter_recordings_are_refused(replay_case, tmp_path, monkeypatch, field):
    from app import stage_physics

    instance, plan = replay_case
    digests = stage_physics.input_digests(instance, plan)
    epoch = "synthetic-binding-regression-not-a-hero-recording"
    frame = {key: 1.0 for key in stage_physics._FRAME_NUMBERS}
    frame.update({key: [] for key in stage_physics._FRAME_LISTS})
    frame.update(step=len(plan.moves), error=None, criteria="departure: sea-going IS Code",
                 gz=[], stability_passed=True, strength_passed=True)
    cache = {"version": stage_physics.PHYSICS_VERSION, "complete": True, **digests,
             "instance_id": instance.id, "plan_id": plan.id, "physics_epoch": epoch,
             "physics": {"n_lifts": len(plan.moves), "frames": [frame]},
             "label": "Synthetic binding regression; not computed physics"}
    changed = tmp_path / "synthetic-binding.physics.json"
    regate_path = tmp_path / "synthetic-binding-regate.json"
    changed.write_text(json.dumps(cache))
    regate_path.write_text(json.dumps({"parts": {"physics_epoch": epoch},
                                      "label": "Synthetic epoch binding; not a checker verdict"}))
    monkeypatch.setattr(stage_physics, "cache_path", lambda digests: changed)
    # source_record still reads and hashes the actual fixture bytes; only its
    # relative-path base changes because tmp_path lives outside the source tree.
    monkeypatch.setattr(planner_data, "ROOT", tmp_path)
    baseline = planner_data._departure_meters(instance, plan, regate_path)
    assert baseline["gm_m"] == frame["gm_m"]
    assert baseline["source"]["label"] == "Recorded departure physics"
    assert baseline["source"]["physics_epoch"] == epoch
    assert baseline["source"]["instance_sha256"] == digests["instance_sha256"]
    assert baseline["source"]["plan_sha256"] == digests["plan_sha256"]
    assert {row["path"] for row in baseline["source"]["files"]} == {changed.name, regate_path.name}
    cache[field] = False if field == "complete" else "another-recording"
    changed.write_text(json.dumps(cache))
    with pytest.raises(ValueError, match="not bound"):
        planner_data._departure_meters(instance, plan, regate_path)
