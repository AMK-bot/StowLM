"""Review findings P1-7 and P2-1: unavailable evidence never renders as a pass (rules, overlay, physics, gauges).

Runs the production stage scripts in Node: stage-rules.js whole, and renderRules / renderPhysics / overlayHtml as
extracted from stage.js and stage-director.js, against a stub DOM. No browser, no network.
"""
import json
import subprocess
from pathlib import Path

from app.stage import build_rules

STATIC = Path(__file__).resolve().parents[1] / "static"

STUBS = """
const window = {devicePixelRatio: 1};
RULES_JS
const num = (v) => typeof v === 'number' && isFinite(v);
const esc = (s) => String(s == null ? '' : s);
const fmtInt = (v) => (num(v) ? String(Math.round(v)) : '—');
const els = {};
const $ = (id) => els[id] || (els[id] = {textContent: '', className: '', innerHTML: '', title: ''});
const INPUT = INPUT_JSON;
"""


def node(body: str, payload: dict | None = None):
    rules_js = (STATIC / "stage-rules.js").read_text()
    script = (STUBS.replace("RULES_JS", rules_js).replace("INPUT_JSON", json.dumps(payload or {})) + body)
    return json.loads(subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout)


def extract(file: str, start: str, end: str) -> str:
    js = (STATIC / file).read_text()
    begin = js.index(start)
    return js[begin:js.index(end, begin) + len(end)]


def render_rules(families, *, done=True, meta=None):
    fn = extract("stage.js", "  const ICON = {", "\n  }\n")
    body = """
const B = INPUT.B, S = {k: INPUT.k}, total = INPUT.total;
""" + fn + """
renderRules(false);
console.log(JSON.stringify({badge: $('rules-badge'), note: $('rules-note').textContent, html: $('rules').innerHTML}));
"""
    return node(body, {"B": {"rules": {"source": "rules.json", "families": families}, "meta": meta or {}},
                       "k": 5 if done else 2, "total": 5})


def family(*statuses, status=None):
    rules = [{"id": f"R{i}", "description": f"rule {i}", "status": s} for i, s in enumerate(statuses)]
    return {"family": "f", "label": "Family", "rules": rules, **({"status": status} if status else {})}


def assert_not_green(view):
    assert "ok" not in view["badge"]["className"].split(), view["badge"]
    assert "PASS" not in view["badge"]["textContent"], view["badge"]


def test_not_run_is_not_green():
    view = render_rules([family("not_run", status="not_run")])
    assert view["badge"]["textContent"] == "NOT CHECKED"
    assert view["badge"]["className"] == "src-badge warn"
    assert "✓" not in view["html"]
    assert 'class="ico pass' not in view["html"]
    assert 'class="ico unavailable' in view["html"]


def test_unbound_checker_from_the_real_rule_builder_is_not_green():
    rules = build_rules({"bound": False}, {"containers": []}, None)
    assert rules["families"] and not rules["bound"]
    view = render_rules(rules["families"])
    assert_not_green(view)
    assert view["badge"]["textContent"] == "NOT CHECKED"
    assert 'class="ico pass' not in view["html"] and "✓" not in view["html"]


def test_mixed_tally_counts_each_bucket_separately():
    fams = [family("pass", "pass", "fail"), family("not_run", "n/a"), family("n/a", "n/a", "pass")]
    t = node("console.log(JSON.stringify(window.StageRules.tally(INPUT.fams)));", {"fams": fams})
    assert t == {"pass": 3, "fail": 1, "unavailable": 1, "na": 3, "total": 8}
    view = render_rules(fams)
    assert view["badge"] == {**view["badge"], "textContent": "FAILED 1", "className": "src-badge fail"}
    assert "3 pass · 1 fail · 1 unavailable · 3 not applicable" in view["note"]


def test_unavailable_without_failure_is_amber_with_a_ratio():
    view = render_rules([family("pass", "pass", "not_run", "n/a")])
    assert view["badge"]["textContent"] == "UNAVAILABLE 1/3"
    assert view["badge"]["className"] == "src-badge warn"
    assert 'class="ico pass' not in view["html"].split("</summary>")[0]  # the family row is not green either
    assert 'class="ico unavailable' in view["html"].split("</summary>")[0]


def test_all_na_family_is_grey_and_not_a_pass():
    view = render_rules([family("n/a", "n/a", status="n/a")])
    assert_not_green(view)
    assert view["badge"]["textContent"] == "N/A"
    head = view["html"].split("</summary>")[0]
    assert 'class="ico na' in head and '<span class="cnt">n/a</span>' in head


def test_all_na_family_beside_passes_stays_grey():
    view = render_rules([family("pass", "pass"), family("n/a", status="n/a")])
    assert view["badge"]["textContent"] == "2/2 PASS"
    assert view["badge"]["className"] == "src-badge ok"
    assert 'class="ico na' in view["html"]


def test_unknown_or_missing_status_fails_closed():
    for bad in ("skipped", "PASS", "passed", "", None):
        view = render_rules([family(bad)])
        assert_not_green(view)
        assert view["badge"]["textContent"] == "NOT CHECKED", bad
        assert 'class="ico unavailable' in view["html"], bad


def test_empty_or_inconsistent_family_cannot_read_as_pass():
    assert render_rules([{"family": "f", "rules": [], "status": "pass"}])["badge"]["textContent"] == "NOT CHECKED"
    # a family status worse than its rows is kept
    view = render_rules([family("pass", status="not_run")])
    assert view["badge"]["className"] == "src-badge warn"
    assert render_rules([family("pass", status="fail")])["badge"]["className"] == "src-badge fail"
    # a family status that disagrees upward does not launder a row
    assert render_rules([family("not_run", status="pass")])["badge"]["className"] == "src-badge warn"
    # a violation count outranks a pass status
    fam = family("pass")
    fam["rules"][0]["violations"] = 2
    assert render_rules([fam])["badge"]["textContent"] == "FAILED 1"


def test_pass_only_is_green():
    view = render_rules([family("pass", "pass", status="pass")])
    assert view["badge"]["textContent"] == "2/2 PASS"
    assert view["badge"]["className"] == "src-badge ok"
    assert 'class="ico pass' in view["html"]


def test_before_the_plan_ends_nothing_is_judged():
    view = render_rules([family("pass")], done=False)
    assert view["badge"]["textContent"] == "checking…"
    assert "✓" not in view["html"]


def test_no_plan_outcome_keeps_its_badge():
    view = render_rules([family("not_run")], meta={"model_outcome": {"kind": "dead_end"}})
    assert view["badge"]["textContent"] == "NO PLAN · NOT CHECKED"
    assert 'class="ico pass' not in view["html"]


# ---------------------------------------------------------------- director overlay
def overlay(meta, families):
    fn = extract("stage-director.js", "  function overlayHtml(rt, o) {", "\n  }\n")
    return node(fn + """
console.log(JSON.stringify(overlayHtml({B: INPUT.B}, {type: 'verdict'})));
""", {"B": {"meta": meta, "rules": {"families": families} if families is not None else None}})


APPROVED = {"checker_passed": True, "departure_approved": True, "notice": "synthetic"}


def test_overlay_enumerates_mixed_statuses():
    html = overlay({"checker_passed": False, "departure_approved": False, "notice": "n"},
                   [family("pass", "pass", "fail"), family("not_run", "n/a")])
    assert "✕ NOT APPROVED" in html
    assert "2 pass · 1 fail · 1 unavailable · 1 not applicable" in html
    assert "the rest are not applicable" not in html


def test_overlay_all_pass_with_na():
    html = overlay(APPROVED, [family("pass", "pass"), family("n/a")])
    assert "✓ CHECKER PASS · DEPARTURE APPROVED" in html
    assert "2 pass · 0 fail · 0 unavailable · 1 not applicable · synthetic" in html


def test_overlay_omits_counts_when_the_bundle_has_none():
    html = overlay(APPROVED, None)
    assert "demo rules" not in html.lower() and "not applicable" not in html
    assert '<div class="dc-meta">synthetic</div>' in html


def test_overlay_stamp_follows_the_bundle_verdict_only():
    green = [family("pass")]
    assert "✕ NOT APPROVED" in overlay({"checker_passed": True, "departure_approved": False}, green)
    assert "✕ NOT APPROVED" in overlay({"checker_passed": None, "departure_approved": None}, green)
    assert "✕ NOT APPROVED" in overlay({}, green)
    # a verdict the checker gave stands, but the not-run rule is still counted as unavailable, not as a pass
    html = overlay(APPROVED, [family("pass", "not_run")])
    assert "1 pass · 0 fail · 1 unavailable" in html


def test_override_card_does_not_claim_legal_without_evidence():
    fn = extract("stage-director.js", "  function overlayHtml(rt, o) {", "\n  }\n")
    body = fn + """
console.log(JSON.stringify(INPUT.rows.map((x) => overlayHtml({B: {meta: {}}, xrayRow: x}, {type: 'override'}))));
"""
    base = {"chosen": {"text": "A"}, "n_actions": 10, "n_legal": 4}
    clean, missing, other = node(body, {"rows": [
        {**base, "decisions": [{"field": "box", "overridden": False}]},
        {**base, "decisions": []},
        {**base, "decisions": [{"field": "action", "overridden": True, "top1": "x"}]}]})
    assert "MODEL'S OWN CHOICE" in clean
    for card in (missing, other):
        assert "MODEL'S OWN CHOICE" not in card and "all legal" not in card


# ---------------------------------------------------------------- physics
def physics_badge(frame, physics=True, prior=None):
    """renderPhysics with the production stage-rules.js; `prior` renders first, to catch a stale badge."""
    fn = extract("stage.js", "  function renderPhysics(force) {", "\n  }\n")
    body = """
const B = {physics: INPUT.physics, geometry: {}}, S = {k: 3};
let lastFrameKey = null, current = INPUT.prior || INPUT.frame, lines = [];
const frameAt = () => current;
const L = {gauges: {gm() {}, heel() {}, draught() {}, gz() {}, line(c, v) { lines.push(v.passed); }}};
""" + fn + """
if (INPUT.prior) { renderPhysics(true); current = INPUT.frame; }
renderPhysics(true);
console.log(JSON.stringify({...$('phys-status'), lines: lines.slice(-2)}));
"""
    return node(body, {"physics": {"frames": []} if physics else None, "frame": frame, "prior": prior})


GOOD = {"step": 3, "stability_passed": True, "strength_passed": True}


def test_physics_green_needs_both_verdicts_true():
    st = physics_badge(GOOD)
    assert (st["textContent"], st["className"]) == ("WITHIN LIMITS", "src-badge ok")
    assert st["lines"] == [True, True]


def test_physics_error_or_null_flags_are_not_within_limits():
    frames = [{"step": 3, "error": "boom", "stability_passed": True, "strength_passed": True},
              {"step": 3, "stability_passed": None, "strength_passed": None},
              {"step": 3, "stability_passed": True, "strength_passed": None},
              {"step": 3, "stability_passed": True},
              {"step": 3}]
    for frame in frames:
        st = physics_badge(frame)
        assert st["textContent"] == "NOT MEASURED", frame
        assert st["className"] == "src-badge warn", frame


def test_physics_failure_stays_red():
    st = physics_badge({"step": 3, "stability_passed": True, "strength_passed": False, "strength_violations": ["X"]})
    assert (st["textContent"], st["className"], st["title"]) == ("OUTSIDE LIMITS", "src-badge fail", "X")


def test_physics_missing_frame_clears_a_stale_green_badge():
    st = physics_badge(None, prior=GOOD)
    assert st["textContent"] == "NOT MEASURED" and st["className"] == "src-badge warn"


def gauge(call: str, v: dict):
    body = (STATIC / "stage-gauges.js").read_text() + """
const texts = [], strokes = [];
const g = new Proxy({}, {
  get: (t, k) => k === 'fillText' ? (s) => texts.push(s) : k === 'measureText' ? () => ({width: 10})
    : k === 'createLinearGradient' ? () => ({addColorStop() {}, green: true}) : (k in t ? t[k] : () => {}),
  set: (t, k, val) => { t[k] = val; if (k === 'strokeStyle') strokes.push(val); return true; },
});
window.StageLib.gauges[INPUT.call]({clientWidth: 300, clientHeight: 160, getContext: () => g}, INPUT.v);
console.log(JSON.stringify({texts, green: strokes.some((s) => s && s.green)}));
"""
    return node(body, {"call": call, "v": v})


def curve(**over):
    return {"title": "SF", "xs": [0, 1], "ys": [1, 2], "lo": [-5, -5], "hi": [5, 5], "passed": True, **over}


def test_curve_within_limits_needs_limits_and_the_part_verdict():
    assert "within limits" in gauge("line", curve())["texts"]
    for over in ({"passed": None}, {"passed": False}, {"lo": None, "hi": None}, {"lo": [None, None], "hi": [None, None]}):
        out = gauge("line", curve(**over))["texts"]
        assert "within limits" not in out and "limits not checked" in out, over


def test_curve_over_a_limit_is_outside_even_without_a_verdict():
    assert "OUTSIDE LIMITS" in gauge("line", curve(ys=[1, 9], passed=None))["texts"]


def test_curve_with_no_data_says_nothing_about_limits():
    out = gauge("line", curve(ys=[], reason="physics pending"))["texts"]
    assert "within limits" not in out and "physics pending" in out


def test_gm_arc_is_green_only_against_a_limit():
    assert gauge("gm", {"gm": 1.2, "min": 0.15})["green"]
    assert not gauge("gm", {"gm": 1.2, "min": None})["green"]


# ---------------------------------------------------------------- one aggregation, loaded first
def test_helper_is_the_only_aggregation_and_loads_first():
    html = (STATIC / "stage.html").read_text()
    rules = html.index('src="/stage-rules.js"')
    for name in ("stage-director.js", "stage-gauges.js", "stage.js"):
        assert rules < html.index(f'src="/{name}"')
    for name in ("stage.js", "stage-director.js"):
        assert "status === 'pass'" not in (STATIC / name).read_text()


def test_stage_rules_is_served():
    from app import server

    assert server.static_name("/stage-rules.js") == "stage-rules.js"
