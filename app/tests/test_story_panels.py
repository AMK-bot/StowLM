"""Logic review `experiments/review/v4/logic-demo.md`, stage side: P0-2, P1-1, P1-5, P1-6, P1-7, P2-5, P2-7, P2-10,
P3-2 and the securing card.

The JS tests run the production scripts in Node: stage-rules.js whole, and renderRace / renderKpis / renderSpark /
renderXray extracted from stage.js, against a stub DOM. No browser, no network, no Spark.
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.stage import PLANNER_LABELS, build_securing, compute_kpi, load_kpi_config, load_prize

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
STUBS = """
const window = {};
RULES_JS
const num = (v) => typeof v === 'number' && isFinite(v);
const esc = (s) => String(s == null ? '' : s);
const fmtInt = (v) => (num(v) ? Math.round(v).toLocaleString('en-US') : '—');
const els = {};
const classes = (id) => ({add() {}, remove() {}, toggle(c, on) { (els[id].cls = els[id].cls || {})[c] = !!on; }});
const $ = (id) => els[id] || (els[id] = {textContent: '', className: '', innerHTML: '', title: '', classList: classes(id), setAttribute() {}});
const setText = (id, v) => { $(id).textContent = v; };
const INPUT = INPUT_JSON;
"""


def node(body: str, payload: dict | None = None):
    rules = (STATIC / "stage-rules.js").read_text()
    script = STUBS.replace("RULES_JS", rules).replace("INPUT_JSON", json.dumps(payload or {})) + body
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-600:]
    return json.loads(done.stdout)


def extract(start: str, end: str = "\n  }\n") -> str:
    js = (STATIC / "stage.js").read_text()
    begin = js.index(start)
    return js[begin:js.index(end, begin) + len(end)]


COUNTS = {"loads": 22, "discharges": 0, "yard_rehandles": 15, "restows": 0, "hatch_lifts": 4, "total_lifts": 41,
          "projected_later_restows": 10, "projected_later_hatch_lifts": 8, "weighted_nonproductive": 37}
FLOOR = {"label": "Lower bound (proven floor, not necessarily reachable)", "nonproductive": 15, "projected_later": 0,
         "yard_rehandles": 15, "restows": 0, "hatch_lifts": 0, "total_lifts": 37, "weighted_nonproductive": 15}


def prefix_step(i):  # a running count far below the final totals, as at the start of any plan
    return {"i": i, "kind": "load", "counts": {"loads": i + 1, "yard_rehandles": 0, "restows": 0, "hatch_lifts": 0,
                                               "total_lifts": i + 1, "projected_later_restows": 0,
                                               "projected_later_hatch_lifts": 0, "weighted_nonproductive": 1}}


def render_race(k, *, fin=None, base=None, lb=None, meta=None, n=41):
    fn = extract("  const TWO_STEP = ")
    body = """
const steps = INPUT.steps, total = steps.length, S = {k: INPUT.k}, B = INPUT.B;
""" + fn + """
renderRace();
console.log(JSON.stringify({race: $('race').innerHTML, delta: $('race-delta').innerHTML, title: $('race-delta').title}));
"""
    fin = fin or COUNTS
    bundle = {"meta": meta or {}, "comparison": {"stowlm": {"label": "StowLM", "counts": fin},
                                                 "baseline": {"counts": base or COUNTS}, "lower_bound": lb or FLOOR,
                                                 "reasons": {}}}
    return node(body, {"steps": [prefix_step(i) for i in range(n)], "k": k, "B": bundle})


def live_cells(html):
    return [int(x) for x in re.findall(r'<td class="live[^"]*"[^>]*>(\d+)</td>', html)]


# ------------------------------------------------------------------ P1-1
def test_mid_plan_race_shows_final_counts_never_the_prefix():
    final = {**COUNTS, "weighted_nonproductive": 40}
    mid, end = render_race(20, fin=final), render_race(41, fin=final)
    assert live_cells(mid["race"]) == live_cells(end["race"])
    assert live_cells(mid["race"]) == [22, 15, 4, 41, 18, 40]  # loads, rehandles, hatch, total, later, objective
    assert "lift 20 of 41" in mid["race"] and "race-tick" not in end["race"]
    assert "finished plans only" in mid["race"]


def test_mid_plan_bars_are_final_and_have_no_ghost():
    mid = render_race(5)
    values = re.findall(r'class="bv">([\d,—]+)</span>', mid["race"])
    assert values == ["59", "59", "37"]  # 41 + 18 later, same for the rival, floor 37 + 0
    assert "ghost" not in mid["race"] and "<span>live</span>" not in mid["race"]


def test_progress_tick_sits_in_the_row_label_corner_not_a_plan_column():
    race = render_race(5)["race"]
    corner = race[race.index("<thead>"):race.index('<th class="live-h"')]
    assert "lift 5 of 41" in corner and race.count("race-tick") == 1


def test_headline_is_the_same_mid_plan_and_at_the_end():
    fin = {**COUNTS, "weighted_nonproductive": 45}
    assert render_race(3, fin=fin)["delta"] == render_race(41, fin=fin)["delta"]
    assert "final plan" not in render_race(3, fin=fin)["delta"]


def test_missing_final_count_is_a_dash_not_a_zero():
    fin = {k: v for k, v in COUNTS.items() if k != "yard_rehandles"}
    assert 'title="not reported">—' in render_race(5, fin=fin)["race"]


# ------------------------------------------------------------------ P1-6, P1-7
def test_lower_bound_label_is_a_floor():
    source = (ROOT / "app/stage.py").read_text()
    assert "proved minimum" not in source
    assert "Lower bound (proven floor, not necessarily reachable)" in source


def test_reaching_the_bound_without_proof_is_not_called_optimal():
    fin = {**COUNTS, "weighted_nonproductive": 15}
    delta = render_race(41, fin=fin, base={**COUNTS, "weighted_nonproductive": 37})["delta"]
    assert "reaches the floor" in delta and "optimal" not in delta


def test_optimal_needs_the_proof_flag_and_gap_is_not_a_target():
    fin = {**COUNTS, "weighted_nonproductive": 15}
    assert "= lower bound (optimal)" in render_race(41, fin=fin, meta={"proved_optimal": True})["delta"]
    view = render_race(41)
    assert "gap to bound 22" in view["delta"] and "not a target" in view["title"]


def test_no_baseline_branch_has_the_same_guard():
    fin = {**COUNTS, "weighted_nonproductive": 15}
    bundle_free = render_race(41, fin=fin, base={"weighted_nonproductive": None})
    assert "reaches the floor" in bundle_free["delta"] and "optimal" not in bundle_free["delta"]


def test_two_step_label_is_qualified_everywhere():
    long_form = "a simple two-step planner, modelled from one field expert's description"  # docs/headline.md
    assert PLANNER_LABELS["baseline.twostep"].lower() == long_form
    race = render_race(5)["race"].replace("&#39;", "'")
    assert "Two-step baseline" in race and long_form in race  # short form with the long form on the same panel
    assert "(industry practice)" not in race and "our model of" not in race
    for name in ("stage.py", "stage_service.py", "stage_extras.py", "static/stage.js", "static/stage.html"):
        assert "(industry practice)" not in (ROOT / "app" / name).read_text(), name
    assert '"label": TWO_STEP_LABEL' in (ROOT / "app/stage_service.py").read_text()  # baseline_entry


# ------------------------------------------------------------------ P0-2, P2-7, P3-2
def tiles(kpi):
    return node("console.log(JSON.stringify(window.StageRules.kpiTiles(INPUT.k)));", {"k": kpi})


def kpi_of(moves, hours, usd, thb=None, now=None, later=0):
    return {"moves_saved": moves, "lifts_saved_now": moves if now is None else now, "later_lifts_saved": later,
            "crane_hours": hours, "usd_per_call": usd, "thb_per_call": usd * 33 if thb is None else thb}


def test_tiles_read_as_comparisons_when_better():
    t = tiles(kpi_of(14, 0.48, 1725))
    assert [t[k]["value"] for k in ("moves", "hours", "usd", "thb")] == ["14 fewer", "0.48 fewer", "$1,725 less", "≈ ฿56,925 less"]
    assert not any(t[k]["worse"] for k in t)


def test_tiles_read_as_comparisons_when_worse():
    t = tiles(kpi_of(-8, -1.3, -520))
    assert [t[k]["value"] for k in ("moves", "hours", "usd", "thb")] == ["8 more", "1.3 more", "$520 more", "≈ ฿17,160 more"]
    assert all(t[k]["worse"] for k in t)


def test_tiles_state_a_tie():
    t = tiles(kpi_of(0, 0, 0))
    assert [t[k]["value"] for k in ("moves", "hours", "usd", "thb")] == ["0 · tie", "0.00 · tie", "$0 · tie", "≈ ฿0 · tie"]


@pytest.mark.parametrize("sign", [1, 0, -1])
def test_no_tile_can_read_as_a_signed_saving(sign):
    t = tiles(kpi_of(sign * 5, sign * 0.2, sign * 300))
    text = " ".join(t[k]["value"] for k in ("moves", "hours", "usd", "thb")) + t["moves"]["sub"]
    assert "saved" not in text and "$−" not in text and "−$" not in text and "per call" not in text


def test_moves_sub_line_sign_matches_the_tile_name():
    sub = tiles(kpi_of(-2, 0, 0, now=-3, later=1))["moves"]["sub"]
    assert sub == "+3 now · −1 later ports · this slice"  # + = more lifts than two-step


def test_null_kpi_is_all_dashes():
    t = tiles(None)
    assert {t[k]["value"] for k in ("moves", "hours", "usd", "thb")} == {"—"}
    assert not any(t[k]["available"] for k in t)


def render_kpis(kpi, meta=None):
    body = """
const B = INPUT.B;
""" + extract("  function renderKpis()") + """
renderKpis();
console.log(JSON.stringify({v: ['kpi-moves-v','kpi-hours-v','kpi-usd-v','kpi-thb-v'].map((i) => $(i).textContent),
  t: ['kpi-moves','kpi-hours','kpi-usd','kpi-thb'].map((i) => $(i).title), scn: $('kpi-scn').textContent,
  worse: $('kpi-usd').cls.worse, nulled: $('kpi-usd').cls.null}));
"""
    return node(body, {"B": {"meta": meta or {}, "kpi": kpi, "comparison": {"reasons": {"kpi": "no two-step plan"}}}})


def real_kpi(saved):
    cand = {**COUNTS, "yard_rehandles": COUNTS["yard_rehandles"] - saved, "total_lifts": COUNTS["total_lifts"] - saved}
    return compute_kpi(cand, COUNTS)


def test_kpi_strings_state_the_scale_and_the_thb_assumption():
    kpi = real_kpi(3)
    assert "this slice only" in kpi["basis"] and "not a whole call" in kpi["basis"] and kpi["scale"] == "this slice"
    view = render_kpis(kpi, {"slice_boxes": 22})
    assert view["scn"] == "mid scenario · this slice"
    assert all("This slice only (22 boxes to load), not a whole call" in t for t in view["t"])
    assert "per call" not in " ".join(view["t"]).replace("usd_per_call", "")
    rate = load_kpi_config()["thb_per_usd"]
    assert f"assumed ฿{rate:g} per USD" in view["t"][3] and "not a Bank of Thailand fixing" in view["t"][3]
    assert view["v"][3].startswith("≈ ฿")


def test_prize_tooltip_is_assumption_labelled_and_absent_from_the_tile_value():
    view = render_kpis(real_kpi(-3))
    assert "not a StowLM result" in view["t"][2]
    assert "578" not in view["v"][2] and view["worse"] is True


def test_unavailable_kpi_clears_the_previous_plans_value():
    view = render_kpis(None)
    assert view["v"] == ["—"] * 4 and view["nulled"] is True and view["t"][2] == "no two-step plan"


def test_header_html_has_no_per_call_label():
    html = (STATIC / "stage.html").read_text()
    assert "per call" not in html and "USD per" not in html and "THB per" not in html and "เที่ยว" not in html
    assert "Moves vs two-step" in html and "Crane-hours vs two-step" in html
    assert "saved" not in html[html.index('id="kpis"'):html.index('class="call"')]


def test_prize_numbers_are_the_ship_time_model():
    path = ROOT / "experiments/impact-estimate/ship_time.py"
    spec = importlib.util.spec_from_file_location("ship_time_for_prize", path)
    ship_time = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ship_time  # dataclasses resolve their module by name
    spec.loader.exec_module(ship_time)
    table = dict(ship_time.table(0.10))
    prize = load_prize()
    assert prize["avoided_pct"] == 10 and prize["export_boxes"] == 500
    assert all(s.retrieved_boxes == 500 for s in ship_time.impact.SCENARIOS)
    assert round(table["low"].total_usd) == prize["low_usd"] == 110
    assert round(table["mid"].total_usd) == prize["mid_usd"] == 578
    assert round(table["high"].total_usd) == prize["high_usd"] == 2248
    assert prize["text"] == ("each 10 % of avoidable moves ≈ $578 on a 500-box call (mid; low $110, high $2,248; "
                             "assumptions labelled, not a StowLM result)")
    assert real_kpi(1)["prize"] == prize


def test_broken_prize_file_shows_no_line(tmp_path):
    bad = tmp_path / "prize.json"
    bad.write_text("{}")
    assert load_prize(bad) is None and load_prize(tmp_path / "missing.json") is None


# ------------------------------------------------------------------ P2-10
def test_no_unpriced_hatch_label():
    assert "unpriced" not in (STATIC / "stage.js").read_text()
    recording = (ROOT / "experiments/mvp/ui/RECORDING.md").read_text()
    assert "hatch lifts unpriced" not in recording and "does not price hatch lifts" not in recording
    assert "Open on the hero plan" not in recording


# ------------------------------------------------------------------ P2-5
def render_spark(explanation):
    body = """
const B = INPUT.B, S = {selected: null}, SRC_ = null;
""" + extract("  function paragraphs(text)") + """
renderSpark();
console.log(JSON.stringify({title: $('spark-title').textContent, note: $('spark-note').textContent,
  badge: $('spark-src').textContent, body: $('spark-body').innerHTML}));
"""
    return node(body, {"B": {"explanation": explanation}})


def test_template_is_not_headed_muse_spark():
    view = render_spark({"source": "template", "label": "Deterministic template (no bound Spark text)",
                         "text": "[Deterministic template fallback] text"})
    assert view["title"] == "Plan explanation"
    assert view["note"] == "deterministic template (no Spark text bound to this plan)"
    assert "SPARK ·" not in view["badge"] and "MUSE" not in view["badge"]


def test_cached_spark_keeps_the_heading():
    view = render_spark({"source": "spark-cached", "label": "Muse Spark (cached)", "text": "text"})
    assert view["title"] == "Muse Spark" and view["badge"] == "MUSE SPARK (CACHED)"


@pytest.mark.parametrize("source", ["none", None, "something-else"])
def test_unknown_source_is_not_headed_muse_spark(source):
    assert render_spark({"source": source})["title"] == "Plan explanation"


# ------------------------------------------------------------------ P1-5
def render_xray(card):
    body = """
const B = INPUT.B, S = {k: 0, xray: true, xrayHL: INPUT.hl}, steps = [{kind: 'load'}], KIND = {};
const scene = null, bays = null, xrayByStep = new Map([[0, B.xray.steps[0]]]);
""" + extract("  function xrayInfo()", "\n  // ------------------------------------------------------------------ caption") + """
renderXray();
console.log(JSON.stringify({html: $('xray-card').innerHTML}));
"""
    return node(body, {"B": {"xray": card}, "hl": True})


def test_xray_card_uses_the_scoped_claim():
    row = {"n_legal": 3, "n_actions": 90, "top_k": [{"text": "1", "logprob": -1, "legal": True}], "legal_slots": ["a"],
           "chosen": {"text": "P001", "slot": "a", "box": "P"}, "overridden": True, "decisions": []}
    html = render_xray({"available": True, "steps": [row]})["html"]
    assert "cannot write a placement the mask forbids · the checker still decides" in html
    assert "illegal" not in html and "impossible" not in html and "cannot emit" not in html


def test_spark_highlight_point_is_scoped():
    source = (ROOT / "app/stage_extras.py").read_text()
    assert "impossible" not in source
    assert source.count("cannot write a placement the mask forbids") == 2 and source.count("the checker still decides") == 2


# ------------------------------------------------------------------ securing
def report(metrics=None, *, violations=(), ran=True, error=None, top_ran=True):
    part = {"ran": ran, "error": error, "metrics": {"condition.phase: departure": 1, **(metrics or {})}, "violations": list(violations)}
    top = {"ran": top_ran, "error": None, "metrics": {}, "violations": list(violations)}
    return {"bound": True, "report": {"parts": {"lashing": top, "condition:d:lashing": part}}}


def state(securing):
    return node("console.log(JSON.stringify(window.StageRules.securingState(INPUT.sc)));", {"sc": securing})


def test_securing_green_only_when_passed():
    sec = build_securing(report({"max_fitting_ratio": 0.9}))
    assert sec["state"] == "ok" and sec["passed"] is True and sec["reason"] is None
    st = state(sec)
    assert st == {"state": "ok", "label": "ALL WITHIN LIMIT", "cls": "ok", "reason": None}


def test_securing_ratio_over_limit_names_the_check():
    sec = build_securing(report({"max_fitting_ratio": 1.4, "max_lashing_ratio": 0.5}))
    assert sec["state"] == "over_limit" and sec["passed"] is False
    st = state(sec)
    assert st["state"] == "over_limit" and st["cls"] == "bad"
    assert st["label"] == "OVER LIMIT: Twistlocks & corner fittings 1.40"


def test_securing_part_that_did_not_run_is_not_over_limit():
    sec = build_securing(report({"max_fitting_ratio": 0.5}, top_ran=False))
    assert sec["state"] == "not_checked" and sec["passed"] is False
    st = state(sec)
    assert st["state"] == "not_checked" and st["cls"] == "warn" and "NOT CHECKED: lashing:" in st["label"]
    assert "OVER LIMIT" not in st["label"]


def test_securing_unbound_is_not_checked():
    st = state(build_securing({"bound": False}))
    assert st["state"] == "not_checked" and st["label"].startswith("NOT CHECKED: lashing not evaluated")


def test_securing_one_condition_not_run_blocks_green():
    data = report({"max_fitting_ratio": 0.5})
    data["report"]["parts"]["condition:m:lashing"] = {"ran": False, "error": "no ballast"}
    sec = build_securing(data)
    assert sec["state"] == "not_checked" and "m: no ballast" in sec["reason"] and sec["passed"] is False


def test_securing_violation_with_ratios_under_limit_names_the_rule():
    v = {"rule_id": "iacs.member_irs.container_securing", "message": "paired supports"}
    sec = build_securing(report({"max_fitting_ratio": 0.5}, violations=[v]))
    assert sec["state"] == "violation" and sec["violation_rules"] == [v["rule_id"]]
    st = state(sec)
    assert st["label"] == f"VIOLATION: {v['rule_id']}" and st["cls"] == "bad"


def test_securing_state_fails_closed_on_thin_bundles():
    assert state(None)["state"] == "not_checked"
    assert state({"available": True, "checks": [{"label": "x", "ratio": 0.5}]})["state"] == "not_checked"  # no passed flag
    over = {"available": True, "passed": True, "checks": [{"label": "x", "ratio": 1.2}]}
    assert state(over)["state"] == "over_limit"  # a contradictory passed flag cannot hide a ratio


# ------------------------------------------------------------------ dead-end label (item 11)
def test_no_non_video_surface_says_run_pending():
    for name in ("stage.py", "stage_service.py", "stage_extras.py", "stage_stowlm.py", "static/stage.js", "static/stage.html"):
        assert "run pending" not in (ROOT / "app" / name).read_text().lower(), name
