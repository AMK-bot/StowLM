"""Break-it chapter backend: drags go to two persistent workers, so a request never blocks the single-threaded server.

    POST /api/v6/try {box, slot, case?}     -> {job} at once
    GET  /api/v6/try/<job>                  -> {fast, full, elapsed_s, status, ...}
    GET  /api/v6/explain?box=&lang=en|th[&slot=]  -> the box's checked placement facts or, with slot, that move; every statement with its evidence

The fast lane answers ``fast_screen``; the full lane runs ``try_move``. **A new drag supersedes the old one:** the fast
lane keeps only the newest drag waiting, and the full lane stops a superseded check (kill and reload) because that
beats waiting out 40-odd seconds of work nobody will read. Neither lane can hold up the other. A drag in the pinned cache
replays its recording and no checker runs, so the card says "Recorded answer", never that it was checked now.

**The plan here is the configured hero with its disclosed edits undone, or the chapter is a gap.** ``derivation`` works that out
from the plan itself; the fixture's own word for it isn't taken. After a hero switch a stale demo drops out instead of disagreeing.

**The checker's answer is what's shown.** ``view`` only orders and groups the rows and names the outcome: passes as edited;
stow fine but the crane order can't reach it; stow breaks a rule. Nothing is dropped. Sentences are templates from the
checker's own fields, labelled so. Explanations say what they are: a Spark summary only where one was accepted, template facts
otherwise. The recorded cache describes the hero before its disclosed edits, so a box those edits changed gets fresh template facts.

**Fixtures change under this file.** Everything read from them goes through the small adapters here (``read_demo``,
``cases_view``, ``view``, ``measured``): a changed shape loses that panel or that case, never the chapter.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERO_FILE = Path(__file__).with_name("v6_hero.json")
FIXTURES = ROOT / "experiments/mvp/pipeline/sandbox/fixtures"
EXPLAIN_HERO = ROOT / "experiments/mvp/pipeline/explain/fixtures/hero"
PID_FILE = ROOT / "experiments/mvp/ui/cache/v6-sandbox-workers.json"
FAST_LABEL = "Fast screen, full check running"
TEMPLATE_LABEL = "Template, from the checker's report"
RECORDED_LABEL = "Recorded answer"
THAI_NOTE = "No native Thai reader has reviewed these explanations."
SPARK_SUMMARY = "Muse Spark wrote this short summary around facts software inserted and checked."
SCOPE_ONE = "This edits one box and keeps the crane order and ballast. It doesn't replan."
QUALITY = "Legal isn't the same as good."
# D311 (M8): the demo fixture's own label said "Demo cargo and limits tightened"; only the cargo declaration changed, so the page says so,
# with the fixture's disclosure beside it. The fixture's label never reaches a page.
CHALLENGE_TAG = "Demo cargo declaration changed for this challenge; no vessel or rule limit tightened"
ENV = {"PYTHONPATH": os.pathsep.join(str(ROOT / p) for p in (".", "src", "experiments/mvp/frontier", "experiments/mvp/frontier/lab")),
       "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1"}
log = logging.getLogger(__name__)

# ---------------------------------------------------------------- reading a verdict
_ENGINE = "expert.moves."
_PLAIN = (  # rule id prefix -> plain name, plain sentence
    ("expert.physical.stack_weight", "Stack weight", "This stack would weigh more than its limit allows."),
    ("imdg.segregation", "Dangerous goods too close", "These dangerous-goods boxes must be kept further apart."),
    ("iscode.loading.trim", "Trim", "The ship would sit outside its trim limit."),
    ("iscode.", "Stability", "The ship's stability would leave its limits."),
    ("iacs.member_irs.container_securing", "Lashing (our approximation, synthetic gear)", "The lashing would carry more load than its declared limit."),
    ("iacs.", "Hull strength", "The hull would carry more load than its limit."),
    ("expert.physical.tier_height", "Stack height", "This stack would stand taller than its limit."),
    ("expert.physical.no_20_on_40", "20 ft on 40 ft", "A twenty-foot box can't rest on top of a forty-foot box."),
    ("expert.physical.size_fit", "Box doesn't fit", "This box doesn't fit that slot."),
    ("expert.physical.slot_occupancy", "Slot taken", "Two boxes would share one cell."),
    ("expert.physical.reefer_plug", "No reefer plug", "A running reefer needs a plug here, and this slot has none."),
    ("expert.e2.weight_order", "Heavy above light", "A heavier box would sit on a lighter one."),
    ("expert.e8.away_from_heat", "Too close to heat", "This cargo would sit too close to a heat source."),
    ("expert.e9.over_width_length", "Oversize clearance", "Oversize cargo would block a neighbouring slot."),
    ("expert.moves.hatch_clear", "Hatch cover", "The crane sequence can't get past a hatch cover."),
    ("expert.moves.bottom_up", "Gap in the stack", "Stacks fill from the bottom, and this would leave a gap."),
    ("expert.moves.top_access", "Crane can't reach it", "The crane can only lift the top box of a stack."),
    ("checker.missing_vessel_data", "Can't be settled", "The checker can't settle this one, so it stops here."),
)
_UNSETTLED = ("checker.internal_error", "checker.missing_vessel_data")  # the check couldn't finish: no proof of a broken stow
_RULE_NOTES = {"iacs.member_irs.container_securing": "Securing uses the checker's approximation with declared synthetic gear."}
_CONDITION = re.compile(r"^\[[^\]]*\]\s*")


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(verdict: dict) -> list[dict]:
    broken = verdict.get("broken")
    return [row for row in broken if isinstance(row, dict)] if isinstance(broken, list) else []


def _boxes(row: dict) -> list:
    boxes = row.get("boxes")
    return list(boxes) if isinstance(boxes, (list, tuple)) else []


def _plain(rule: str) -> tuple[str, str | None]:
    for prefix, name, sentence in _PLAIN:
        if rule.startswith(prefix):
            return name, sentence
    return rule, None


def _rank(row: dict, cause: dict | None = None) -> tuple:
    """The deciding cause leads. Then cargo and physics rules, numbers before words; engine-replay messages follow. Stable within a rank."""
    rule = str(row.get("rule_id") or "")
    return (not cause or rule != cause.get("rule_id"), rule.startswith(_ENGINE), rule in _UNSETTLED, row.get("measured") is None or row.get("limit") is None)


def lead_rows(broken: list[dict], challenge: dict | None = None, cause: dict | None = None) -> list[dict]:
    """Group a verdict's broken rows by rule, the deciding cause first. Every rule stays listed; a snapshot's finding says it's one."""
    groups: OrderedDict[str, dict] = OrderedDict()
    for row in sorted(broken, key=lambda row: _rank(row, cause)):
        rule = str(row.get("rule_id") or "unreported")
        boxes = _boxes(row)
        group = groups.get(rule)
        if group is not None:
            group["count"] += 1
            continue
        name, sentence = _plain(rule)
        groups[rule] = {"rule_id": rule, "name": name, "sentence": sentence or _CONDITION.sub("", str(row.get("message") or "")),
                        "message": _CONDITION.sub("", str(row.get("message") or "")), "measured": row.get("measured"),
                        "limit": row.get("limit"), "unit": row.get("unit"), "boxes": boxes[:12], "n_boxes": len(boxes),
                        "source": row.get("source"), "severity": row.get("severity", "hard"), "count": 1,
                        "challenge": _is_challenge(row, challenge), "diagnostic": row.get("source") == "requested_stow_snapshot",
                        "note": _RULE_NOTES.get(rule)}
    return list(groups.values())


def _is_challenge(row: dict, challenge: dict | None) -> bool:
    """The limit or the cargo came from this demo's synthetic edits, so the card has to say so."""
    if not challenge:
        return False
    limit = row.get("limit")
    return (isinstance(limit, (int, float)) and any(abs(limit - x) < 1e-9 for x in challenge["limits"])) or \
        (str(row.get("rule_id")).startswith("imdg.") and bool(set(map(str, _boxes(row))) & set(challenge["boxes"])))


def _tidy(text: str) -> str:
    text = re.sub(r";? boxes: \[[^\]]*\]\.?", ".", re.sub(r"\[[a-z]+:call-[^\]]*\]\s*", "", text))
    text = text.replace(" Measured: not reported; limit: not reported.", "")
    return re.sub(r"-?\d+\.\d{4,}", lambda m: f"{float(m.group()):,.3f}" if abs(float(m.group())) < 100 else f"{float(m.group()):,.0f}", text)


def outcome(verdict: dict) -> dict:
    """The deciding cause of one verdict, in plain words. Pure.

    The sandbox's own ``gauges.outcome`` is used when a verdict carries it. Older verdicts are read from their rows. A row
    counts as crane order if it's an engine rule, or a replay row on a diagnostic verdict; every full check of a reached plan
    also carries ``source: proposal_replay``, so the source alone doesn't say. "Stow is fine" is only said of a full check
    that looked at the stow. ``gauges.deciding_cause`` decides when a verdict carries it: a failed crane order leads even when
    the requested-stow snapshot also breaks a rule, and that finding stays on the card as ``diagnostic``. On an older verdict,
    with no cause, a stow rule leads and ``crane_too`` keeps the crane order.
    """
    verdict = _dict(verdict)
    gauges = _dict(verdict.get("gauges"))
    if gauges.get("input_error") is True:
        return {"kind": "input_error", "title": "That drop can't be checked", "plain": "The request didn't name a real box and slot.", "rule_id": None, "crane_too": False}
    fast = verdict.get("screen") == "fast"
    if verdict.get("legal") is True:
        if fast:
            return {"kind": "clear", "title": "Nothing flagged yet", "plain": "No screened rule failed. This isn't a pass; the full check decides.", "rule_id": None, "crane_too": False}
        return {"kind": "passes", "title": "Passes as edited", "plain": "Every rule the full checker runs passed on the edited plan.", "rule_id": None, "crane_too": False}
    diagnostic = gauges.get("diagnostic_only") is True
    hard = sorted((r for r in _rows(verdict) if r.get("rule_id") and r.get("severity", "hard") == "hard"), key=_rank)
    crane = [r for r in hard if str(r.get("rule_id") or "").startswith(_ENGINE) or (diagnostic and r.get("source") == "proposal_replay")]
    stow = [r for r in hard if r not in crane and str(r.get("rule_id") or "") not in _UNSETTLED]
    unsettled = [r for r in hard if str(r.get("rule_id") or "") in _UNSETTLED]

    def broke(row: dict, kind: str, title: str) -> dict:
        rule = str(row.get("rule_id") or "unreported")
        return {"kind": kind, "title": title.format(_plain(rule)[0]), "plain": _plain(rule)[1] or _CONDITION.sub("", str(row.get("message") or "")),
                "rule_id": rule, "crane_too": bool(crane)}

    if fast:
        if stow or unsettled:
            return broke((stow or unsettled)[0], "flagged", "{}")
        if crane:
            return {"kind": "flagged_crane", "title": "The crane order can't reach it", "plain": "The full check is now looking at the stow itself.",
                    "rule_id": None, "crane_too": True}
        return {"kind": "flagged", "title": "Flagged", "plain": "The screen stopped here. The full check decides.", "rule_id": None, "crane_too": False}
    said = gauges.get("outcome")
    cause, assessment = _dict(gauges.get("deciding_cause")), _dict(gauges.get("stow_assessment"))
    fine = {"kind": "crane_order", "title": "Stow is fine, but the existing crane order can't reach it",
            "plain": "A diagnostic check of the requested stow broke no rule. A replan might.", "rule_id": cause.get("rule_id"), "crane_too": True}
    unsure = {"kind": "unresolved", "title": "Refused, cause not settled", "plain": "The checker couldn't settle the stow itself, so this edit stands refused.",
              "rule_id": None, "crane_too": bool(crane)}
    if said == "stow_fine_crane_order_failed":
        return fine
    if cause.get("kind") == "crane_order":
        snapshot = stow[0] if assessment.get("passed") is False and stow else None
        if snapshot is None and assessment.get("passed") is not False:
            return {**fine, "kind": "crane_unsettled", "title": "The existing crane order can't reach it", "plain": "The check of the requested stow didn't settle, so nothing is said about the stow."}
        name = _plain(str(snapshot.get("rule_id")))[0] if snapshot else "a rule"
        return {"kind": "crane_first", "title": "The existing crane order can't reach it", "plain": f"The diagnostic snapshot of the requested stow also breaks a rule: {name}. It's a diagnostic, not the deciding cause.",
                "rule_id": cause.get("rule_id"), "crane_too": True, "diagnostic": {"rule_id": snapshot.get("rule_id"), "name": name} if snapshot else None}
    if cause.get("kind") == "check_unavailable":
        return unsure
    if cause.get("kind") == "stow_rule":
        chosen = next((r for r in hard if r.get("rule_id") == cause.get("rule_id")), None)
        if chosen:
            return broke(chosen, "stow_rule", "Stow breaks rule: {}")
    if said == "stow_breaks_rule" and (stow or hard):
        return broke((stow or hard)[0], "stow_rule", "Stow breaks rule: {}")
    if "outcome" in gauges and said is None:
        return unsure
    if stow:
        return broke(stow[0], "stow_rule", "Stow breaks rule: {}")
    return fine if crane and not unsettled and gauges.get("checker_passed") is not False else unsure


def lifts_line(verdict: dict) -> str | None:
    """The engine's own line, both measures or neither: "+0" alone hides the later cost, and a failed replay says unavailable, never +0."""
    counts = _dict(_dict(_dict(verdict).get("gauges")).get("lift_counts"))
    now, later = counts.get("this_call_label"), counts.get("projected_later_label")
    line = counts.get("display") or (f"{now} {later}" if now and later else None)
    return line.replace("Crane lifts this call", "Extra lifts this call") if line else None  # the script's word (logic review S18); the numbers stay the engine's


def sentence(verdict: dict, explanation: dict | None, lead_rule: str | None = None) -> dict | None:
    """The status statement plus the lead rule's statement, from the explanation as given. None: the checker stands alone."""
    explanation = _dict(explanation)
    statements = [s for s in explanation.get("statements") or [] if isinstance(s, dict) and s.get("text")]
    if not statements:
        return None
    lead, rows = None, verdict.get("broken") if isinstance(verdict.get("broken"), list) else []
    usable = [i for i, row in enumerate(rows) if isinstance(row, dict)]
    if usable:
        match = [i for i in usable if rows[i].get("rule_id") == lead_rule]
        first = match[0] if match else min(usable, key=lambda i: _rank(rows[i]))
        lead = next((s for s in statements if s.get("id") == f"broken:{first}"), None)
    chosen = [s for s in statements if s.get("id") == "status" and verdict.get("screen") != "fast"] + ([lead] if lead else [])
    mode, spark = explanation.get("mode"), str(explanation.get("text") or "").startswith("Muse Spark selected")
    label = RECORDED_LABEL if mode == "recorded" else TEMPLATE_LABEL
    if mode == "recorded":
        label += " · " + ("Muse Spark, cloud, selects and orders checked statements" if spark else "template")
    return {"text": " ".join(_tidy(str(s["text"])) for s in chosen), "mode": mode, "label": label}


def view(verdict: dict, explanation: dict | None = None, challenge: dict | None = None) -> dict:
    """What the page reads: rule rows in order, the last gauge frame, the sentence. Numbers unchanged."""
    verdict, gauges = _dict(verdict), _dict(_dict(verdict).get("gauges"))
    frames = [f for f in gauges.get("frames") or [] if isinstance(f, dict)] if isinstance(gauges.get("frames"), list) else []
    said, rows = outcome(verdict), lead_rows(_rows(verdict), challenge, _dict(gauges.get("deciding_cause")))
    rows.sort(key=lambda row: row["rule_id"] != said["rule_id"])  # stable: only the deciding rule moves
    keep = ("label", "note", "approximation", "diagnostic_only", "swapped_box", "input_error", "error", "lift_counts", "fast_screen_legal",
            "outcome", "deciding_cause", "stow_assessment", "full_check_scope", "scope_note")
    return {"legal": verdict.get("legal"), "screen": verdict.get("screen"), "wall_s": verdict.get("wall_s"),
            "extra_lifts": verdict.get("extra_lifts"), "broken": _rows(verdict), "rows": rows, "outcome": said,
            "lifts": lifts_line(verdict), "scope": gauges.get("scope_note") or SCOPE_ONE,
            "quality": QUALITY if said["kind"] == "passes" else None,
            "gauges": {**{key: gauges.get(key) for key in keep}, "frame": frames[-1] if frames else None},
            "sentence": sentence(verdict, explanation, said["rule_id"])}


def checked_on(utc) -> str | None:
    day = str(utc or "")[:10]
    return f"Checked on {day}" if day else None


def stored_label(demo: dict) -> str | None:
    """Stored playback: nothing is checked at launch, so the date is the fixture's own check."""
    checked = checked_on(demo.get("created_utc"))
    computed = str(_dict(configured_hero()).get("computed_utc") or "")[:10]
    return " · ".join(filter(None, (f"Stored plan, computed on {computed}" if computed else None, RECORDED_LABEL, checked))) if checked else None


def demo_fixture() -> dict:
    path = FIXTURES / "demo.json"  # superset of demo190.json: the same cases plus their recorded fast frames
    return json.loads((path if path.is_file() else FIXTURES / "demo190.json").read_text())


def read_demo() -> dict:
    """The demo fixture, or nothing: free drags still work with no prepared cases."""
    try:
        return _dict(demo_fixture())
    except (OSError, ValueError) as error:
        log.info("no break-it demo fixture: %s", error)
        return {}


def first_case(demo: dict) -> dict | None:
    """The first usable prepared drag, for warming a worker."""
    cases = demo.get("cases")
    return next((c for c in cases if isinstance(c, dict) and c.get("box_id") and c.get("target_slot")), None) if isinstance(cases, list) else None


def source_path(demo: dict | None = None) -> Path:
    """The plan the prepared drags were recorded on: the fixture says which, so verdicts and worker can't part ways."""
    named = _dict(demo if demo is not None else read_demo()).get("source_fixture")
    path = ROOT / str(named) if named else None
    if path is not None and path.is_file():
        return path
    return ROOT / _config()["sandbox_source"]


def _config() -> dict:
    return _dict(json.loads(HERO_FILE.read_text()))


def configured_hero() -> dict | None:
    """The hero the app is set to: ``hero_slot`` of ``hero_source``. None when the config names none."""
    config = _config()
    if not (config.get("hero_source") and config.get("hero_slot")):
        return None
    return _dict(json.loads((ROOT / config["hero_source"]).read_text()).get(config["hero_slot"])) or None


_EDIT = re.compile(r"instance\.containers\[([^\]]+)\]\.(\w+)")
_DERIVED: dict[tuple, dict] = {}


def _undo_edits(source: dict, hero: dict) -> dict:
    """Undo the source's declared edits. What's left has to hash to the hero's instance and plan, or the plan isn't this hero."""
    from experiments.mvp.pipeline.contract.schema import sha256_json
    from stowage.model import Instance, MovePlan
    instance = {**source["instance"], "id": hero["instance_id"]}
    boxes = {box["id"]: dict(box) for box in instance["containers"]}
    edits = source.get("modifications") or []
    for edit in edits:
        match = _EDIT.fullmatch(str(edit.get("field")))
        if not match or match[1] not in boxes:
            raise ValueError("the demo plan declares an edit this page can't read")
        boxes[match[1]][match[2]] = edit["before"]
    instance["containers"] = [boxes[box["id"]] for box in instance["containers"]]
    plan = {**source["plan"], "id": source.get("hero_plan_id"), "instance_id": hero["instance_id"]}
    base = _dict(json.loads((ROOT / hero["base_plan"]).read_text())).get("plan") if hero.get("base_plan") else None
    if sha256_json(Instance.model_validate(instance).model_dump(mode="json")) != hero["instance_sha256"] or (
            base is not None and sha256_json(MovePlan.model_validate(plan).model_dump(mode="json")) != sha256_json(MovePlan.model_validate(base).model_dump(mode="json"))):
        raise ValueError("the break-it demo sits on another instance than the configured hero")
    count = len(edits)
    declarations = "DG declaration" if all(str(e.get("field")).endswith(".dg") for e in edits) else "cargo declaration"
    listed = " ".join(re.split(r"(?<=\.)\s", str(e["plain_words"]))[0] for e in edits if e.get("plain_words"))
    line = f"Derived from this voyage's checked plan, with {'one' if count == 1 else count} disclosed {declarations}{'' if count == 1 else 's'}."
    return {"edits": count, "line": " ".join(filter(None, (line, listed))) if count else None}


def derivation(demo: dict | None = None) -> dict | None:
    """How the break-it plan relates to the configured hero. Raises ValueError if it doesn't derive from it, or if the demo was
    recorded on another file. None when no hero is configured."""
    hero, path = configured_hero(), source_path(demo)
    if not hero:
        return None
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if demo and demo.get("source_sha256") and demo["source_sha256"] != digest:
        raise ValueError("the break-it demo was recorded on another plan file")
    key = (digest, hero.get("instance_sha256"), hero.get("base_plan"))
    if key not in _DERIVED:
        _DERIVED[key] = _undo_edits(json.loads(raw), hero)
    return _DERIVED[key]


_CACHED: dict[tuple, frozenset] = {}


def cache_requests() -> frozenset:
    """The drags the recorded cache may answer: its request list, if its bytes match the pin in app/v6_hero.json. Otherwise none,
    and every drag goes to the checker. ``lookup_cached_drag`` still checks the exact inputs on each hit."""
    path, pin = FIXTURES / "drag-cache.json", _config().get("drag_cache_sha256")
    try:
        key = (pin, path.stat().st_mtime_ns)
    except OSError:
        return frozenset()
    if key not in _CACHED:
        index = _json("drag-cache.index.json")
        good = pin and index.get("cache_sha256") == pin and hashlib.sha256(path.read_bytes()).hexdigest() == pin
        rows = index.get("requests") if good else None
        _CACHED[key] = frozenset((r.get("box_id"), r.get("target_slot")) for r in rows if isinstance(r, dict)) if isinstance(rows, list) else frozenset()
    return _CACHED[key]


def challenge_of(demo: dict) -> dict:
    limits, boxes = [], []
    edits = demo.get("modifications")
    for edit in edits if isinstance(edits, list) else []:
        if not isinstance(edit, dict):
            continue
        if isinstance(edit.get("after"), (int, float)):
            limits.append(float(edit["after"]))
        match = re.search(r"containers\[([^\]]+)\]", str(edit.get("field")))
        if match:
            boxes.append(match.group(1))
    return {"limits": limits, "boxes": boxes}


def _json(name: str) -> dict:
    try:
        return _dict(json.loads((FIXTURES / name).read_text()))
    except (OSError, ValueError):
        return {}


def _span(lo, hi) -> str | None:
    return f"load {lo:.0f}–{hi:.0f}" if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) else None


_SCREEN = ("perf_fast190_low.json", 190)  # the low-load recording, and the plan size it ran on (README); its data doesn't repeat the size
_SCREEN_TARGET_S = 2.0


def _fast_line(data: dict, boxes: int) -> dict | None:
    """The separately measured low-load screen, with its sample, its plan and its window. It isn't a figure for the plan on stage."""
    rows = [row for row in data.get("cases") or [] if isinstance(row, dict) and isinstance(row.get("wall_s"), (int, float))]
    median = data.get("median_s")
    if not rows or not isinstance(median, (int, float)):
        return None
    walls = [row["wall_s"] for row in rows]
    loads = [_dict(row.get(k)).get("load1") for row in rows for k in ("admission", "end_load")]
    window = f"load1 {min(loads):.2f}–{max(loads):.2f}" if loads and all(isinstance(n, (int, float)) for n in loads) else None
    unmet = f" The under-{_SCREEN_TARGET_S:.0f}-second target is unmet on this {len(rows)}-move sample." if median > _SCREEN_TARGET_S else ""
    return {"fast_median_s": median, "fast_min_s": min(walls), "fast_max_s": max(walls), "moves": len(rows), "boxes": boxes,
            "line": ", ".join(filter(None, (f"Fast screen {median:.3f} s median, range {min(walls):.3f}–{max(walls):.3f} s: {len(rows)} development moves on a {boxes}-box plan, not this one",
                                            "low-load window", window))) + "." + unmet}


def _full_line(data: dict) -> dict | None:
    timing = _dict(_dict(data.get("timings")).get("full_api_response"))
    median, p95, checks, boxes = timing.get("median_s"), timing.get("p95_s"), data.get("fresh_canonical_checks"), data.get("source_boxes")
    if not (isinstance(median, (int, float)) and isinstance(p95, (int, float)) and checks and boxes):
        return None
    admission = _dict(_dict(data.get("load1_readings")).get("admission"))
    workers = data.get("workers")
    return {"full_median_s": median, "full_p95_s": p95,
            "line": ", ".join(filter(None, (f"Full check {median:.1f} s median, {p95:.1f} s p95: {checks} fresh development checks", f"{boxes}-box plan, not this one",
                                            f"{workers} at once" if workers else None, _span(admission.get("min"), admission.get("max"))))) + "."}


def measured() -> dict | None:
    """Recorded timings, each line with its own scope. The screen is the README's low-load figure; the full check is the fresh holdout, on a loaded machine."""
    name, boxes = _SCREEN
    fast, full = _fast_line(_json(name), boxes), _full_line(_json("holdout.summary.json"))
    if not fast and not full:
        return None
    lines = [part.pop("line") for part in (fast, full) if part]
    return {**(fast or {}), **(full or {}), "lines": lines, "scope": "development"}


def excludes_level_trim(demo: dict, verdicts: list) -> bool:
    """Logic review S1: a tightened trim window with no level keel in it is backwards. It drops a case that turns on that limit."""
    edits = demo.get("modifications")
    backwards = any(isinstance(e, dict) and isinstance(e.get("after"), (int, float)) and
                    ((str(e.get("field")).endswith("iscode.loading.trim.maximum") and e["after"] < 0) or
                     (str(e.get("field")).endswith("iscode.loading.trim.minimum") and e["after"] > 0))
                    for e in (edits if isinstance(edits, list) else []))
    return backwards and any(str(row.get("rule_id")).startswith("iscode.loading.trim") for v in verdicts for row in _rows(_dict(v)))


_CASE_NAMES = {"legal": "A move that passes", "crane_order": "A stow the crane order can't reach", "securing": "A stow that overloads the lashings",
               "segregation": "Dangerous goods side by side"}


def _case_view(case: dict, index: int, demo: dict, challenge: dict) -> dict | None:
    case = _dict(case)
    box, slot, verdict, fast = case.get("box_id"), case.get("target_slot"), case.get("verdict"), case.get("fast")
    if not (isinstance(box, str) and isinstance(slot, str) and isinstance(_dict(verdict).get("legal"), bool)):
        return None
    if excludes_level_trim(demo, [verdict, fast]):
        return None
    kind = str(case.get("kind") or f"case{index + 1}")
    checked = checked_on(_dict(case.get("fast_recording")).get("created_utc") or demo.get("created_utc"))
    return {"kind": kind, "name": _CASE_NAMES.get(kind) or kind.replace("_", " ").capitalize(), "box": box, "slot": slot, "checked_on": checked,
            "verdict": view(verdict, case.get("explanation"), challenge),
            "fast": view(fast, case.get("fast_explanation"), challenge) if isinstance(fast, dict) else None}


def cases_view(demo: dict) -> list[dict]:
    """Whichever prepared cases the fixture has. One this can't read is left out; the others stay."""
    challenge, cases = challenge_of(demo), demo.get("cases")
    views = (_case_view(case, i, demo, challenge) for i, case in enumerate(cases if isinstance(cases, list) else []))
    return [case for case in views if case]


# ---------------------------------------------------------------- workers
class Lane:
    """One worker process, one request at a time. ``key`` is the latest-wins slot: a newer request replaces a waiting one."""

    def __init__(self, role: str, argv: list[str], *, stop_superseded: bool = False, on_spawn=None):
        self.role, self.argv, self.stop_superseded, self.on_spawn = role, argv, stop_superseded, on_spawn
        self.lock = threading.RLock()
        self.proc, self.ready, self.running, self.waiting = None, False, None, []
        self.load_s, self.closed = None, False

    @property
    def pid(self):
        return self.proc.pid if self.proc is not None else None

    def start(self) -> None:
        with self.lock:
            if self.proc is None and not self.closed:
                self._spawn()

    def _spawn(self) -> None:
        self.ready = False
        self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None, cwd=ROOT, text=True,
                                     encoding="utf-8", bufsize=1, env={**os.environ, **ENV})
        threading.Thread(target=self._read, args=(self.proc,), daemon=True, name=f"v6-sandbox-{self.role}").start()
        if self.on_spawn:
            self.on_spawn()

    def _read(self, proc) -> None:
        for line in proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            with self.lock:
                if proc is not self.proc:
                    return
                if message.get("ready"):
                    self.ready, self.load_s = True, message.get("load_s")
                elif self.running is not None and message.get("id") == self.running["id"]:
                    item, self.running = self.running, None
                    item["done"](message)
                self._pump()
        with self.lock:
            if proc is not self.proc:
                return  # replaced on purpose
            failed, self.proc, self.ready, self.running = self.running, None, False, None
            if failed is not None:
                failed["done"]({"ok": False, "error": "the worker stopped"})
            else:  # it never came up: answer now rather than respawn for ever behind a spinner
                waiting, self.waiting = self.waiting, []
                for item in waiting:
                    item["done"]({"ok": False, "error": "the worker could not start"})
            self._pump()

    def _pump(self) -> None:
        if self.closed or self.running is not None or not self.waiting:
            return
        if self.proc is None:
            self._spawn()
            return
        if not self.ready:
            return
        item = self.waiting.pop(0)
        self.running = item
        try:
            self.proc.stdin.write(json.dumps(item["request"]) + "\n")
            self.proc.stdin.flush()
        except OSError:  # the reader sees the pipe close and answers for it
            pass

    def submit(self, request: dict, done, *, key: str | None = None) -> None:
        with self.lock:
            item = {"id": request["id"], "request": request, "done": done, "key": key}
            if key is not None:
                for old in [w for w in self.waiting if w["key"] == key]:
                    self.waiting.remove(old)
                    old["done"]({"ok": False, "superseded": True})
                self.waiting.insert(0, item)
                if self.stop_superseded and self.running is not None and self.running["key"] == key:
                    old, self.running = self.running, None
                    old["done"]({"ok": False, "superseded": True})
                    self._restart()
            else:
                self.waiting.append(item)
            self._pump()

    def _restart(self) -> None:
        proc, self.proc = self.proc, None
        _stop(proc)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            proc, self.proc = self.proc, None
        _stop(proc)


def _stop(proc) -> None:
    if proc is None:
        return
    try:
        proc.stdin.close()
    except OSError:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


class Sandbox:
    def __init__(self, argv_for=None, pid_file: Path | None = None):
        argv_for = argv_for or (lambda role: [sys.executable, "-m", "app.v6_sandbox_worker", "--role", role])
        self.pid_file = pid_file
        self.fast = Lane("fast", argv_for("fast"), on_spawn=self._record_pids)
        self.full = Lane("full", argv_for("full"), stop_superseded=True, on_spawn=self._record_pids)
        self.lock = threading.Lock()
        self.jobs: OrderedDict[str, dict] = OrderedDict()
        self.answers: dict[tuple, dict] = {}
        self.counter = 0
        self.demo = None
        self.prepared = None

    def _record_pids(self) -> None:
        if self.pid_file is None:
            return
        try:
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            self.pid_file.write_text(json.dumps({"server": os.getpid(), "fast": self.fast.pid, "full": self.full.pid}))
        except OSError as error:
            log.info("no pid record: %s", error)

    def warm(self) -> None:
        self.fast.start()
        self.full.start()

    def close(self) -> None:
        self.fast.close()
        self.full.close()

    def _challenge(self) -> dict:
        if self.demo is None:
            self.demo = challenge_of(read_demo())
        return self.demo

    def _next(self) -> int:
        with self.lock:
            self.counter += 1
            return self.counter

    def start_try(self, box: str, slot: str, case: str | None = None) -> dict:
        if not all(isinstance(v, str) and 0 < len(v) < 40 for v in (box, slot)):
            raise ValueError("box and slot must be short strings")
        number = self._next()
        job = {"id": str(number), "box": box, "slot": slot, "case": case, "t0": time.monotonic(), "fast": None, "full": None,
               "fast_s": None, "full_s": None, "status": "running", "error": None, "recorded": False, "checked_on": None, "number": number}
        with self.lock:
            for old in self.jobs.values():
                if old["status"] == "running":
                    old["status"] = "superseded"
            self.jobs[job["id"]] = job
            while len(self.jobs) > 24:
                self.jobs.popitem(last=False)
        if (box, slot) in cache_requests():
            self.fast.submit({"id": 2 * number, "op": "cached", "box": box, "slot": slot}, lambda m: self._replayed(job, m), key="try")
        else:
            self._check(job)
        return {"job": job["id"]}

    def _check(self, job: dict) -> None:
        number, box, slot = job["number"], job["box"], job["slot"]
        self.fast.submit({"id": 2 * number, "op": "try", "box": box, "slot": slot}, lambda m: self._landed(job, "fast", m), key="try")
        self.full.submit({"id": 2 * number + 1, "op": "try", "box": box, "slot": slot}, lambda m: self._landed(job, "full", m), key="try")

    def _replayed(self, job: dict, message: dict) -> None:
        """A cache hit is the recording, shown as one. A miss or a failed lookup goes to the checker, never to another drag's verdict."""
        recorded = message.get("recorded") if message.get("ok") else None
        with self.lock:
            if message.get("superseded") or job["status"] != "running":
                job["status"] = "superseded" if job["status"] == "running" else job["status"]
                return
            if recorded:
                fast, full = _dict(recorded.get("fast")), _dict(recorded.get("full"))
                job.update(fast=view(fast, None, self._challenge()), full=view(full, recorded.get("explanation"), self._challenge()),
                           fast_s=fast.get("wall_s"), full_s=full.get("wall_s"), status="done", recorded=True, checked_on=checked_on(recorded.get("checked_utc")))
                return
        self._check(job)

    def _landed(self, job: dict, stage: str, message: dict) -> None:
        with self.lock:
            elapsed = round(time.monotonic() - job["t0"], 3)
            if message.get("superseded"):
                job["status"] = "superseded" if job["status"] == "running" else job["status"]
            elif message.get("ok"):
                job[stage], job[stage + "_s"] = view(message["verdict"], message.get("explanation"), self._challenge()), elapsed
                if stage == "full" and job["status"] != "failed":
                    job["status"] = "done"
            elif job["status"] != "done":
                job["status"], job["error"] = "failed", str(message.get("error"))

    def job(self, number: str) -> dict:
        with self.lock:
            job = self.jobs[number]
            elapsed = job["full_s"] if job["full_s"] is not None else round(time.monotonic() - job["t0"], 3)
            return {**{k: job[k] for k in ("id", "box", "slot", "case", "fast", "full", "fast_s", "full_s", "status", "error", "recorded", "checked_on")},
                    "elapsed_s": elapsed, "running_label": FAST_LABEL}

    def _recorded_move(self, box: str, slot: str) -> bool:
        with self.lock:
            if self.prepared is None:
                cases = read_demo().get("cases")
                self.prepared = {(c.get("box_id"), c.get("target_slot")) for c in cases if isinstance(c, dict)} if isinstance(cases, list) else set()
            return (box, slot) in self.prepared or (box, slot) in cache_requests()

    def explain(self, box: str, lang: str, target_slot: str | None = None) -> dict:
        if lang not in {"en", "th"}:
            raise ValueError("lang must be en or th")
        if not (isinstance(box, str) and 0 < len(box) < 40):
            raise ValueError("box is required")
        if target_slot is not None and not (isinstance(target_slot, str) and 0 < len(target_slot) < 40):
            raise ValueError("target slot must be a short string")
        key = (box, lang, target_slot)
        with self.lock:
            answer = self.answers.get(key)
            start = answer is None
            if start:
                self.answers[key] = answer = {"status": "running", "box": box, "lang": lang}
            elif answer["status"] == "failed":
                del self.answers[key]  # said once; the next ask tries again
        if start:  # only a move no recording covers waits on the full lane
            lane = self.full if target_slot and not self._recorded_move(box, target_slot) else self.fast
            lane.submit({"id": 2 * self._next(), "op": "explain", "box": box, "lang": lang, "slot": target_slot}, lambda m: self._explained(key, m))
        return answer

    def _explained(self, key: tuple, message: dict) -> None:
        box, lang, target_slot = key
        with self.lock:
            if message.get("ok"):
                self.answers[key] = {**explain_view(box, lang, message["explanation"], message.get("basis")), "target_slot": target_slot}
            else:
                self.answers[key] = {"status": "failed", "box": box, "lang": lang, "error": str(message.get("error"))}


_NOTES = {"hero": "About this voyage's plan as checked, before the disclosed cargo change and before any drag.",
          "edited": "This box carries the disclosed cargo change, so its recorded answer doesn't apply: these facts are for the demo cargo.",
          "demo": "About the plan as checked, before any drag."}


def explain_view(box: str, lang: str, explanation: dict, basis: dict | None = None) -> dict:
    """What the page reads. The label says what this answer is, from the answer: Spark's summary only if one was accepted."""
    statements, basis = [], _dict(basis)
    for row in explanation.get("statements") or []:
        evidence = [{"source": e.get("source"), "path": e.get("path"), "sha256": str(e.get("sha256") or ""), "href": e.get("href"),
                     "value": _brief(e.get("value")), "value_json": e.get("value")}
                    for e in row.get("evidence") or []]
        statements.append({"id": row.get("id"), "text": row.get("text"), "evidence": evidence,
                           "kind": row.get("kind"), "model_label": row.get("model_label"),
                           "render_checks": row.get("render_checks") or {}})
    mode = explanation.get("mode")
    move_verdict = next((e.get("value") for row in explanation.get("statements") or []
                         if row.get("id") == "what_if" for e in row.get("evidence") or []
                         if e.get("source") == "sandbox" and isinstance(e.get("value"), dict)), None)
    move_check = ("not_checked" if _dict(move_verdict.get("gauges")).get("input_error") else move_verdict.get("screen")) if move_verdict else (
        "not_checked" if any(str(row.get("id", "")).startswith("what_if") for row in explanation.get("statements") or []) else None)
    summary = next((s["model_label"] for s in statements if s.get("kind") == "model_summary" and s.get("model_label")), None)
    header = str(explanation.get("text") or "").split("\n", 1)[0]
    return {"status": "done", "box": box, "lang": lang, "mode": mode, "statements": statements, "text": explanation.get("text", ""), "move_check": move_check,
            "label": summary or header or TEMPLATE_LABEL, "spark": SPARK_SUMMARY if summary else None, "recorded": RECORDED_LABEL if summary or basis.get("move") == "recorded" else None,
            "checked_on": checked_on(basis.get("checked_utc")), "note": _NOTES.get(basis.get("about")), "thai_note": THAI_NOTE if lang == "th" else None}


def _brief(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 90 else text[:87] + "..."


# ---------------------------------------------------------------- launch and the routes
_SANDBOX: Sandbox | None = None
_STATE = {"failed": None, "baseline": None}


def sandbox_workers() -> Sandbox:
    global _SANDBOX
    if _SANDBOX is None:
        _SANDBOX = Sandbox(pid_file=PID_FILE)
        atexit.register(_SANDBOX.close)
    return _SANDBOX


def baseline_physics(instance, plan, report) -> dict:
    """The checked plan's departure frame, from the recorded baseline report, for the gauges before any drag."""
    from experiments.mvp.pipeline.sandbox.physics import full_gauges
    from stowage.moves import apply_plan
    block = full_gauges(instance, apply_plan(instance, plan).final_state.ship, report)
    block["n_lifts"] = len(plan.moves)
    for frame in block.get("frames", []):
        frame["step"] = len(plan.moves)
    block["keyframes"] = "final-only"
    return json.loads(json.dumps(block, default=str))


def register(config: dict, comparison_inputs: list) -> list[str]:
    """``--v6``: add the synthetic derivative as ``v6:sandbox`` and warm the workers. Failure is a clean gap, with its reason."""
    from app.inputs import LoadedInputs
    from stowage.model import CheckReport, Instance, MovePlan
    try:
        derivation(read_demo())
        source = json.loads(source_path().read_text())
        instance, plan = Instance.model_validate(source["instance"]), MovePlan.model_validate(source["plan"])
        comparison_inputs.append(LoadedInputs(instance, plan, {"label": "v6:sandbox", "planner": "supplied-plan"}))
        try:
            _STATE["baseline"] = baseline_physics(instance, plan, CheckReport.model_validate(read_demo().get("baseline_report")))
        except Exception as error:  # noqa: BLE001 - no baseline gauges is a gap in the gauges, not in the chapter
            log.info("no break-it baseline gauges: %s", error)
        sandbox_workers().warm()
    except Exception as error:  # noqa: BLE001 - no break-it chapter is a gap, never a failed launch
        _STATE["failed"] = f"{type(error).__name__}: {error}"
        return [f"break-it plan not registered, chapter dropped: {_STATE['failed']}"]
    return []


def failed() -> str | None:
    return _STATE["failed"]


def baseline() -> dict | None:
    return _STATE["baseline"]


def start_try(handler, fields: dict) -> dict:
    return sandbox_workers().start_try(fields["box"], fields["slot"], fields.get("case"))


def job(number: str) -> dict:
    return sandbox_workers().job(number)


def explain(handler, box: str, lang: str, target_slot: str | None = None) -> dict:
    return sandbox_workers().explain(box, lang, target_slot)
