"""Break-it chapter: rule ordering, the lanes' supersede rules, the routes, and the drag logic under node. No Chrome, no network."""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from app import v6, v6_sandbox as bench
from app.server import make_handler

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "app/static/stage-v6breakit.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

FAKE = textwrap.dedent("""
    import json, sys, time
    RECORDED = {"mode": "recorded", "checked_utc": "2026-01-02T03:04:05+00:00", "fast": {"legal": True, "screen": "fast", "wall_s": 1.5, "broken": [], "gauges": {}},
                "full": {"legal": True, "screen": "full", "wall_s": 9.0, "broken": [], "gauges": {}}, "explanation": {"mode": "recorded", "statements": []}}
    role = sys.argv[1]
    print(json.dumps({"ready": True, "pid": 1, "load_s": 0}), flush=True)
    for line in sys.stdin:
        q = json.loads(line)
        if q["op"] == "explain":
            if q["box"] == "BROKEN":
                print(json.dumps({"id": q["id"], "ok": False, "error": "no"}), flush=True)
                continue
            if q.get("slot") and role == "fast" and q["box"] != "REC":
                print(json.dumps({"id": q["id"], "ok": False, "error": "a move no recording covers belongs on the full lane"}), flush=True)
                continue
            print(json.dumps({"id": q["id"], "ok": True, "basis": {"about": "hero", "checked_utc": "2026-01-02T00:00:00+00:00"}, "explanation": {"mode": "live", "text": "Template placement facts (no model summary)\\n" + q["box"], "statements": [
                {"id": "position", "text": q["box"], "evidence": [{"source": "report", "path": "/passed", "sha256": "ab" * 32, "href": "report:" + "ab" * 32 + "#/passed", "value": True}]}]}}), flush=True)
            continue
        if q["op"] == "cached":
            print(json.dumps({"id": q["id"], "ok": True, "recorded": RECORDED if q["box"] == "HIT" else None}), flush=True)
            continue
        if q["box"] == "CRASH":
            sys.exit(3)
        if role == "full" and q["box"] != "QUICK":
            time.sleep(30)
        verdict = {"legal": True, "screen": role, "broken": [], "gauges": {"frames": [{"gm_m": 2.5}]}, "extra_lifts": 0, "wall_s": 0.01}
        print(json.dumps({"id": q["id"], "ok": True, "verdict": verdict, "explanation": {"mode": "live", "statements": []}}), flush=True)
""")


@pytest.fixture
def workers(tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE)
    bench_sandbox = bench.Sandbox(lambda role: [sys.executable, str(script), role])
    yield bench_sandbox
    bench_sandbox.close()


def wait(test, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------- reading verdicts
def cases():
    return {case["kind"]: case for case in bench.cases_view(bench.demo_fixture())}


@pytest.mark.parametrize("kind", ["legal", "crane_order", "securing", "segregation"])
def test_the_deciding_cause_leads_and_every_rule_stays(kind):
    case = cases()[kind]
    cause = case["verdict"]["gauges"]["deciding_cause"]
    if cause["rule_id"]:
        assert case["verdict"]["rows"][0]["rule_id"] == cause["rule_id"]
    for answer in (case["fast"], case["verdict"]):
        assert {r["rule_id"] for r in answer["rows"]} == {b["rule_id"] for b in answer["broken"]}
        assert sum(r["count"] for r in answer["rows"]) == len(answer["broken"])


def test_retired_case_names_stay_retired():
    assert set(bench._CASE_NAMES) == {"legal", "crane_order", "securing", "segregation"}
    assert {"stack_weight", "stability"}.isdisjoint(cases())
    js = JS.read_text()
    assert "kind: 'securing'" in js and "stack_weight" not in js and "'stability'" not in js
    assert "Stack" not in bench._CASE_NAMES["securing"]
    assert cases()["securing"]["verdict"]["rows"][0]["name"] == "Lashing (our approximation, synthetic gear)", "a securing failure never reads as stack weight"
    assert "approximation" in cases()["securing"]["verdict"]["rows"][0]["note"]


def test_a_failed_crane_order_leads_even_when_the_snapshot_breaks_a_rule():
    case = cases()["segregation"]["verdict"]
    said, cause = case["outcome"], case["gauges"]["deciding_cause"]
    assert cause["kind"] == "crane_order" and case["gauges"]["outcome"] == "stow_breaks_rule"
    assert said["kind"] == "crane_first" and said["rule_id"] == cause["rule_id"] and "crane order" in said["title"]
    snapshot = next(r for r in case["rows"] if r["rule_id"] == said["diagnostic"]["rule_id"])
    assert snapshot["diagnostic"] and said["diagnostic"]["name"] in said["plain"] and "diagnostic" in said["plain"]
    assert case["rows"][0]["rule_id"] == cause["rule_id"] and not case["rows"][0]["diagnostic"]
    first, second = case["sentence"]["text"].split(". ")[:2]
    assert "crane order" in first


def test_the_view_keeps_the_new_gauge_keys_and_the_engine_s_lift_line():
    for case in cases().values():
        gauges, raw = case["verdict"]["gauges"], next(c for c in bench.demo_fixture()["cases"] if c["kind"] == case["kind"])["verdict"]["gauges"]
        assert all(gauges[k] == raw[k] for k in ("outcome", "deciding_cause", "stow_assessment", "full_check_scope", "scope_note"))
        assert case["verdict"]["lifts"] == raw["lift_counts"]["display"].replace("Crane lifts", "Extra lifts") and case["verdict"]["scope"] == raw["scope_note"]
    unavailable = cases()["crane_order"]["verdict"]["lifts"]
    assert "unavailable" in unavailable and "+0" not in unavailable


def test_numbers_reach_the_card_unchanged():
    row = next((c["verdict"]["rows"][0] for c in cases().values() if c["verdict"]["rows"] and c["verdict"]["rows"][0]["measured"] is not None), None)
    if row is None:
        pytest.skip("no measured row in this fixture")
    raw = next(b for c in bench.demo_fixture()["cases"] for b in c["verdict"]["broken"] if b["rule_id"] == row["rule_id"])
    assert (row["measured"], row["limit"], row["unit"]) == (raw["measured"], raw["limit"], raw["unit"])
    if row["limit"] in bench.challenge_of(bench.demo_fixture())["limits"]:
        assert row["challenge"], "a synthetic limit has to say so on the card"


def test_engine_rows_never_lead_a_physics_rule():
    rows = bench.lead_rows([{"rule_id": "expert.moves.hatch_clear", "message": "m", "boxes": []},
                            {"rule_id": "imdg.segregation.7_4_3", "message": "n", "boxes": ["A", "B"]}])
    assert [r["rule_id"] for r in rows] == ["imdg.segregation.7_4_3", "expert.moves.hatch_clear"]


def test_unknown_rule_falls_back_to_the_checkers_own_message():
    rows = bench.lead_rows([{"rule_id": "made.up", "message": "[straits:call-0 departure] the checker's words", "boxes": []}])
    assert rows[0]["name"] == "made.up" and rows[0]["sentence"] == "the checker's words"


def test_sentence_is_labelled_by_mode_and_absent_when_there_is_none():
    verdict = {"legal": False, "screen": "full", "broken": [{"rule_id": "x.y", "measured": 2.123456, "limit": 1.0, "boxes": ["A"]}]}
    explain = {"mode": "recorded", "text": "Template explanation.\n...", "statements": [
        {"id": "status", "text": "The full checker rejected the moved plan."},
        {"id": "broken:0", "text": 'Rule x.y failed. Measured: 2.123456 kg; limit: 1.0 kg; boxes: ["A"].'}]}
    said = bench.sentence(verdict, explain)
    assert said["label"].startswith("Recorded answer") and "boxes" not in said["text"] and "2.123 kg" in said["text"]
    assert bench.sentence(verdict, {**explain, "mode": "live"})["label"] == bench.TEMPLATE_LABEL
    assert bench.sentence(verdict, None) is None  # cut order 5: the checker's answer stands alone
    spark = bench.sentence(verdict, {**explain, "text": "Muse Spark selected checked statements.\n..."})
    assert "selects and orders checked statements" in spark["label"] and "decid" not in spark["label"]


def test_fast_sentence_does_not_repeat_the_label():
    verdict = {"legal": False, "screen": "fast", "broken": [{"rule_id": "x.y", "boxes": []}]}
    said = bench.sentence(verdict, {"mode": "live", "statements": [{"id": "status", "text": bench.FAST_LABEL + ". Found."}, {"id": "broken:0", "text": "Rule x.y failed."}]})
    assert said["text"] == "Rule x.y failed."


def test_route_view_has_recorded_fast_and_full_with_measured_scope():
    data = v6.feature(v6.sandbox)
    assert data["available"] and data["running_label"] == "Fast screen, full check running"
    demo = bench.demo_fixture()
    assert data["cases"] and {c["kind"] for c in data["cases"]} <= {c["kind"] for c in demo["cases"]}
    assert all(c["verdict"]["screen"] == "full" and c["name"] and c["checked_on"].startswith("Checked on ") for c in data["cases"])
    assert data["label"] == "Recorded answer" and "Recorded answer" in data["stored_label"] and f"Checked on {demo['created_utc'][:10]}" in data["stored_label"]
    assert "rechecked" not in data["stored_label"]
    assert data["challenge_tag"] == "Demo cargo declaration changed for this challenge; no vessel or rule limit tightened"
    assert data["challenge_note"] == demo["label_disclosure"]  # the fixture's own disclosure stays beside it
    assert "development" in data["measured"]["scope"]


# ---------------------------------------------------------------- the demo has to be the configured hero
def test_the_plan_is_shown_to_derive_from_the_configured_hero_by_undoing_its_edits():
    derived = bench.derivation(bench.read_demo())
    words = bench.demo_fixture()["modifications"][0]["after"]["un_number"]
    assert derived["edits"] == 1 and derived["line"].startswith("Derived from this voyage's checked plan, with one disclosed DG declaration.") and words in derived["line"]
    assert v6.feature(v6.sandbox)["derived"] == derived["line"]


def switched(tmp_path, monkeypatch, **hero):
    """The config points at a published hero that isn't the one the demo was built on."""
    name = json.loads((ROOT / "app/v6_hero.json").read_text())["hero_source"]
    manifest = json.loads((ROOT / name).read_text())
    manifest["other"] = {**manifest["hero"], **hero}
    path = tmp_path / "hero.json"
    path.write_text(json.dumps(manifest))
    config = {**json.loads((ROOT / "app/v6_hero.json").read_text()), "hero_source": str(path), "hero_slot": "other"}
    monkeypatch.setattr(bench, "_config", lambda: config)


def test_a_hero_switch_makes_the_stale_demo_a_clean_gap(tmp_path, monkeypatch):
    switched(tmp_path, monkeypatch, instance_sha256="0" * 64)
    data = v6.feature(v6.sandbox)
    assert data["available"] is False and "another instance" in data["reason"]
    monkeypatch.setitem(bench._STATE, "failed", None)  # restored after the test: register records its failure here
    notes = bench.register({}, [])
    assert "chapter dropped" in notes[0] and bench._SANDBOX is None


def test_a_demo_recorded_on_another_file_is_a_clean_gap(monkeypatch):
    demo = {**bench.demo_fixture(), "source_sha256": "0" * 64}
    monkeypatch.setattr(bench, "demo_fixture", lambda: demo)
    assert "another plan file" in v6.feature(v6.sandbox)["reason"]


# ---------------------------------------------------------------- the pinned drag cache
def test_the_pin_is_independent_of_the_fixture_s_own_hash_file():
    config = json.loads((ROOT / "app/v6_hero.json").read_text())
    assert config["drag_cache_sha256"] == hashlib.sha256((bench.FIXTURES / "drag-cache.json").read_bytes()).hexdigest()
    assert len(bench.cache_requests()) == json.loads((bench.FIXTURES / "drag-cache.index.json").read_text())["entries_count"]


def test_a_wrong_pin_answers_nothing_from_the_cache(monkeypatch):
    monkeypatch.setattr(bench, "_config", lambda: {"drag_cache_sha256": "0" * 64})
    assert bench.cache_requests() == frozenset()
    monkeypatch.setattr(bench, "_config", lambda: {})
    assert bench.cache_requests() == frozenset()


def real_world():
    from app import v6_sandbox_worker as worker
    return worker, worker.inputs()


def test_a_real_cached_drag_replays_with_its_recorded_date_and_a_near_miss_does_not():
    worker, world = real_world()
    box, slot = sorted(bench.cache_requests())[0]
    out = worker.answer(world, "fast", {"id": 1, "op": "cached", "box": box, "slot": slot})
    assert out["recorded"]["mode"] == "recorded" and out["recorded"]["label"] == "Recorded answer" and out["recorded"]["checked_utc"]
    assert "canonical_call" not in out["recorded"]
    assert worker.answer(world, "fast", {"id": 2, "op": "cached", "box": "NOTABOX", "slot": slot})["recorded"] is None


def test_a_cached_drag_is_a_recording_and_no_check_runs(workers, monkeypatch):
    monkeypatch.setattr(bench, "cache_requests", lambda: frozenset({("HIT", "0020482")}))
    number = workers.start_try("HIT", "0020482")["job"]
    assert wait(lambda: workers.job(number)["status"] == "done")
    row = workers.job(number)
    assert row["recorded"] and row["checked_on"] == "Checked on 2026-01-02" and row["full"]["legal"] and row["full_s"] == 9.0
    assert workers.full.proc is None, "a hit must not start a full check"


def test_a_cache_miss_goes_to_the_checker_never_to_another_verdict(workers, monkeypatch):
    monkeypatch.setattr(bench, "cache_requests", lambda: frozenset({("MISS", "0020482")}))
    number = workers.start_try("MISS", "0020482")["job"]
    assert wait(lambda: workers.job(number)["fast"] is not None)
    assert not workers.job(number)["recorded"] and workers.job(number)["status"] == "running"


# ---------------------------------------------------------------- timings come from the fixture
def test_measured_is_the_low_load_screen_with_its_scope_and_its_unmet_target():
    data, raw = bench.measured(), json.loads((bench.FIXTURES / "perf_fast190_low.json").read_text())
    assert data["fast_median_s"] == raw["median_s"] and data["moves"] == len(raw["cases"])
    line = data["lines"][0]
    assert f"{raw['median_s']:.3f} s median" in line and "low-load window" in line and "development" in line
    assert "The under-2-second target is unmet on this 3-move sample." in line
    assert "0.76" not in " ".join(data["lines"]) and "not this one" in line


def test_the_screen_s_plan_size_matches_the_plan_it_ran_on():
    envelope = json.loads((bench.FIXTURES / "envelope190.json").read_text())
    assert hashlib.sha256((bench.FIXTURES / "envelope190.json").read_bytes()).hexdigest() == json.loads((bench.FIXTURES / "perf_fast190_low.json").read_text())["source_sha256"]
    assert len(envelope["instance"]["load_list"]) == bench._SCREEN[1]


def test_a_broken_timing_fixture_loses_its_line(monkeypatch, tmp_path):
    monkeypatch.setattr(bench, "FIXTURES", tmp_path)
    assert bench.measured() is None
    (tmp_path / bench._SCREEN[0]).write_text(json.dumps({"median_s": 1.5, "cases": [{"wall_s": 1.5, "admission": {"load1": 3}, "end_load": {"load1": 4}}]}))
    assert "target" not in bench.measured()["lines"][0], "a met target isn't called unmet"


# ---------------------------------------------------------------- the three outcomes, deciding cause first
MOVES = {"rule_id": "expert.moves.hatch_clear", "message": "All hatch covers must be replaced.", "source": "proposal_replay", "severity": "hard"}
STACK = {"rule_id": "expert.physical.stack_weight", "message": "m", "measured": 3.0, "limit": 2.0, "source": "requested_stow_snapshot", "severity": "hard"}


def full(legal=False, rows=(), **gauges):
    return {"legal": legal, "screen": "full", "broken": list(rows), "gauges": gauges, "extra_lifts": None}


def test_passes_as_edited():
    said = bench.outcome(full(True))
    assert said["kind"] == "passes" and said["title"] == "Passes as edited"


def test_stow_fine_but_the_crane_order_cannot_reach_it():
    said = bench.outcome(full(rows=[MOVES], diagnostic_only=True, checker_passed=True))
    assert said["kind"] == "crane_order" and "crane order can't reach it" in said["title"] and "A replan might." in said["plain"]


def test_a_stow_rule_leads_and_the_crane_order_stays_on_the_card():
    verdict = full(rows=[MOVES, STACK], diagnostic_only=True, checker_passed=False)
    said = bench.outcome(verdict)
    assert said["kind"] == "stow_rule" and said["rule_id"] == STACK["rule_id"] and said["crane_too"] and "Stack weight" in said["title"]
    assert bench.view(verdict)["rows"][0]["rule_id"] == STACK["rule_id"]


CRANE = {"kind": "crane_order", "source": "proposal_replay", "rule_id": MOVES["rule_id"], "message": "m"}


def test_deciding_cause_overrides_the_rule_first_order_and_the_snapshot_is_diagnostic():
    verdict = full(rows=[STACK, MOVES], diagnostic_only=True, outcome="stow_breaks_rule", deciding_cause=CRANE, stow_assessment={"passed": False})
    said, seen = bench.outcome(verdict), bench.view(verdict)
    assert said["kind"] == "crane_first" and said["rule_id"] == MOVES["rule_id"] and said["diagnostic"]["name"] == "Stack weight"
    assert [r["rule_id"] for r in seen["rows"]] == [MOVES["rule_id"], STACK["rule_id"]] and seen["rows"][1]["diagnostic"] and not seen["rows"][0]["diagnostic"]


def test_an_unsettled_snapshot_stays_unsettled_under_a_failed_crane_order():
    said = bench.outcome(full(rows=[MOVES], diagnostic_only=True, outcome=None, deciding_cause=CRANE, stow_assessment={"passed": None}))
    assert said["kind"] == "crane_unsettled" and "fine" not in said["title"] and "didn't settle" in said["plain"]


def test_a_stow_rule_cause_names_that_rule_and_an_unavailable_check_invents_nothing():
    other = {**STACK, "rule_id": "iacs.member_irs.container_securing"}
    verdict = full(rows=[STACK, other], outcome="stow_breaks_rule", deciding_cause={"kind": "stow_rule", "rule_id": other["rule_id"]})
    assert bench.outcome(verdict)["rule_id"] == other["rule_id"] and bench.view(verdict)["rows"][0]["name"] == "Lashing (our approximation, synthetic gear)"
    assert bench.outcome(full(rows=[STACK], outcome=None, deciding_cause={"kind": "check_unavailable", "rule_id": STACK["rule_id"]}))["kind"] == "unresolved"


def test_a_failed_check_of_a_reached_plan_is_a_rule_though_its_source_says_replay():
    row = {**STACK, "source": "proposal_replay"}
    said = bench.outcome(full(rows=[row], diagnostic_only=False))
    assert said["kind"] == "stow_rule" and not said["crane_too"]


def test_a_replay_row_on_a_diagnostic_verdict_is_crane_order_whatever_its_rule():
    row = {**STACK, "source": "proposal_replay"}
    assert bench.outcome(full(rows=[row], diagnostic_only=True, checker_passed=True))["kind"] == "crane_order"


@pytest.mark.parametrize("verdict", [
    full(rows=[MOVES, {"rule_id": "checker.internal_error", "message": "x", "severity": "hard"}], diagnostic_only=True),
    full(rows=[MOVES], diagnostic_only=True, checker_passed=False),
    full(rows=[MOVES], diagnostic_only=True, outcome=None),
    full()])
def test_stow_is_fine_is_never_said_without_a_check_that_shows_it(verdict):
    assert bench.outcome(verdict)["kind"] == "unresolved"


def test_the_sandboxs_own_outcome_wins_when_a_verdict_carries_it():
    assert bench.outcome(full(rows=[MOVES], outcome="stow_fine_crane_order_failed"))["kind"] == "crane_order"
    assert bench.outcome(full(rows=[MOVES, STACK], outcome="stow_breaks_rule"))["rule_id"] == STACK["rule_id"]


def test_fast_screens_never_claim_a_full_outcome():
    fast = {"legal": False, "screen": "fast", "broken": [{"rule_id": MOVES["rule_id"]}], "gauges": {}}
    assert bench.outcome(fast)["kind"] == "flagged_crane" and "fine" not in bench.outcome(fast)["title"]
    fast["broken"].append({"rule_id": STACK["rule_id"]})
    said = bench.outcome(fast)
    assert said["kind"] == "flagged" and said["title"] == "Stack weight" and said["crane_too"]
    clear = bench.outcome({"legal": True, "screen": "fast", "broken": []})
    assert clear["kind"] == "clear" and "isn't a pass" in clear["plain"]


def test_an_input_error_names_no_rule():
    said = bench.outcome({"legal": False, "screen": "full", "gauges": {"input_error": True}})
    assert said["kind"] == "input_error" and said["rule_id"] is None


def test_every_recorded_case_gets_an_outcome_that_matches_its_rows():
    for case in cases().values():
        said = case["verdict"]["outcome"]
        assert said["kind"] in {"passes", "crane_order", "crane_first", "stow_rule"}
        if said["rule_id"]:
            assert case["verdict"]["rows"][0]["rule_id"] == said["rule_id"]
        assert (said["kind"] == "passes") == case["verdict"]["legal"]


# ---------------------------------------------------------------- what the edit does and costs
def test_lift_line_is_the_engine_s_own_or_nothing():
    display = "Crane lifts this call: +0. Projected later-port lifts: +7 (projection)."
    assert bench.lifts_line({"gauges": {"lift_counts": {"display": display}}}) == "Extra lifts this call: +0. Projected later-port lifts: +7 (projection)."
    assert bench.lifts_line({"gauges": {"lift_counts": {"this_call_label": "A.", "projected_later_label": "B."}}}) == "A. B."
    assert bench.lifts_line({"extra_lifts": 0, "gauges": {"lift_counts": {"this_call_label": "A."}}}) is None
    assert bench.lifts_line({"extra_lifts": 0, "gauges": {}}) is None and bench.lifts_line({}) is None


def test_the_fully_checked_pass_keeps_its_measured_later_port_cost():
    raw = next(c for c in bench.demo_fixture()["cases"] if c["kind"] == "legal")["verdict"]["gauges"]["lift_counts"]
    case = cases()["legal"]["verdict"]
    assert f"{raw['projected_later_delta']:+d}" in case["lifts"] and "(projection)" in case["lifts"]
    assert case["quality"] == "Legal isn't the same as good." and cases()["securing"]["verdict"]["quality"] is None


def test_scope_is_the_verdict_s_own_note_and_a_pass_says_legal_is_not_good():
    assert bench.view(full(True))["scope"] == "This edits one box and keeps the crane order and ballast. It doesn't replan."
    assert bench.view(full(True))["quality"] == "Legal isn't the same as good."
    assert bench.view(full(True, scope_note="Arrival edit."))["scope"] == "Arrival edit." and bench.view(full())["quality"] is None


# ---------------------------------------------------------------- prepared cases: whichever the fixture has
TRIM = {"rule_id": "iscode.loading.trim", "measured": -0.148, "limit": -0.156, "severity": "hard"}


def demo_with(*cases, maximum=-0.156):
    return {"modifications": [{"field": "instance.rule_overrides.iscode.loading.trim.maximum", "after": maximum}], "cases": list(cases)}


def case(kind, verdict):
    return {"kind": kind, "box_id": "A", "target_slot": "0020482", "verdict": verdict}


def test_a_trim_window_that_excludes_level_drops_its_case_only():
    demo = demo_with(case("stability", full(rows=[TRIM])), case("legal", full(True)))
    assert [c["kind"] for c in bench.cases_view(demo)] == ["legal"]


def test_a_trim_case_on_a_sane_limit_stays():
    assert [c["kind"] for c in bench.cases_view(demo_with(case("stability", full(rows=[TRIM])), maximum=0.4))] == ["stability"]


def test_the_chapter_loads_with_no_trim_case_in_the_fixture(monkeypatch):
    demo = bench.demo_fixture()
    kept = [c for c in demo["cases"] if not any("trim" in str(b.get("rule_id")) for b in c["verdict"]["broken"])]
    monkeypatch.setattr(bench, "demo_fixture", lambda: {**demo, "cases": kept})
    data = v6.feature(v6.sandbox)
    assert data["available"] and not any(r["rule_id"].startswith("iscode.loading.trim") for c in data["cases"] for r in c["verdict"]["rows"])


def test_a_new_case_kind_is_listed_with_a_readable_name():
    assert bench.cases_view({"cases": [case("reefer_plug_gap", full())]})[0]["name"] == "Reefer plug gap"


# ---------------------------------------------------------------- a fixture that changed shape loses a panel, never the chapter
def strip(verdict):
    return {k: v for k, v in verdict.items() if k != "gauges"}


MUTATIONS = {
    "no broken fields": lambda d: [c["verdict"].update(broken=[{} for _ in c["verdict"]["broken"]]) for c in d["cases"]],
    "broken isn't rows": lambda d: [c["verdict"].update(broken=["hatch_clear", 4, None]) for c in d["cases"]],
    "boxes isn't a list": lambda d: [b.update(boxes=3) for c in d["cases"] for b in c["verdict"]["broken"]],
    "renamed gauges": lambda d: [c["verdict"].update(gauges={"stages": c["verdict"]["gauges"].get("frames")}) for c in d["cases"]],
    "gauges gone": lambda d: [c["verdict"].pop("gauges") for c in d["cases"]],
    "frames are junk": lambda d: [c["verdict"]["gauges"].update(frames="none") for c in d["cases"]],
    "new kind": lambda d: [c.update(kind="a_new_kind") for c in d["cases"]],
    "no kind": lambda d: [c.pop("kind") for c in d["cases"]],
    "no explanation": lambda d: [(c.pop("explanation", None), c.pop("fast_explanation", None)) for c in d["cases"]],
    "explanation is junk": lambda d: [c.update(explanation={"statements": [1, {"id": "status"}]}) for c in d["cases"]],
    "no fast frames": lambda d: [c.pop("fast", None) for c in d["cases"]],
    "a case without a verdict": lambda d: d["cases"][0].pop("verdict"),
    "a case that isn't one": lambda d: d["cases"].__setitem__(0, "case"),
    "no cases": lambda d: d.pop("cases"),
    "cases is junk": lambda d: d.update(cases={"a": 1}),
    "modifications are junk": lambda d: d.update(modifications=[1, {"after": "x"}]),
    "no source or date": lambda d: (d.pop("source_fixture"), d.pop("created_utc")),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_a_changed_fixture_still_loads_the_chapter(name, monkeypatch):
    demo = json.loads(json.dumps(bench.demo_fixture()))
    MUTATIONS[name](demo)
    monkeypatch.setattr(bench, "demo_fixture", lambda: demo)
    data = v6.feature(v6.sandbox)
    assert data["available"] is True and isinstance(data["cases"], list)
    json.dumps(data)
    for row in data["cases"]:
        assert row["verdict"]["outcome"]["title"] and row["verdict"]["outcome"]["plain"] and row["verdict"]["scope"]
        assert isinstance(row["verdict"]["gauges"], dict) and isinstance(row["verdict"]["rows"], list)


@pytest.mark.parametrize("error", [OSError("gone"), ValueError("half written")])
def test_no_readable_demo_fixture_leaves_free_drags(error, monkeypatch):
    def fail():
        raise error
    monkeypatch.setattr(bench, "demo_fixture", fail)
    data = v6.feature(v6.sandbox)
    assert data["available"] is True and data["cases"] == [] and data["stored_label"] is None


def test_worker_starts_with_no_report_and_no_cases(monkeypatch):
    from app import v6_sandbox_worker as worker
    monkeypatch.setattr(bench, "demo_fixture", lambda: {"cases": [{"kind": "x"}]})
    world = worker.inputs()
    assert world["report"] is None and world["probe"] is None and world["plan"] is not None
    with pytest.raises(ValueError):
        worker.answer(world, "fast", {"id": 1, "op": "explain", "box": "A", "lang": "en"})


def test_a_failed_registration_is_a_clean_gap(monkeypatch):
    monkeypatch.setitem(bench._STATE, "failed", "ValueError: no")
    data = v6.feature(v6.sandbox)
    assert data["available"] is False and "no" in data["reason"]


# ---------------------------------------------------------------- lanes and jobs
def test_fast_answer_never_waits_for_a_full_check(workers):
    job = workers.start_try("A", "0020482")["job"]
    assert wait(lambda: workers.job(job)["fast"] is not None)
    row = workers.job(job)
    assert row["full"] is None and row["status"] == "running" and row["running_label"] == "Fast screen, full check running"


def test_new_drag_supersedes_and_stops_the_old_full_check(workers):
    first = workers.start_try("A", "0020482")["job"]
    assert wait(lambda: workers.job(first)["fast"] is not None and workers.full.running is not None)
    old_pid = workers.full.pid
    second = workers.start_try("QUICK", "0020482")["job"]
    assert workers.job(first)["status"] == "superseded"
    assert wait(lambda: workers.job(second)["full"] is not None)
    assert workers.full.pid != old_pid
    assert workers.job(second)["status"] == "done" and workers.job(first)["full"] is None


def test_only_the_newest_drag_waits_on_a_busy_lane(workers):
    done = []
    lane = workers.full
    lane.start()
    assert wait(lambda: lane.ready)
    lane.submit({"id": 10, "op": "try", "box": "SLOW", "slot": "s"}, done.append, key="other")
    lane.submit({"id": 12, "op": "try", "box": "QUICK", "slot": "s"}, done.append, key="try")
    lane.submit({"id": 14, "op": "try", "box": "QUICK", "slot": "s"}, done.append, key="try")
    assert any(m.get("superseded") for m in done)


def test_a_dead_worker_is_an_answer_not_a_spinner(workers):
    job = workers.start_try("CRASH", "0020482")["job"]
    assert wait(lambda: workers.job(job)["status"] == "failed")
    assert "worker" in workers.job(job)["error"]
    again = workers.start_try("QUICK", "0020482")["job"]
    assert wait(lambda: workers.job(again)["full"] is not None), "the lane respawns for the next drag"


def test_job_input_is_checked_and_unknown_job_is_a_keyerror(workers):
    with pytest.raises(ValueError):
        workers.start_try("", "0020482")
    with pytest.raises(KeyError):
        workers.job("nope")


def test_explain_is_asynchronous_and_memoised(workers):
    first = workers.explain("A", "th")
    assert first["status"] == "running"
    assert wait(lambda: workers.explain("A", "th")["status"] == "done")
    done = workers.explain("A", "th")
    evidence = done["statements"][0]["evidence"][0]
    assert evidence["sha256"] == "ab" * 32 and evidence["href"].startswith("report:") and evidence["value_json"] is True
    assert done["thai_note"] == bench.THAI_NOTE and workers.explain("A", "en")["status"] == "running"
    with pytest.raises(ValueError):
        workers.explain("A", "fr")


def test_a_template_is_labelled_a_template_and_only_an_accepted_summary_names_spark():
    row = {"id": "position", "text": "x", "evidence": []}
    template = bench.explain_view("A", "en", {"mode": "live", "text": "Template placement facts (no model summary)\nx", "statements": [row]})
    assert template["label"] == "Template placement facts (no model summary)" and template["spark"] is None and template["recorded"] is None
    summary = {"id": "summary", "kind": "model_summary", "model_label": "Recorded Spark summary", "text": "y", "evidence": []}
    spark = bench.explain_view("A", "en", {"mode": "recorded", "text": "Recorded Spark summary\ny", "statements": [summary, row]}, {"about": "hero", "checked_utc": "2026-01-02T00:00:00"})
    assert spark["label"] == "Recorded Spark summary" and spark["recorded"] == "Recorded answer" and spark["checked_on"] == "Checked on 2026-01-02"
    assert "wrote" in spark["spark"] and "explain" not in spark["spark"].lower() and "before the disclosed cargo change" in spark["note"]


def test_a_failed_explanation_is_said_once_then_asked_again(workers):
    workers.explain("BROKEN", "en")
    assert wait(lambda: workers.answers.get(("BROKEN", "en", None), {}).get("status") == "failed")
    assert workers.explain("BROKEN", "en")["status"] == "failed"
    assert workers.explain("BROKEN", "en")["status"] == "running"


def test_a_new_move_explanation_uses_the_full_lane_a_recorded_one_does_not(workers, monkeypatch):
    monkeypatch.setattr(bench, "cache_requests", lambda: frozenset({("REC", "0020482")}))
    workers.explain("REC", "en", "0020482")
    assert wait(lambda: workers.explain("REC", "en", "0020482")["status"] == "done")
    assert workers.explain("REC", "en", "0020482")["target_slot"] == "0020482" and workers.full.proc is None
    workers.explain("NEW", "en", "0020482")
    assert wait(lambda: workers.explain("NEW", "en", "0020482")["status"] == "done") and workers.full.proc is not None


def test_close_stops_every_process(workers):
    workers.warm()
    assert wait(lambda: workers.fast.ready and workers.full.ready)
    procs = [workers.fast.proc, workers.full.proc]
    workers.close()
    assert all(p.wait(timeout=5) is not None for p in procs)


# ---------------------------------------------------------------- the worker's own work, on the real fixture
def test_worker_answers_a_real_fast_screen_and_explanation():
    from app import v6_sandbox_worker as worker
    world = worker.inputs()
    case = world["probe"]
    out = worker.answer(world, "fast", {"id": 1, "op": "try", "box": case["box_id"], "slot": case["target_slot"]})
    assert out["verdict"]["screen"] == "fast" and out["verdict"]["gauges"]["full_check_pending"] is True
    json.dumps(out)
    said = worker.answer(world, "fast", {"id": 2, "op": "explain", "box": case["box_id"], "lang": "th"})
    assert said["explanation"]["statements"]


def selected():
    """The boxes with a recorded Spark summary. The close-bay-9 cache (p18) names them ``model_box_ids``; its ``demo_box_ids`` is
    empty, since that recording ran no fast screens (verification.json: fast_screens 0)."""
    verification = json.loads((bench.EXPLAIN_HERO / "verification.json").read_text())
    assert verification["fast_screens"] == 0 and "selected_demo_box_ids" not in verification
    return verification["model_box_ids"]


def test_the_hero_s_recorded_cache_answers_in_both_languages_on_its_own_inputs():
    from app import v6_sandbox_worker as worker
    world = worker.inputs()
    hero = worker.hero_world(world)
    assert hero is not None and hero["edited"]
    flagged = json.loads((bench.EXPLAIN_HERO / "verification.json").read_text())["thai_sense_issues"]  # p18: some flagged, some passed
    boxes = [b for b in selected() if b not in hero["edited"]]
    picks = [next(b for b in boxes if b in flagged)] + [b for b in boxes if b not in flagged][:1]
    for box in picks:
        for lang in ("en", "th"):
            out = worker.answer(world, "fast", {"id": 1, "op": "explain", "box": box, "lang": lang})
            seen = bench.explain_view(box, lang, out["explanation"], out["basis"])
            assert seen["mode"] == "recorded" and seen["label"] == "Recorded Spark summary" and seen["statements"][0]["id"] == "summary"
            assert seen["statements"][0]["evidence"], "a model summary carries its evidence"
        sense = seen["statements"][0]["render_checks"]["thai_sense"]
        assert sense["status"] == ("flagged" if box in flagged else "passed") and sense["native_reader_reviewed"] is False
        assert seen["thai_note"] == bench.THAI_NOTE


def test_a_box_the_demo_edited_never_plays_the_hero_s_answer_for_other_cargo():
    from app import v6_sandbox_worker as worker
    world = worker.inputs()
    box = sorted(worker.hero_world(world)["edited"])[0]
    assert box in selected(), "the case that matters: this box has a recorded Spark summary of the old cargo"
    out = worker.answer(world, "fast", {"id": 1, "op": "explain", "box": box, "lang": "en"})
    seen = bench.explain_view(box, "en", out["explanation"], out["basis"])
    un = bench.demo_fixture()["modifications"][0]
    assert seen["label"].startswith("Template placement facts") and seen["recorded"] is None and "doesn't apply" in seen["note"]
    assert f"UN {un['after']['un_number']}" in seen["statements"][0]["text"] and f"UN {un['before']['un_number']}" not in out["explanation"]["text"]


def test_a_stale_explanation_cache_is_ignored_after_a_hero_switch(monkeypatch):
    from app import v6_sandbox_worker as worker
    world = worker.inputs()
    monkeypatch.setattr(bench, "configured_hero", lambda: {"instance_sha256": "0" * 64})
    assert worker.hero_world(world) is None


def test_a_report_for_other_inputs_loses_explanations_and_keeps_the_drags(monkeypatch):
    from app import v6_sandbox_worker as worker
    from stowage import checker
    monkeypatch.setattr(checker, "report_matches_inputs", lambda *a: False)
    world = worker.inputs()
    assert world["report"] is None and world["plan"] is not None


def test_explaining_a_recorded_move_reuses_its_verdict_and_starts_no_check(monkeypatch):
    from app import v6_sandbox_worker as worker
    from experiments.mvp.pipeline import sandbox
    def no_check(*a, **k):
        raise AssertionError("a recorded move must not run a check")
    monkeypatch.setattr(sandbox, "try_move", no_check)
    world = worker.inputs()
    case = next(c for c in world["cases"] if c["kind"] == "segregation")
    out = worker.answer(world, "fast", {"id": 1, "op": "explain", "box": case["box_id"], "lang": "en", "slot": case["target_slot"]})
    seen = bench.explain_view(case["box_id"], "en", out["explanation"], out["basis"])
    assert seen["move_check"] == "full" and seen["recorded"] == "Recorded answer" and seen["checked_on"].startswith("Checked on ")
    assert seen["label"].startswith("Template move facts") and any(s["id"] == "what_if" for s in seen["statements"])


# ---------------------------------------------------------------- routes
class _Conn:
    def __init__(self, request: bytes):
        self.rfile, self.wfile = io.BytesIO(request), io.BytesIO()

    def makefile(self, mode, *_):
        return self.rfile if "r" in mode else self.wfile

    def sendall(self, data):
        self.wfile.write(data)


def request(handler, method, path, body=None, headers=()):
    head = f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
    payload = json.dumps(body).encode() if body is not None else b""
    if body is not None:
        head += f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
    for line in headers:
        head += line + "\r\n"
    conn = _Conn(head.encode() + b"\r\n" + payload)
    handler(conn, ("127.0.0.1", 1), None)
    raw = conn.wfile.getvalue()
    status = int(raw.split(b" ", 2)[1])
    return status, json.loads(raw.split(b"\r\n\r\n", 1)[1] or b"null")


@pytest.fixture
def routes(workers, monkeypatch):
    monkeypatch.setattr(bench, "_SANDBOX", workers)
    return make_handler({"metadata": {}})


def test_try_routes_return_a_job_at_once_then_its_answer(routes, workers):
    started = time.monotonic()
    status, body = request(routes, "POST", "/api/v6/try", {"box": "A", "slot": "0020482"})
    assert status == 202 and time.monotonic() - started < 1
    assert wait(lambda: request(routes, "GET", f"/api/v6/try/{body['job']}")[1]["fast"] is not None)
    status, row = request(routes, "GET", f"/api/v6/try/{body['job']}")
    assert status == 200 and {"fast", "full", "elapsed_s"} <= set(row) and row["fast"]["gauges"]["frame"]["gm_m"] == 2.5


def test_try_route_refuses_bad_bodies_and_other_origins(routes):
    assert request(routes, "POST", "/api/v6/try", {"box": "A"})[0] == 400
    assert request(routes, "POST", "/api/v6/try", {"box": "A", "slot": "s", "plan": "x"})[0] == 400
    assert request(routes, "POST", "/api/v6/try", {"box": "A", "slot": "s"}, ("Sec-Fetch-Site: cross-site",))[0] == 403
    assert request(routes, "GET", "/api/v6/try/999")[0] == 404


def test_explain_route_polls_to_done_and_a_bad_lang_is_a_gap(routes):
    assert wait(lambda: request(routes, "GET", "/api/v6/explain?box=A&lang=en")[1].get("status") == "done")
    assert request(routes, "GET", "/api/v6/explain?box=A&lang=fr")[1]["available"] is False


def test_explain_route_passes_the_target_slot(routes):
    path = "/api/v6/explain?box=A&lang=en&slot=0020482"
    assert wait(lambda: request(routes, "GET", path)[1].get("status") == "done")
    assert request(routes, "GET", path)[1]["target_slot"] == "0020482"


# ---------------------------------------------------------------- the page's logic, under node
def node(expr: str):
    code = f"global.window = globalThis; require({json.dumps(str(JS))}); const L = window.StageV6Breakit.logic; console.log(JSON.stringify({expr}));"
    return json.loads(subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True).stdout)


@needs_node
def test_same_stack_spans_a_forty_foot_box():
    assert node("[L.sameStack('0100486','0090484'), L.sameStack('0100486','0110482'), L.sameStack('0100486','0140484'), L.sameStack('0100486','0100506'), L.sameStack('0100486','0100406')]") == [True, True, False, False, False]


@needs_node
def test_candidates_are_swaps_of_the_same_length_and_free_tops():
    expr = """L.candidates(
      ['0010182','0010184','0010186','0030182','0030184','0050182'],
      [['0010182','A'],['0010184','H'],['0020182','L']],
      {A:{length_ft:20},H:{length_ft:20},L:{length_ft:40}}, 'H')"""
    assert sorted((c["slot"], c["swap"]) for c in map(dict, node(expr))) == sorted([("0010182", "A"), ("0010184", None), ("0050182", None)] + [("0030184", None)])
    long_box = """L.candidates(['0010182','0030182','0050182'], [['0010182','A']], {A:{length_ft:20},H:{length_ft:40}}, 'H')"""
    assert [c["slot"] for c in node(long_box)] == ["0040182"]


@needs_node
def test_merge_frame_keeps_a_gauge_with_no_number_and_drops_stale_green():
    out = node("L.mergeFrame({gm_m:2.5,sf_n:[1,2],stability_passed:true,strength_passed:true,trim_m:-0.1},{gm_m:null,sf_n:null,trim_m:-0.2,stability_passed:null})")
    assert out == {"gm_m": 2.5, "sf_n": [1, 2], "stability_passed": None, "strength_passed": None, "trim_m": -0.2}


@needs_node
def test_needle_starts_at_from_settles_at_to_and_overshoots_only_a_little():
    start, end, peak = node("[L.needle(1,2,0,0.1), L.needle(1,2,4,0.1), Math.max(...Array.from({length:200},(_, i)=>L.needle(1,2,i/100,0.1)))]")
    assert start == pytest.approx(1) and end == pytest.approx(2, abs=1e-3) and 2 < peak < 2.45


@needs_node
def test_film_timeline_is_pure_and_covers_the_beat():
    assert node("JSON.stringify(L.beat(5.123)) === JSON.stringify(L.beat(5.123))") is True
    assert node("[L.beat(-4.1).phase, L.beat(-0.5).phase, L.beat(-0.5).hook, L.beat(0.1).kind, L.beat(3.2).phase, L.beat(6.5).phase, L.beat(8.0).phase, L.beat(9.5).kind, L.beat(10.8).phase, L.beat(13.9).phase, L.beat(13.9).kind]") == \
        ["hold", "fast", True, None, "fast", "full", "undo", "legal", "fast", "full", "legal"]


@needs_node
def test_film_picks_its_case_or_skips_the_beat():
    cases_js = "[{kind:'a',verdict:{legal:false}},{kind:'b',verdict:{legal:true}}]"
    assert node(f"[L.pickCase({cases_js},'b').kind, L.pickCase({cases_js},'legal').kind, L.pickCase({cases_js},'securing').kind, L.pickCase([],'legal')]") == ["b", "b", "a", None]
    assert node(f"L.pickCase([{{kind:'b',verdict:{{legal:true}}}}],'securing')") is None


@needs_node
def test_crane_order_is_amber_and_a_missing_number_prints_nothing():
    assert node("[L.refusedTone({outcome:{kind:'crane_order'}}), L.refusedTone({outcome:{kind:'stow_rule'}}), L.refusedTone({}), L.num(1.234,2), L.num(null,2)]") == [True, False, False, "1.23", None]


@needs_node
def test_film_cues_are_the_current_cases_and_a_ratio_prints_no_unit():
    assert node("[...new Set(L.DRAGS.map((d) => d.kind))]") == ["securing", "legal"]
    assert node("[L.fmt(1.25607,'ratio'), L.fmt(1234,'kg')]") == ["1.256", "1,234 kg"]
    assert node("[L.recordedTiming(0.37,15.4), L.recordedTiming(null,15.4), L.recordedTiming(0.37,null)]") == ["Recorded: fast screen 0.37 s, full check 15.4 s.", "Recorded: full check 15.4 s.", ""]


def test_shot_eight_captions_quote_no_timing_and_keep_spark_to_its_job():
    shots = (ROOT / "app/static/stage-shots.js").read_text()
    shot = shots[shots.index('"title": "Drag a box"'):shots.index('"title": "The copilot"')]
    assert "0.76" not in shot and not re.search(r"\d+(\.\d+)? s\b", shot)  # draft 4: timings live in the README
    assert "explains" not in shot.lower() and "says which rule" not in shot.lower()
    assert all("Fast screen, full check running" not in line for line in shot.splitlines() if '"text"' in line), "the film plays a recording, so no caption says a screen is running"


def test_the_page_says_placement_facts_not_why():
    js = JS.read_text()
    assert "Why ${" not in js and "ask why" not in js and "Placement facts for" in js
    assert "Explain this move" in js and "not a checker verdict" in js and "model language feedback, not proof" in js


def test_words_on_screen_obey_the_contract():
    text = JS.read_text() + Path(bench.__file__).read_text()
    for banned in ("instant", "real-time", "approved", "certified", "industry", "StowLM planned"):
        assert banned.lower() not in text.lower(), banned
    assert "pending" not in JS.read_text().lower()


def test_the_fixture_s_old_challenge_label_never_reaches_a_page(monkeypatch):
    """D311 (M8): the sandbox fixture says "Demo cargo and limits tightened"; only the cargo declaration changed."""
    old = "limits tightened for this challenge"
    demo = bench.read_demo()
    assert old in str(demo.get("label")), "the fixture still carries the old label: the override is what's tested"
    monkeypatch.setattr(bench, "read_demo", lambda: {**demo, "label": "Demo cargo and limits tightened for this challenge"})
    data = v6.feature(v6.sandbox)
    assert data["challenge_tag"] == bench.CHALLENGE_TAG and data["challenge_note"] == demo["label_disclosure"]
    assert old not in json.dumps(data)
    for name in ("stage-shots.js", "stage-v6breakit.js"):
        assert old not in (v6.ROOT / "app/static" / name).read_text()
