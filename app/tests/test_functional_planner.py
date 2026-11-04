"""app/functional_planner.payload: the take-4 planner's documents built from one live call's own files (Extension B).

The released-plan cases use the repo's real synthetic runner outcome (the D192 hero run, a full checker pass) copied
into a tmp call folder, so the builders meet a real instance and a real checker report. No engine, model or network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import functional_planner as fp
from app.functional_view import NO_PLAN, NoPlan

ROOT = Path(__file__).resolve().parents[2]
HERO = ROOT / "experiments/mvp/pipeline/package/fixtures/hero-run/outcome.json"
DEMO_DATA = ROOT / "app/static/planner-data"
CALL = "c20261103-120000-abcdef"


@pytest.fixture(scope="module")
def hero():
    if not HERO.is_file():
        pytest.skip("the hero run outcome is not in this checkout")
    return json.loads(HERO.read_text())


def make_call(tmp_path: Path, outcome: dict | None, *, instance: dict | None = None, receipt: list | None = None) -> Path:
    call = tmp_path / CALL
    (call / "run").mkdir(parents=True)
    inst = instance if instance is not None else outcome["instance"]
    (call / "assembled-instance.json").write_text(json.dumps(inst))
    (call / "case.json").write_text(json.dumps({"fill": 0.6, "ship": "feeder_500"}))
    if outcome is not None:
        (call / "run" / "outcome.json").write_text(json.dumps(outcome))
    if receipt is not None:
        (call / "run" / "package").mkdir()
        (call / "run" / "package" / "receipt.json").write_text(json.dumps({"checker": {"active_rules": receipt, "inactive_rules": []}}))
        (call / "run" / "package" / "bay-plan.html").write_text("<!doctype html><title>bay plan</title>")
    return call


@pytest.fixture
def passed(tmp_path, hero):
    return make_call(tmp_path, hero, receipt=[f"rule.{i}" for i in range(48)])


def test_names_and_gate_are_the_contract():
    assert fp.NAMES == ("ship", "boxes", "plan-before", "plan-after", "moves", "meters", "checks", "package", "state")
    assert fp.GATED == {"plan-after", "moves", "meters", "checks"}


def test_ship_and_boxes_match_the_demo_shapes(passed):
    ship, boxes = fp.payload(passed, "ship"), fp.payload(passed, "boxes")
    demo_ship = json.loads((DEMO_DATA / "ship.json").read_text())
    demo_boxes = json.loads((DEMO_DATA / "boxes.json").read_text())
    assert set(ship) == set(demo_ship)
    assert set(ship["bays"][0]) == set(demo_ship["bays"][0])
    assert set(ship["limits"]) == set(demo_ship["limits"])
    assert set(boxes) == set(demo_boxes) and set(next(iter(boxes["boxes"].values()))) == set(next(iter(demo_boxes["boxes"].values())))
    assert ship["ship"]["teu"] == 500 and ship["ship"]["section_teu"] == 240
    assert ship["ship"]["synthetic_label"] == "Synthetic ship and cargo, simulated port"
    assert ship["initial_plan"]["slots"] == [] and ship["initial_plan"]["counts"] == {"boxes": 0, "total_moves": 0}
    assert ship["source"]["call_id"] == CALL and ship["source"]["files"][0]["path"] == "assembled-instance.json"
    assert "v6_hero" not in json.dumps(ship) and "lattice" not in json.dumps(ship)


def test_released_plan_payloads(passed, hero):
    after, moves, meters, checks = (fp.payload(passed, n) for n in ("plan-after", "moves", "meters", "checks"))
    demo_after = json.loads((DEMO_DATA / "plan-after.json").read_text())
    demo_moves = json.loads((DEMO_DATA / "moves.json").read_text())
    assert {"plan_id", "label", "slots", "counts", "meters", "rule", "source"} <= set(after)
    assert set(after["slots"][0]) == set(demo_after["slots"][0])
    assert after["label"] == "Passes the full checker" and after["status"] == "checked_plan"
    assert after["counts"]["boxes"] == 87 and after["counts"]["total_moves"] == len(hero["plan"]["moves"])
    assert set(moves) >= {"moves", "plan_id", "initial_slots", "source"}
    assert set(moves["moves"][1]) == set(demo_moves["moves"][1]) and len(moves["moves"]) == len(hero["plan"]["moves"])
    # The same real plan's checker values the demo shows, now read from this outcome's checker report.
    assert meters["gm_m"] == pytest.approx(demo_after["meters"]["gm_m"])
    assert meters["draught_m"] == pytest.approx(demo_after["meters"]["draught_m"])
    assert meters["gm_min_m"] == pytest.approx(0.15)
    assert meters["heel_deg"] == pytest.approx(0.00132, abs=1e-4) and meters["trim_m"] == pytest.approx(-0.2502, abs=1e-3)
    assert meters["hull_pct"] == pytest.approx(100 * 0.20377645003306194)  # metrics.max_sf_ratio, not the 0.82 margin
    assert meters["hull_pct"] == pytest.approx(demo_after["meters"]["hull_pct"])
    assert meters["moves"] == len(hero["plan"]["moves"]) and meters["active_rules"] == 48 and meters["passed"] is True
    assert meters["source"]["checker_parts"] == ["condition:straits:call-0:THLCH:departure:stability",
                                                 "condition:straits:call-0:THLCH:departure:strength"]
    assert checks["passed"] is True and checks["active_rules"] == 48 and checks["violation_count"] == 0
    assert checks["parts_ran"] == len(hero["checker"]["parts"])
    assert checks["confirmed_instructions"] == [{"sentence": "Nothing goes into bay nine on this call.", "rechecked": True}]


def test_meters_are_null_when_the_checker_did_not_measure(tmp_path, hero):
    outcome = json.loads(json.dumps(hero))
    for name in list(outcome["checker"]["parts"]):
        if name.endswith(":departure:stability"):
            outcome["checker"]["parts"][name]["ran"] = False
    meters = fp.payload(make_call(tmp_path, outcome), "meters")
    assert meters["gm_m"] is None and meters["gm_min_m"] is None and meters["heel_deg"] is None and meters["draught_m"] is None
    assert meters["hull_pct"] is not None  # strength still ran
    assert meters["active_rules"] is None  # no package receipt in this call


@pytest.mark.parametrize("change", [
    lambda o: o.update(status="no_checked_plan", passed=False),
    lambda o: o.update(passed=False),
    lambda o: o["checker"].update(passed=False),
    lambda o: o.update(plan=None),
    lambda o: o.update(status="relaxed_plan", passed=False, level=3, broken_rules=[]),
])
def test_gated_payloads_refuse_anything_the_gate_refuses(tmp_path, hero, change):
    outcome = json.loads(json.dumps(hero))
    change(outcome)
    call = make_call(tmp_path, outcome)
    for name in fp.GATED:
        with pytest.raises(NoPlan) as caught:
            fp.payload(call, name)
        assert NO_PLAN in str(caught.value)
    # Ungated documents still describe the call itself.
    assert fp.payload(call, "ship")["bays"] and fp.payload(call, "state")["plan_released"] is False


def test_no_outcome_yet(tmp_path, hero):
    call = make_call(tmp_path, None, instance=hero["instance"])
    with pytest.raises(NoPlan, match="hasn't written an outcome"):
        fp.payload(call, "plan-after")
    state = fp.payload(call, "state")
    assert state["instance"] == "assembled" and state["outcome_status"] is None and state["plan_released"] is False
    assert fp.payload(call, "package") == {"call_id": CALL, "files": [], "outcome_url": None, "built": False, "attempt_folder": "run",
                                           "label": "Synthetic ship and cargo, simulated port"}


def test_relaxed_plan_is_released_with_its_label(tmp_path, hero):
    outcome = json.loads(json.dumps(hero))
    outcome.update(status="relaxed_plan", passed=False, level=2, broken_rules=["expert.e2.weight_order"])
    call = make_call(tmp_path, outcome)
    after, checks, meters = fp.payload(call, "plan-after"), fp.payload(call, "checks"), fp.payload(call, "meters")
    assert after["label"] == checks["label"] == "Relaxed plan: gives up stowage practice"
    assert checks["passed"] is False and meters["passed"] is False
    assert checks["broken_rules"] == [{"id": "expert.e2.weight_order", "title": "No heavier container above a lighter container", "tier": "T4"}]


def test_package_lists_only_whitelisted_files(passed):
    (passed / "run" / "package" / "secret.txt").write_text("not shown")
    pkg = fp.payload(passed, "package")
    assert [f["name"] for f in pkg["files"]] == ["bay-plan.html", "receipt.json"]
    assert pkg["files"][0]["url"] == f"/api/functional/calls/{CALL}/file/run/package/bay-plan.html"
    assert pkg["outcome_url"].endswith("/file/run/outcome.json") and pkg["built"] is True


def test_no_instance_yet(tmp_path):
    (tmp_path / CALL).mkdir()
    with pytest.raises(NoPlan, match="no synthetic instance"):
        fp.payload(tmp_path / CALL, "ship")
    with pytest.raises(KeyError):
        fp.payload(tmp_path / CALL, "stress")


def test_module_reads_no_recorded_or_demo_sources():
    source = (ROOT / "app/functional_planner.py").read_text()
    for banned in ("RecordedClient", "RecordedParser", "stored=", "lattice", "radio/readings", "v6_hero", "ui/cache",
                   "planner-data", "persisted_regate", "_departure_meters", "import stowage.moves", "apply_plan"):
        assert banned not in source, banned


@pytest.mark.parametrize("sf, bm, expected", [(0.30, 0.55, 55.0), (1.25, 0.40, 125.0)])
def test_hull_meter_is_utilisation_from_metrics_not_margin(tmp_path, hero, sf, bm, expected):
    """Normal and overloaded strength parts: the meter is 100 x max(actual/permissible), whatever the margins say."""
    outcome = json.loads(json.dumps(hero))
    for name, part in outcome["checker"]["parts"].items():
        if name.endswith(":departure:strength"):
            part["metrics"].update(max_sf_ratio=sf, max_bm_ratio=bm)
            part["margins"].update(sf_ratio=1 - sf, bm_ratio=1 - bm)
    assert fp.payload(make_call(tmp_path, outcome), "meters")["hull_pct"] == pytest.approx(expected)


def test_hull_meter_is_null_when_strength_did_not_run(tmp_path, hero):
    outcome = json.loads(json.dumps(hero))
    for name, part in outcome["checker"]["parts"].items():
        if name.endswith(":departure:strength"):
            part["error"] = "strength failed"
    assert fp.payload(make_call(tmp_path, outcome), "meters")["hull_pct"] is None



def test_plan_again_reads_the_latest_attempt(tmp_path, hero):
    """Plan again on the same call: runs/<n>/ attempts; the gate and payloads use the highest-numbered one."""
    from app.functional_view import run_folder
    call = make_call(tmp_path, hero, receipt=[f"rule.{i}" for i in range(48)])
    assert run_folder(call) == "run"
    relaxed = json.loads(json.dumps(hero))
    relaxed.update(status="relaxed_plan", passed=False, level=2, broken_rules=["expert.e2.weight_order"])
    for n, data in ((1, hero), (2, relaxed)):
        (call / "runs" / str(n)).mkdir(parents=True)
        (call / "runs" / str(n) / "outcome.json").write_text(json.dumps(data))
    assert run_folder(call) == "runs/2"
    assert fp.payload(call, "checks")["label"] == "Relaxed plan: gives up stowage practice"
    assert fp.payload(call, "state")["plan_label"] == "Relaxed plan: gives up stowage practice"
    (call / "runs" / "3").mkdir()  # a new attempt is running: nothing earlier is drawn as current
    with pytest.raises(NoPlan, match="hasn't written an outcome"):
        fp.payload(call, "plan-after")
    pkg = fp.payload(call, "package")
    assert pkg["attempt_folder"] == "runs/3" and pkg["files"] == [] and pkg["outcome_url"] is None
