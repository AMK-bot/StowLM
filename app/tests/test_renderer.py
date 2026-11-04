"""Execute production app.js with the shipped page's IDs and engine-derived frames.

The dependency-free Node DOM harness tests rendered markup and visible labels.
It does not establish browser paint/CSS layout; the fix log records manual checks.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from app.backend import build_replay
from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import Discharge, HatchCoverOff, HatchCoverOn, RestowOff, RestowOn


NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Renderer tests require a local Node JavaScript runtime")


def render_page(replay, *, index=0, view="operations", comparison=None, panel="stability",
                catalog=None, planner="solver.scalable", selected_instance=None):
    result = subprocess.run(
        [NODE, str(Path(__file__).with_name("renderer_harness.js"))],
        input=json.dumps(dict(replay=replay, index=index, view=view, comparison=comparison, panel=panel,
            catalog=catalog, planner=planner, selected_instance=selected_instance)),
        capture_output=True, text=True, timeout=10, check=True,
    )
    return json.loads(result.stdout)


@pytest.fixture
def layover_replay():
    instance = build_instance(layover=True)
    *_, discharge, through = instance.containers
    plan = MovePlan(id="renderer.layover", instance_id=instance.id, call_index=0, moves=[
        HatchCoverOff(call_index=0, cover_id="HC-02-1"),
        RestowOff(call_index=0, container_id=through.id, src="0010104"),
        Discharge(call_index=0, container_id=discharge.id, src="0010102", dst="B-002-01-01"),
        RestowOn(call_index=0, container_id=through.id, dst="0010102"),
        HatchCoverOn(call_index=0, cover_id="HC-02-1"),
    ])
    return build_replay(instance, plan), through.id


def test_renderer_keeps_restowed_box_visible_in_labelled_quay_buffer(layover_replay):
    replay, through = layover_replay
    page = render_page(replay, index=2)
    assert "QUAY BUFFER" in page["quay-buffer-label"]["text"]
    assert through in page["quay-buffer"]["html"]
    assert "1 box" in page["quay-buffer-label"]["text"]
    assert through not in page["yard"]["html"]
    assert through not in page["bay"]["html"]
    final = render_page(replay, index=5)
    assert through not in final["quay-buffer"]["html"]
    assert through in final["bay"]["html"]
    assert "0 boxes" in final["quay-buffer-label"]["text"]


def test_renderer_displays_discharges_and_projected_hatch_lifts(layover_replay):
    replay, _ = layover_replay
    page = render_page(replay, index=5)
    counts = replay["frames"][5]["counts"]
    assert page["discharges"]["text"] == "1"
    assert page["restows"]["text"] == "2"
    assert page["hatches"]["text"] == "2"
    assert page["total-lifts"]["text"] == "5"
    assert page["future-hatches"]["text"] == str(sum(f["hatch_cover_lifts"] for f in counts["future"]))


def test_renderer_keeps_simulator_gate_events_separate_from_crane_lifts(layover_replay):
    replay, _ = layover_replay
    # Planning replay deliberately rejects simulator gates (move-engine 2.0).
    # Inject a separately recorded simulator count to test this display contract.
    replay = deepcopy(replay)
    replay["frames"][5]["counts"]["gate_moves"] = 7
    page = render_page(replay, index=5)
    assert page["gate-moves"]["text"] == "7"
    assert page["total-lifts"]["text"] == "5"
    html = Path(__file__).parents[1].joinpath("static/index.html").read_text()
    assert "simulator events · excluded from lifts" in html


def test_renderer_checker_pass_does_not_approve_tainted_departure(layover_replay):
    replay, _ = layover_replay
    replay = deepcopy(replay)
    replay["metadata"].update(checker_status="passed", departure_approved=False, shippable=False,
        stub_tainted=True, provenance={"created_by": "imported-test", "stubs": {"solver.foo": "stub"}},
        shipping_blockers=["Stub-tainted provenance"])
    replay["checker"]["passed"] = True
    page = render_page(replay, view="checker")
    assert "NOT VALIDATED" in page["verification"]["text"]
    assert "departure not approved" in page["verification"]["text"]
    assert page["verification"]["color"] != "var(--green)"
    assert "NOT VALIDATED" in page["checker-verdict"]["text"]
    assert "departure not approved" in page["checker-verdict"]["text"]
    assert page["checker-verdict"]["className"] == "verdict-fail"
    assert not page["provenance-warning"]["hidden"]
    assert "STUB-TAINTED" in page["provenance-warning"]["text"]
    assert "solver.foo" in page["provenance-details"]["text"]
    assert "imported-test" in page["provenance-details"]["text"]


def test_renderer_clean_checker_pass_still_requires_departure_authorization(layover_replay):
    replay, _ = layover_replay
    replay = deepcopy(replay)
    replay["metadata"].update(checker_status="passed", departure_approved=False,
        shipping_blockers=["Required calls not checked"], stub_tainted=False)
    replay["checker"]["passed"] = True
    page = render_page(replay, view="checker")
    assert "departure not approved" in page["verification"]["text"]
    assert "Required calls not checked" in page["shipping-blockers"]["text"]
    assert page["checker-verdict"]["className"] == "verdict-fail"


def test_renderer_approved_departure_uses_server_shipping_gate(layover_replay):
    replay, _ = layover_replay
    replay = deepcopy(replay)
    # Synthetic complete evidence isolates the server's shipping gate. The
    # layover fixture itself has missing checks and is never a real approval.
    replay["metadata"].update(checker_status="passed", departure_approved=True,
        stub_tainted=False, validation_status="PASS")
    replay["checker"]["validation_status"] = "PASS"
    replay["checker"]["report"] = {"passed": True, "provenance": {}, "parts": {
        name: {"ran": True} for name in
        ("moves", "rules", "stability", "strength", "visibility", "lashing", "constraints")}}
    replay["checker"]["passed"] = True
    page = render_page(replay, view="checker")
    assert "departure approved" in page["verification"]["text"]
    assert page["verification"]["color"] == "var(--green)"
    assert page["checker-verdict"]["className"] == "verdict-pass"


def test_renderer_comparison_approval_uses_shippable_and_shows_taint(layover_replay):
    replay, _ = layover_replay
    side = dict(checker_passed=True, shippable=False, stub_tainted=True, shut_out=[],
        label="Tainted result", provenance={"stubs": {"solver.foo": "stub"}})
    comparison = dict(left=side, right=side, rows=[], comparable=False, saved_objective=None)
    page = render_page(replay, comparison=comparison)
    markup = page["comparison-content"]["html"]
    assert "NOT APPROVED" in markup
    assert "NOT VALIDATED" in markup
    assert "CHECKER PASSED" not in markup
    assert "STUB-TAINTED" in markup
    assert "solver.foo" in markup
    assert "check-badge pass" not in markup


def test_renderer_dg_pair_with_unresolved_dgl_shows_warning(replay_case):
    instance, plan = replay_case
    _, dg_id, special_id, _ = instance.load_list
    boxes = {box.id: box for box in instance.containers}
    boxes[special_id].dg = boxes[dg_id].dg.model_copy(deep=True)
    boxes[dg_id].dg.un_number = "1263"
    boxes[dg_id].dg.packing_group = None
    replay = build_replay(instance, plan)
    pair = next(p for p in replay["checker"]["dg"]["pairs"]
                if set(p["containers"]) == {dg_id, special_id})
    assert pair["status"] == "unavailable"
    page = render_page(replay, view="checker", panel="dg")
    markup = page["checker-content"]["html"]
    assert 'check-badge warn">unavailable</span>' in markup
    if pair["violations"]:
        assert all(v["message"] in markup for v in pair["violations"])
    else:
        assert "DG checker unavailable; no verdict for this pair." in markup
    assert 'check-badge pass' not in markup


def test_renderer_projects_restows_and_hatches_as_separate_counts(layover_replay):
    replay, _ = layover_replay
    replay = deepcopy(replay)
    replay["frames"][5]["counts"]["future"] = [
        dict(call_index=1, restow_lifts=2, hatch_cover_lifts=4),
        dict(call_index=2, restow_lifts=6, hatch_cover_lifts=2),
    ]
    page = render_page(replay, index=5)
    assert page["future"]["text"] == "8"
    assert page["future-hatches"]["text"] == "6"
    assert page["total-lifts"]["text"] == "5"


def endpoint_replay(replay):
    """Synthetic presentation contract; physics remains owned by the checker."""
    replay = deepcopy(replay)
    stability = dict(ran=True, metrics={"gm_m": 1.11}, criteria=[],
                     gz_curve=[dict(heel_rad=0., gz_m=0.), dict(heel_rad=.5, gz_m=.3)])
    strength = dict(ran=True, metrics={"max_sf_ratio": .1, "max_bm_ratio": .2},
                    stations=[dict(x_m=0., sf_n=100., bm_nm=200.,
                        min_shear_n=-1000., max_shear_n=1000.,
                        min_bending_nm=-2000., max_bending_nm=2000.)])
    departure = dict(id="departure-fixture", phase="departure", passed=True,
                     failed_parts=[], notes=[], stability=stability, strength=strength)
    arrival = deepcopy(departure)
    arrival.update(id="arrival-fixture", phase="arrival", passed=False, failed_parts=["stability"])
    arrival["stability"]["metrics"]["gm_m"] = .07
    arrival["stability"]["criteria"] = [dict(label="Arrival-only loading minimum", rule_id="loading.gm",
        measured=.07, limit=.3, passed=False)]
    replay["checker"].update(bound=True, binding_status="bound", passed=False,
        conditions=[departure, arrival], notes=[], failed_parts=["arrival-fixture:stability"])
    replay["metadata"]["departure_approved"] = False
    return replay


@pytest.mark.parametrize("panel", ["stability", "strength"])
def test_renderer_shows_both_endpoints_and_identifies_arrival_failure(layover_replay, panel):
    replay = endpoint_replay(layover_replay[0])
    page = render_page(replay, view="checker", panel=panel)
    markup = page["checker-content"]["html"]
    assert 'data-condition-id="departure-fixture"' in markup
    assert 'data-condition-id="arrival-fixture"' in markup
    assert "DEPARTURE" in markup and "ARRIVAL" in markup
    assert "Failed parts: stability" in markup
    assert "departure not approved" in page["checker-verdict"]["text"]
    if panel == "stability":
        assert "Arrival-only loading minimum" in markup
        assert "1.11 m" in markup and "0.07 m" in markup
    else:
        assert "Computed departure" in markup and "Computed arrival" in markup


@pytest.mark.parametrize("panel", ["stability", "strength"])
def test_renderer_never_borrows_departure_evidence_for_unavailable_arrival(layover_replay, panel):
    replay = endpoint_replay(layover_replay[0])
    arrival = replay["checker"]["conditions"][1]
    arrival[panel] = dict(ran=False, error="Arrival evidence unavailable", metrics={})
    arrival["failed_parts"] = [panel]
    page = render_page(replay, view="checker", panel=panel)
    markup = page["checker-content"]["html"]
    assert 'data-condition-id="arrival-fixture"' in markup
    arrival_markup = markup.split('data-condition-id="arrival-fixture"', 1)[1]
    assert "Arrival evidence unavailable" in arrival_markup
    assert "<svg" not in arrival_markup
    assert "1.11 m" not in arrival_markup


@pytest.mark.parametrize("panel", ["stability", "strength", "dg", "violations"])
@pytest.mark.parametrize("binding_status", ["unbound", "unbound report"])
def test_renderer_suppresses_every_unbound_report_panel(layover_replay, panel, binding_status):
    replay = endpoint_replay(layover_replay[0])
    replay["checker"].update(bound=False, binding_status=binding_status)
    page = render_page(replay, view="checker", panel=panel)
    assert "Unbound report" in page["checker-verdict"]["text"]
    assert "Unbound report" in page["checker-content"]["html"]
    assert "departure not approved" in page["verification"]["text"]
    assert "<svg" not in page["checker-content"]["html"]
    assert "Arrival-only loading minimum" not in page["checker-content"]["html"]


def test_renderer_displays_d38_d42_notes_as_neutral_information(layover_replay):
    replay = endpoint_replay(layover_replay[0])
    replay["checker"]["notes"] = ["D38: legacy witness metadata retained", "D42: explicit synthetic cargo evidence"]
    replay["checker"]["conditions"][1]["notes"] = ["D50: endpoint scope only"]
    page = render_page(replay, view="checker", panel="stability")
    markup = page["checker-content"]["html"]
    assert 'class="checker-note"' in markup
    assert "D38: legacy witness metadata retained" in markup
    assert "D42: explicit synthetic cargo evidence" in markup
    assert "D50: endpoint scope only" in markup
    notes_markup = markup.split('class="checker-notes"', 1)[1].split("</aside>", 1)[0]
    assert "check-badge fail" not in notes_markup
    assert "violation-row" not in notes_markup


def test_renderer_marks_missing_arrival_record_explicitly_unavailable(layover_replay):
    replay = endpoint_replay(layover_replay[0])
    replay["checker"]["conditions"] = replay["checker"]["conditions"][:1]
    page = render_page(replay, view="checker", panel="stability")
    markup = page["checker-content"]["html"]
    assert 'data-condition-id="arrival-unavailable"' in markup
    arrival_markup = markup.split('data-condition-id="arrival-unavailable"', 1)[1]
    assert "No checked arrival condition evidence available" in arrival_markup
    assert "<svg" not in arrival_markup


@pytest.mark.parametrize("missing", [("bound",), ("binding_status",), ("bound", "binding_status")])
def test_renderer_requires_explicit_report_binding(layover_replay, missing):
    replay = endpoint_replay(layover_replay[0])
    for name in missing:
        replay["checker"].pop(name, None)
    page = render_page(replay, view="checker", panel="stability")
    assert "Unbound report" in page["checker-verdict"]["text"]
    assert "Unbound report" in page["checker-content"]["html"]
    assert "<svg" not in page["checker-content"]["html"]
    assert "Arrival-only loading minimum" not in page["checker-content"]["html"]


@pytest.mark.parametrize("panel", ["dg", "violations"])
def test_renderer_identifies_endpoint_failure_in_every_diagnostic_panel(layover_replay, panel):
    replay = endpoint_replay(layover_replay[0])
    page = render_page(replay, view="checker", panel=panel)
    markup = page["checker-content"]["html"]
    assert 'data-condition-summary="departure-fixture"' in markup
    assert 'data-condition-summary="arrival-fixture"' in markup
    assert "DEPARTURE · departure-fixture" in markup
    assert "ARRIVAL · arrival-fixture" in markup
    assert "Failed parts: stability" in markup
    assert "<svg" not in markup


def test_renderer_comparison_labels_rejected_prefix_and_move_tally(replay_case):
    from app.comparison import build_comparison
    from stowage.model.moves import Load

    instance, plan = replay_case
    illegal = Load(call_index=0, container_id=instance.load_list[1],
                   src="B-001-01-01", dst="0010184")
    rejected = plan.model_copy(update={"moves": [illegal, *plan.moves, *plan.moves]})
    replay = build_replay(instance, rejected)
    page = render_page(replay, comparison=build_comparison(replay, replay))
    markup = page["comparison-content"]["html"]
    assert "executed-prefix totals" in markup
    assert "0 of 11 planned moves executed" in markup
    assert "complete plan totals" not in markup
    assert "NOT APPROVED" in markup


def test_renderer_comparison_labels_fully_replayed_sequence(replay_case):
    from app.comparison import build_comparison

    instance, plan = replay_case
    replay = build_replay(instance, plan)
    page = render_page(replay, comparison=build_comparison(replay, replay))
    markup = page["comparison-content"]["html"]
    assert "complete supplied-sequence totals" in markup
    assert "5 of 5 planned moves executed" in markup


@pytest.mark.parametrize("planner", ["solver.scalable", "baseline.twostep"])
def test_renderer_conflicting_instance_cannot_enable_live_planning(layover_replay, planner):
    replay, _ = layover_replay
    catalog = dict(instances=[dict(id=replay["instance"]["id"], size_class="tiny", conflict=True, live=False)],
                   plans=[])
    page = render_page(replay, catalog=catalog, planner=planner)
    assert page["plan-compare"]["disabled"] is True
    assert "CONFLICTED SOURCES" in page["size-label"]["text"]
    assert "Conflicting instance contents" in page["solve-progress"]["text"]
    assert "imported" in page["solve-progress"]["text"]


def test_renderer_catalog_live_false_disables_live_planning(layover_replay):
    replay, _ = layover_replay
    catalog = dict(instances=[dict(id=replay["instance"]["id"], size_class="tiny", live=False)], plans=[])
    page = render_page(replay, catalog=catalog)
    assert page["plan-compare"]["disabled"] is True
    assert "Live planning unavailable" in page["solve-progress"]["text"]


def test_renderer_conflicted_world_keeps_imported_artifacts_inspectable(layover_replay):
    replay, _ = layover_replay
    instance_id = replay["instance"]["id"]
    catalog = dict(instances=[dict(id=instance_id, size_class="tiny", conflict=True, live=False)],
        plans=[dict(id="world-a-plan", instance_id=instance_id), dict(id="world-b-plan", instance_id=instance_id)])
    page = render_page(replay, catalog=catalog, planner="imported")
    assert page["plan-compare"]["disabled"] is False
    assert page["compare-imported"]["disabled"] is False
    assert "conflict" in page["solve-progress"]["text"].lower()
    assert "identity" in page["solve-progress"]["text"].lower()


def test_renderer_displays_optional_crane_berthing_and_lane_context(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay["instance"]["port"].update(max_crane_height_tiers=8, berthing_side="starboard")
    replay["instance"]["port"]["yard"]["blocks"][0]["truck_lane_side"] = "high"
    page = render_page(replay)
    assert "H = 8 tiers" in page["crane-height"]["text"]
    assert "STARBOARD" in page["berthing-side"]["text"]
    assert "TRUCK LANE · HIGH ROW SIDE" in page["yard-meta"]["text"]


def test_renderer_legacy_json_does_not_invent_missing_operating_fields(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay["instance"]["schema_version"] = "1.8"
    replay["instance"]["port"].pop("max_crane_height_tiers", None)
    replay["instance"]["port"].pop("berthing_side", None)
    for block in replay["instance"]["port"]["yard"]["blocks"]:
        block.pop("truck_lane_side", None)
    for box in replay["instance"]["containers"]:
        box.pop("direct_delivery", None)
    page = render_page(replay)
    assert "H unavailable" in page["crane-height"]["text"]
    assert "unavailable" in page["berthing-side"]["text"]
    assert "TRUCK LANE SIDE UNAVAILABLE" in page["yard-meta"]["text"]
    assert page["direct-delivery-area"]["hidden"] is True


def test_renderer_direct_delivery_export_waits_at_ship_side_not_in_yard(layover_replay):
    replay = deepcopy(layover_replay[0])
    cid = replay["instance"]["load_list"][0]
    next(box for box in replay["instance"]["containers"] if box["id"] == cid)["direct_delivery"] = True
    for frame in replay["frames"]:
        for stack in frame["state"]["yard"]["stacks"]:
            stack["boxes"] = [box for box in stack["boxes"] if box != cid]
    page = render_page(replay)
    assert page["direct-delivery-area"]["hidden"] is False
    assert "SHIP-SIDE TRUCKS" in page["direct-delivery-label"]["text"]
    assert "1 export waiting" in page["direct-delivery-label"]["text"]
    assert cid in page["direct-delivery"]["html"]
    assert "Direct delivery" in page["direct-delivery"]["html"]
    assert cid not in page["yard"]["html"]


def test_renderer_preferences_use_only_bound_authoritative_rule_metrics(layover_replay):
    replay = deepcopy(layover_replay[0])
    replay["checker"]["report"]["parts"]["rules"]["metrics"].update(killed_slots=3, heavy_on_deck_count=2)
    before = replay["metadata"]["departure_approved"]
    page = render_page(replay)
    assert page["replay-preferences"]["hidden"] is False
    assert page["preference-killed-slots"]["text"] == "3"
    assert page["preference-heavy-on-deck"]["text"] == "2"
    assert "departure not approved" in page["verification"]["text"] and before is False
    replay["checker"].update(bound=False, binding_status="unbound report")
    assert render_page(replay)["replay-preferences"]["hidden"] is True


def test_renderer_missing_soft_preferences_are_neutral(layover_replay):
    replay = deepcopy(layover_replay[0])
    metrics = replay["checker"]["report"]["parts"]["rules"]["metrics"]
    metrics.pop("killed_slots", None)
    metrics.pop("heavy_on_deck_count", None)
    replay["metadata"].update(killed_slots=999, heavy_on_deck_count=999)
    page = render_page(replay)
    assert page["replay-preferences"]["hidden"] is True


def test_renderer_offline_explanation_is_explicitly_labelled(layover_replay):
    replay, _ = layover_replay
    side = dict(checker_passed=False, shippable=False, shut_out=[], explanation="Deterministic checked counts.")
    comparison = dict(left=side, right=side, rows=[], comparable=False, saved_objective=None)
    page = render_page(replay, comparison=comparison)
    assert "Deterministic template explanation" in page["comparison-content"]["html"]


def test_renderer_exact_solver_is_selectable_without_a_model_call(layover_replay):
    replay, _ = layover_replay
    catalog = dict(instances=[dict(id=replay["instance"]["id"], size_class="tiny", live=True)], plans=[])
    page = render_page(replay, catalog=catalog, planner="solver.exact")
    assert page["plan-compare"]["disabled"] is False
    html = Path(__file__).parents[1].joinpath("static/index.html").read_text()
    assert '<option value="solver.exact">Exact solver</option>' in html


def test_renderer_uses_canonical_vessel_display_label(layover_replay):
    replay, _ = layover_replay
    replay["metadata"]["vessel_label"] = "Synthetic feeder ~800 TEU"
    page = render_page(replay)
    assert page["vessel-name"]["text"] == "Synthetic feeder ~800 TEU"
