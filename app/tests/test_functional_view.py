"""app/functional_view.plan_view: draws only what the checker released (CONTRACT.md honesty rule 2).

Outcomes are the synthetic samples in fixtures/functional/outcomes (build_fixtures.py), copied into tmp dirs and
altered per test. No engine, model, network or browser."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.functional_view import NO_PLAN, NoPlan, plan_view, relaxed_label

FIXTURES = Path(__file__).parent / "fixtures" / "functional"


def call_dir(tmp_path: Path, name: str, change=None) -> Path:
    target = tmp_path / "c20261103-101500-a1b2c3"
    shutil.copytree(FIXTURES / "outcomes" / name, target)
    if change is not None:
        path = target / "run" / "outcome.json"
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))
    return target


def test_l0_pass_is_drawn_and_labelled(tmp_path):
    view = plan_view(call_dir(tmp_path, "l0"))
    assert view["status"] == "checked_plan" and view["passed"] is True and view["full_checker_pass"] is True
    assert view["label"] == "Passes the full checker" and view["level"] == 0 and view["relaxed"] is False
    assert view["broken_rules"] == [] and view["boxes_ashore"] == [] and view["partial"] is False
    assert view["call_id"] == "c20261103-101500-a1b2c3"
    assert view["synthetic_label"] == "Synthetic ship and cargo, simulated port"
    assert view["active_rules"] == 41  # from the package receipt, never a fixed count
    assert "crane moves" not in json.dumps(view)
    assert view["lifts_words"].startswith("extra crane lifts")
    # 24 boxes, 40-foot boxes drawn in both bays they span and counted once.
    assert view["boxes_drawn"] == 24
    drawn = {box["id"] for bay in view["bays"] for box in bay["boxes"]}
    assert len(drawn) == 24
    spans = [box for bay in view["bays"] for box in bay["boxes"] if box["span_bay"]]
    assert spans and all(box["size_ft"] == 40 and box["span_bay"] % 2 == 0 for box in spans)
    assert all(bay["bay"] % 2 == 1 for bay in view["bays"])
    bay1 = next(b for b in view["bays"] if b["bay"] == 1)
    assert bay1["rows"] == [4, 2, 1, 3] and bay1["deck_tiers"] == [84, 82] and bay1["hold_tiers"] == [4, 2]
    box = bay1["boxes"][0]
    assert {"id", "slot", "row", "tier", "pod", "weight_t", "dg_class", "reefer"} <= set(box)
    assert {p["code"] for p in view["pods"]} == {"SGSIN", "MYPKG"}
    assert view["confirmed_instructions"] == [{"sentence": "Nothing goes into bay seven on this call.", "rechecked": True}]


def test_relaxed_l3_partial_carries_label_rules_and_ashore(tmp_path):
    view = plan_view(call_dir(tmp_path, "relaxed-l3"))
    assert view["status"] == "relaxed_plan" and view["passed"] is False and view["full_checker_pass"] is False
    assert view["label"] == "Relaxed plan: gives up bookings or leaves boxes ashore; partial, 91.7 % aboard"
    assert [r["id"] for r in view["broken_rules"]] == ["expert.e2.weight_order", "objective.all_loaded"]
    assert [r["tier"] for r in view["broken_rules"]] == ["T4", "T3"]
    assert all(r["title"] and r["title"] != r["id"] for r in view["broken_rules"])
    assert len(view["boxes_ashore"]) == 2 and view["partial"] is True and view["boxes_aboard"] == 22
    assert view["boxes_drawn"] == 22
    assert "Passes the full checker" not in json.dumps(view)


@pytest.mark.parametrize("name", ["no-plan", "failed-l0"])
def test_failed_outcomes_raise_no_plan(tmp_path, name):
    with pytest.raises(NoPlan) as caught:
        plan_view(call_dir(tmp_path, name))
    assert NO_PLAN in str(caught.value)
    assert NO_PLAN == "No checked plan found. That isn't a proof that none exists."


def test_no_plan_keeps_the_runners_message(tmp_path):
    with pytest.raises(NoPlan) as caught:
        plan_view(call_dir(tmp_path, "no-plan"))
    assert "Runner engine deadline reached" in str(caught.value)


@pytest.mark.parametrize("change, why", [
    (lambda o: o.update(passed=False), "passed false"),
    (lambda o: o.update(checker={"passed": False}), "checker failed"),
    (lambda o: o["rules"][0].update(recheck={"passed": False}), "confirmed instruction failed its recheck"),
    (lambda o: o["evidence"].update(instruction_recheck_passed=False), "instruction recheck flag false"),
    (lambda o: o.update(level=2), "a checked plan at a relaxed level"),
    (lambda o: o.update(plan=None), "no plan"),
    (lambda o: o.update(status="needs_confirmation"), "needs confirmation"),
    (lambda o: o.update(status="error"), "error"),
])
def test_anything_short_of_a_full_pass_is_refused(tmp_path, change, why):
    with pytest.raises(NoPlan):
        plan_view(call_dir(tmp_path, "l0", change))


@pytest.mark.parametrize("change", [
    lambda o: o.update(level=0),
    lambda o: o.update(level=6),
    lambda o: o.update(level=None),
    lambda o: o.update(passed=True),
    lambda o: o.update(broken_rules=[]),
])
def test_malformed_relaxed_outcomes_are_refused(tmp_path, change):
    with pytest.raises(NoPlan):
        plan_view(call_dir(tmp_path, "relaxed-l3", change))


def test_missing_or_unreadable_outcome(tmp_path):
    with pytest.raises(NoPlan, match="hasn't written an outcome"):
        plan_view(tmp_path)
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "outcome.json").write_text("{not json")
    with pytest.raises(NoPlan, match="can't be read"):
        plan_view(tmp_path)


def test_unknown_rule_ids_fail_closed_to_t0(tmp_path):
    view = plan_view(call_dir(tmp_path, "relaxed-l3", lambda o: o.update(broken_rules=["made.up.rule"])))
    assert view["broken_rules"] == [{"id": "made.up.rule", "title": "made.up.rule", "tier": "T0"}]


def test_relaxed_labels_follow_the_ladder_wording():
    assert relaxed_label(1) == "Relaxed plan: gives up preferences"
    assert relaxed_label(2) == "Relaxed plan: gives up stowage practice"
    assert relaxed_label(3, aboard_pct=90.0, partial=True) == "Relaxed plan: gives up bookings or leaves boxes ashore; partial, 90 % aboard"
    assert relaxed_label(4) == "Breaks a class or statutory limit: needs a person's sign-off"
    assert relaxed_label(5) == "Not safe to sail: shown only to say what would have to change"
    with pytest.raises(ValueError):
        relaxed_label(0)


def test_relaxed_l4_and_l5_names(tmp_path):
    l4 = plan_view(call_dir(tmp_path / "a", "relaxed-l3", lambda o: o.update(level=4, boxes_ashore=[]) or o["evidence"].pop("completion")))
    assert l4["label"] == "Breaks a class or statutory limit: needs a person's sign-off" and l4["partial"] is False
    l5 = plan_view(call_dir(tmp_path / "b", "relaxed-l3", lambda o: o.update(level=5)))
    assert l5["label"].startswith("Not safe to sail: shown only to say what would have to change; partial,")


def test_sample_plan_views_are_current(tmp_path):
    """fixtures/functional/planviews/*.json are what plan_view returns today (rebuild with build_fixtures.py)."""
    for name in ("l0", "relaxed-l3"):
        stored = json.loads((FIXTURES / "planviews" / f"{name}.json").read_text())
        fresh = plan_view(call_dir(tmp_path / name, name))
        assert fresh == stored, f"rebuild fixtures: uv run python app/tests/fixtures/functional/build_fixtures.py ({name})"


def test_no_engine_or_recorded_sources_are_referenced():
    source = (Path(__file__).parents[1] / "functional_view.py").read_text()
    for banned in ("RecordedClient", "RecordedParser", "stored=", "lattice", "radio/readings", "v6_hero", "ui/cache", "import stowage", "from stowage"):
        assert banned not in source


def test_runner_rule_rows_without_a_confirmed_key_are_rechecked(tmp_path):
    """Real runner outcomes list post-checker rules without "confirmed"; each must still pass its recheck."""
    def strip(o):
        for row in o["rules"]:
            row.pop("confirmed", None)
    view = plan_view(call_dir(tmp_path / "a", "l0", strip))
    assert view["confirmed_instructions"] == [{"sentence": "Nothing goes into bay seven on this call.", "rechecked": True}]
    def fail(o):
        strip(o)
        o["rules"][0]["recheck"] = {"passed": False}
    with pytest.raises(NoPlan, match="failed its recheck"):
        plan_view(call_dir(tmp_path / "b", "l0", fail))
