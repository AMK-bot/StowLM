"""Video director (/stage?video=1). Variant v6 is the film: the 12 shots of script-v6.md (draft 5), each shot's fallback, its labels, and the
banned wording over every v6 string. Variant v5 stays reachable only as ?variant=v5, a rehearsal: the tests below the "v5 rehearsal"
line pin that config against script.md (draft 2). Story logic runs through app/static/stage-story.js under node, so what is tested is
what plays."""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.v6 import figures
from app.tests.report_helpers import historical

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
RUNS = ROOT / "experiments/mvp/runs"
VIDEO = ROOT / "experiments/mvp/video"
SCRIPT = VIDEO / "script.md"
SCRIPT_V6 = VIDEO / "script-v6.md"
SELECTOR_FIELDS = {"run": "run", "instance": "instance_id", "method": "planner"}  # shot key -> /api/catalog plan key
HERO, SECOND, THIRD = "random.small.29000201.0.7", "random.small.29000022.0.7", "random.small.29000102.0.7"
PLACEHOLDER = re.compile(r"\{\{NUMBER: ([^{}]+)\}\}")
ALL_PLACEHOLDERS = re.compile(r"\{\{(?:NUMBER: )?([^{}]+)\}\}")

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
pytestmark = needs_node


def shots_config(path: Path = STATIC / "stage-shots.js") -> dict:
    text = path.read_text()
    body = text[text.index("{", text.index("window.STAGE_SHOTS")): text.rindex("}") + 1]
    return json.loads(body)


def node(expr: str, shots: Path = STATIC / "stage-shots.js"):
    """Evaluate expr with the real page files loaded; S = StageStory, C = raw config, R = resolved v5, V = configured variant."""
    code = (f"global.window = globalThis; require({json.dumps(str(shots))}); require({json.dumps(str(STATIC / 'stage-fillv5.js'))});"
            f"const S = require({json.dumps(str(STATIC / 'stage-story.js'))}); const C = window.STAGE_SHOTS, V = window.STAGE_VARIANT;"
            f"const R = S.resolveVariant(C, 'v5'), R6 = S.resolveVariant(C, 'v6');"
            f"console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True).stdout)


CONFIG = {}


def cfg() -> dict:
    return CONFIG.setdefault("v5", node("R"))


def shot(sid) -> dict:
    return next(s for s in cfg()["shots"] if str(s["id"]) == str(sid))


def script_rows() -> dict:
    rows = {}
    for line in SCRIPT.read_text().splitlines():
        m = re.match(r"^\| (\d+[a-c]?) \| \d+:\d+–\d+:\d+ \| (\d+) s \| ", line)
        if m:
            cells = line.strip().strip("|").split(" | ")
            title = re.match(r"\s*\*\*(.+?)\*\*", cells[3])
            caption = re.sub(r"\[\[IF [a-z-]+: .*?\]\]", "", cells[5]).replace("`", "").strip()
            rows[m[1]] = {"duration": int(m[2]), "title": title[1].rstrip("."), "caption": caption}
    return rows


# ------------------------------------------------------------------ v6: the film
def v6() -> dict:
    return CONFIG.setdefault("v6", node("R6"))


def v6_shot(sid) -> dict:
    return next(s for s in v6()["shots"] if str(s["id"]) == str(sid))


def v6_script() -> dict:
    """Shot number -> {title, duration, screen} from the script's headings and On screen lines."""
    text = SCRIPT_V6.read_text()
    shots = {}
    for m in re.finditer(r"^### (\d+) · (.+?) · \d+:\d+–\d+:\d+ · (\d+) s$(.*?)(?=^### |^## )", text, re.M | re.S):
        screen = re.search(r"^- \*\*On screen:\*\* (.*)$", m[4], re.M)
        shots[m[1]] = {"title": m[2], "duration": int(m[3]), "screen": screen[1] if screen else ""}
    return shots


def bare(text: str) -> str:
    return text.replace('"', "").replace("**", "").replace("`", "").rstrip(". ")  # markdown emphasis and code marks aren't on screen


def test_variant_is_one_config_line_and_v6_with_v5_as_rehearsal():
    lines = re.findall(r"^window\.STAGE_VARIANT = '(\w+)';$", (STATIC / "stage-shots.js").read_text(), re.M)
    assert lines == ["v6"] and node("V") == "v6"
    assert node("S.chooseVariant(C, 'v6', null)") == {"variant": "v6", "rehearsal": False, "error": None}
    assert node("S.chooseVariant(C, 'v6', 'v5')") == {"variant": "v5", "rehearsal": True, "error": None}
    bad = node("S.chooseVariant(C, 'v6', 'worse')")  # the retired variants are gone, and asking for one fails closed
    assert bad["variant"] == "v6" and bad["error"] and not bad["rehearsal"]
    assert node("S.chooseVariant(C, 'nonsense', null)")["variant"] is None


def test_v6_durations_and_titles_follow_the_script():
    c, table = v6(), v6_script()
    assert c["script"] == "experiments/mvp/video/script-v6.md" and list(table) == [str(n) for n in range(1, 13)]
    assert {str(s["id"]): s["duration_s"] for s in c["shots"]} == {k: v["duration"] for k, v in table.items()}
    assert {str(s["id"]): s["title"] for s in c["shots"]} == {k: v["title"] for k, v in table.items()}
    sum_line = re.search(r"^Sum: .* = (\d+) s\.$", SCRIPT_V6.read_text(), re.M)
    assert sum(s["duration_s"] for s in c["shots"]) == c["total_s"] == int(sum_line[1])


def test_v6_timelines_stay_inside_each_shot():
    for s in v6()["shots"]:
        for form in (s, s.get("fallback") or {}):
            dur = form.get("duration_s", s["duration_s"])
            for key in ("captions", "overlays", "focus"):
                for it in form.get(key, []):
                    assert 0 <= it["from"] < it["to"] <= dur, (s["id"], it)
        if s.get("v6"):  # a chapter plays from its start, except shot 9, which opens after the small-section beat it cut (film-judge edit 3)
            assert s["v6"]["from"] == 0 or (s["id"] == 9 and s["v6"]["from"] > 0), s["id"]
            for h in [*([s["v6"]["hole"]] if s["v6"].get("hole") else []), *s["v6"].get("holes", [])]:
                assert 0 <= h["from"] < h["to"] <= s["duration_s"], (s["id"], h)


def film_figures() -> dict:
    return CONFIG.setdefault("figures", figures()["figures"])


def film_xray() -> dict:
    from app import v6
    return CONFIG.setdefault("xray", v6.xray())


# Draft 5's names for the old hero's figures, retired: shot 7's numbers are the lattice facts file's, served as change_* by /api/v6/figures.
SCRIPT_RETIRED = {"hero_plan_date": "change_plan_date", "hero_peak_heel": "change_peak_heel", "hero_engine": "change_engine",
                  "hero_boxes": "change_boxes", "hero_forced_boxes": "change_forced"}


def spoken(text: str) -> str:
    """Labels the flow supplies at boot (what the check really was, what the trace really is), as the script words them, and each figure as /api/v6/figures serves it:
    the script's words must match the filled caption, so a fixture that moves away from the script fails here."""
    for old, new in SCRIPT_RETIRED.items():
        text = text.replace("{{" + old + "}}", "{{" + new + "}}")
    for name, words in (("{{hero_recheck}}", "rechecked now"), ("{{xray_source_label}}", "Recorded answer"), ("{{reader_label}}", "Recorded answer"),
                        ("{{hero_rules_active}}", "54"), ("{{hero_rules_off}}", "8"), ("{{hero_heel_limit}}", "2"), ("{{replay_drags}}", "25"),
                        ("{{copilot_lift_gap}}", "12"), ("{{copilot_scripted_replies}}", "two"), ("{{cold_replan_speed}}", "3")):  # the script's literals; on screen they come from the receipt, the gate or the take
        text = text.replace(name, words)
    xray = film_xray()  # the trace's own counts, so a trace that moves away from the script fails here
    text = text.replace("{{xray_blocked_tokens}}", str(xray["blocked"])).replace("{{xray_tokens}}", str(xray["tokens"]))
    for name, row in film_figures().items():
        text = text.replace("{{" + name + "}}", row["value"])
    return text


# Words the script has wrong, each with its reason. Empty when the script catches up (draft 4 did).
# Shot 5's first segment is a stage direction (which label to use), not a caption: the caption is "Live, Wi-Fi off" when earned, else the reader's own label.
# Shot 6's heading line is a stage direction too: the x-ray chapter writes the heading from the trace's own first block (stage-v6xray.js "Token “…” blocked").
CHAPTER_SOURCE = "".join(p.read_text() for p in sorted(STATIC.glob("stage-v6*.js")))
LOWER_THIRD_ENDS = " (the lower third ends here). "
V6_SOURCE = (ROOT / "app/v6.py").read_text()  # the row tags are built there (_voice_worded)
SCRIPT_SUPERSEDED = ('Live, Wi-Fi off, or network blocked for the reader', 'heading Token',
)
# What an "otherwise" says when the evidence doesn't support the script's line. Draft 4 has none.
CAPTION_NOT_IN_SCRIPT = ("Live, on this laptop",  # D296: shown only after a finished live read; the script bans "Wi-Fi off" for a recorded take
                         )


def screen_line(text: str) -> str:
    """Shot 9's line names its two beats in prose; the beats' labels are the segments."""
    return re.sub(r"(?i)(^|\. )(first|second|third) beat: ", " · ", text).strip(" ·")


# Stage directions in an On screen line: each names what must be on screen instead, found in the shot's config or its chapter's code.
STAGE_DIRECTIONS = {
    "rule box labelled Confirmed rule": "<span>Confirmed rule</span>",  # shot 7, the replan chapter's rule box
    "counter N of": "`of ${count(run.total)} moves`",  # shot 7, never "crane lifts"
    "end frame holds at": "more: the search also rearranged these",  # shot 7, the facts file's forced and rearranged counts
    "footer: Enforced by software": "wrap.querySelectorAll('.v6c-score .v6c-more').forEach((n) => n.remove())",  # shot 9 takes "or drop the footer" in the film
    "one frozen pipeline, manifest": "one frozen pipeline, manifest {{package_manifest}}",  # shot 10, and the receipt line leaves it out in the film:
    "the first reader step captioned": "'reader (software pre-check)'",
    "low $": "low ${{impact_low_billion}} billion",  # shot 12: on the card, never its headline (checked below)
    "Projection chip, top left": '"type": "chip", "text": "Projection"',  # shot 12: the chip overlay, until the end card covers the frame
}
# Shot 12 names where each line sits on the card; the words after the place are what must be on it.
LAYOUT = ("top, huge: ", "under it: ", "middle, large: ", "fine print, at least 22 px, not monospace: ", "lower third: ", "corner tag: ")
# Segments of an On screen line the film can't show truthfully, each with its reason (reported to the script's owner).
SCRIPT_NOT_SHOWN = {"6": ("selected",)}  # it labels the selected before-trace; the leader's film-judge brief replaced the trace with the card, so no trace is on screen


def test_v6_captions_are_verbatim_and_every_segment_of_the_script_reaches_its_shot():
    for sid, row in v6_script().items():
        s = v6_shot(sid)
        script = bare(spoken(screen_line(row["screen"])))
        for cap in s.get("captions", []):
            if cap.get("extra"):
                continue
            for text in (cap["text"], cap.get("otherwise")):
                for seg in re.sub(r"\[\[IF [a-z-]+: (.*?)\]\]", r"\1", text or "").split(" · "):
                    if seg.strip() and seg.strip() not in CAPTION_NOT_IN_SCRIPT:  # "otherwise" is the script's "If cut" wording, not its On screen line
                        assert bare(spoken(seg.strip())) in (script if text == cap["text"] else bare(SCRIPT_V6.read_text())), (sid, seg)
        used = [v6()["cards"][o["card"]] for o in s.get("overlays", []) if o.get("card")]
        blob = bare(spoken(json.dumps([s, used], ensure_ascii=False).replace('\\"', '"')))
        for seg in re.split(r" · |\[\[IF [a-z-]+: |\]\]", script):
            seg = seg.strip().rstrip(" ·")
            if sid == "1" or not seg or seg.startswith(("End card", "[generated narration", "[Narration", *SCRIPT_SUPERSEDED)) or seg in SCRIPT_NOT_SHOWN.get(sid, ()):
                continue  # 1: the slams and labels are its own overlays and captions; 12: the narration line waits for a generated voice (voice_line)
            seg = next((seg[len(x):] for x in LAYOUT if seg.startswith(x)), seg).replace(" (second-largest text on the card)", "")
            if LOWER_THIRD_ENDS in seg:  # the script marks where the caption stops; what follows is a direction for the chapter, not words on the caption
                seg, tail = seg.split(LOWER_THIRD_ENDS, 1)
                direction = tail.split("Direction, not a caption:", 1)[-1]
                for words in re.findall(r'"([^"]+)"', direction):
                    shown = words.replace("bay 9 garbled", "garbled")  # the tag is built per wording from its rule's bay: f"bay {bay} garbled"
                    assert shown == "lost" or shown in V6_SOURCE, (sid, words)  # "never 'lost'": the tag text has no such word
            direction = next((v for k, v in STAGE_DIRECTIONS.items() if seg.startswith(k)), None)
            if direction is not None:
                assert bare(spoken(direction)) in blob + CHAPTER_SOURCE, (sid, seg)
                continue
            assert seg.replace(", then the verdict", "") in blob, (sid, seg)
    assert "!film && x.pipeline" in (STATIC / "stage-v6package.js").read_text()  # shot 10: the manifest shown once
    assert "low" not in v6()["cards"]["forward"]["headline"]  # shot 12: the low case is never the headline


def test_v6_chapters_resolve_and_every_chapter_shot_has_a_fallback():
    order = {s["id"]: node(f"S.shotChapters({json.dumps(s)})") for s in v6()["shots"] if s.get("v6")}
    assert order == {1: ["voice", "reading", "replan", "breakit"], 3: ["intake", "hook"], 4: ["voice"], 5: ["reading"], 6: ["reading"], 7: ["replan"],
                     8: ["breakit"], 9: ["copilot"], 10: ["package"]}  # film-judge: the cold open cuts through four chapters, shot 3 opens on the hook's slams, shot 6's trace became a card
    source = "".join(p.read_text() for p in STATIC.glob("stage-v6*.js"))
    for chapter in {c for cs in order.values() for c in cs}:
        assert re.search(rf"id: '{chapter}', order:", source), chapter
    for s in v6()["shots"]:
        if s.get("v6"):
            f = s["fallback"]
            assert f.get("plan") == {"label": "v6:before"} and "v6" not in f, s["id"]
            for o in f["overlays"]:
                assert o["type"] != "card" or o["card"] in v6()["cards"], s["id"]


def fell(gaps):
    got = node(f"S.applyFallbacks(R6.shots, {json.dumps(gaps)})")
    return got, {str(s["id"]): s["duration_s"] for s in got}


def test_v6_fallbacks_do_what_the_script_says_and_the_film_keeps_its_length():
    every = [s["v6"]["chapter"] for s in v6()["shots"] if s.get("v6")]
    got, _ = fell([])
    assert not any(s.get("fell_back") for s in got)
    got, length = fell(every)
    assert sum(length.values()) == v6()["total_s"] and all(s.get("fell_back") for s in got if str(s["id"]) in "1 3 4 5 6 7 8 9 10".split())
    assert not any(s.get("v6") for s in got)
    assert fell(["voice"])[1]["4"] == 6 and fell(["voice"])[1]["5"] == 22  # cut order 6: shot 4 drops to 6 s, its 4 s go to shot 5
    six = next(s for s in fell(["reading"])[0] if s["id"] == 6)
    assert six.get("fell_back") and fell(["reading"])[1]["6"] == 16 and [o["card"] for o in six["overlays"]] == ["mask", "reading"]  # no reading, no confirm beat: the card, never "blocked"
    assert "blocked" not in json.dumps(six).lower()
    for gone in ("voice", "reading", "replan", "breakit"):
        assert next(s for s in fell([gone])[0] if s["id"] == 1).get("fell_back"), gone  # the cold open needs every chapter it cuts to
    assert fell(["package"])[1]["10"] == 9 and fell(["package"])[1]["8"] == 18  # the receipt's time goes to shot 8
    rows = next(s for s in fell(["intake"])[0] if s["id"] == 3)
    assert rows["overlays"][0]["title"] == "Sample load list"  # the script's label for the base map's list
    refusal = next(s for s in fell(["copilot"])[0] if s["id"] == 9)
    assert "says no" in v6()["cards"][refusal["overlays"][0]["card"]]["title"]
    assert "with real tools" not in json.dumps(refusal)


def test_chapter_time_is_fitted_to_the_shot_and_the_cold_open_cuts_between_chapters():
    one, three, six, seven, nine = (json.dumps(v6_shot(n)) for n in (1, 3, 6, 7, 9))
    assert node(f"S.chapterAt({seven}, 7, 28)") == {"chapter": "replan", "tau": 14}  # a chapter longer than its shot plays faster, and ends with it
    assert node(f"S.chapterAt({seven}, 99, 15)")["tau"] == 15
    # Shot 1, the cold open (film-judge edit 1): the radio line at its own pace, the rule card, the replan at 3x, its end frame, the drag, the dare
    assert node(f"S.chapterAt({one}, 1.0, 11)") == {"chapter": "voice", "tau": pytest.approx(1.0)}
    assert node(f"S.chapterAt({one}, 2.5, 11)") == {"chapter": "reading", "tau": pytest.approx(6.7)}
    replan = next(h for h in v6_shot(1)["v6"]["holes"] if h["chapter"] == "replan" and h["rate"] > 0)
    assert replan["rate"] == 3 and replan["speed"] == "cold_replan_speed"  # the caption's "sped up N×" is this rate, put by the director
    assert "m[h.speed] = { value: String(h.rate)" in (STATIC / "stage-director.js").read_text()
    assert node(f"S.chapterAt({one}, 4.2, 11)") == {"chapter": "replan", "tau": pytest.approx(replan["start"] + 3 * (4.2 - replan["from"]))}
    assert node(f"S.chapterAt({one}, 7.0, 11)") == {"chapter": "replan", "tau": pytest.approx(15)}  # the end frame holds: every rule ticked
    assert node(f"S.chapterAt({one}, 8.5, 11)")["chapter"] == "breakit" and node(f"S.chapterAt({one}, 8.5, 11)")["tau"] < 0  # the hook's grab
    # Shot 3: the hook's three slams first, then mess in fitted to the rest
    assert node(f"S.chapterAt({three}, 1.0, 16)") == {"chapter": "hook", "tau": pytest.approx(0.8 + 1.2)}
    assert node(f"S.chapterAt({three}, 2.6, 16)") == {"chapter": "intake", "tau": pytest.approx(0)}
    assert node(f"S.chapterAt({three}, 10.999, 16)")["tau"] == pytest.approx(16, abs=1e-2)
    # Shot 6: the card covers the reading chapter's early beats; the confirm plays at its own pace and ends with the shot
    reading = (STATIC / "stage-v6reading.js").read_text()
    confirm, press, end = (float(x) for x in re.search(r"confirm: ([\d.]+), press: ([\d.]+), end: (\d+)", reading).groups())
    card = next(o for o in v6_shot(6)["overlays"] if o["type"] == "pair")
    assert node(f"S.chapterAt({six}, {press}, 16)") == {"chapter": "reading", "tau": pytest.approx(press)} and card["to"] < press
    assert node(f"S.chapterAt({six}, 15.999, 16)")["tau"] == pytest.approx(end, abs=1e-2)
    assert v6_shot(5)["v6"]["to"] <= confirm  # shot 5 stops before the confirm, which shot 6 plays after the card
    # Shot 9 (film-judge edit 3): opens on the hero's stored refusal, after the cut small-section beat
    copilot = (STATIC / "stage-v6copilot.js").read_text()
    proved_on = float(re.search(r"PROVED = \{ on: ([\d.]+)", copilot)[1])
    assert node(f"S.chapterAt({nine}, 0, 21)")["tau"] < proved_on and node(f"S.chapterAt({nine}, 16, 21)")["tau"] == pytest.approx(21)
    assert "M.heroFirst = !ctx.interactive && c === d.main" in copilot  # the film hides the small section's panels


def test_v6_hook_fallback_follows_what_is_really_in_the_film():
    o = v6_shot(1)["fallback"]["overlays"][0]
    on = {"available": True}
    both = node(f"S.hookHits({json.dumps(o)}, {json.dumps({'intake': on, 'voice': on, 'sandbox': on})})")
    assert both == {"words": ["TALLY SHEET", "EMAIL", "RADIO"], "dare": "TRY TO BREAK IT"}
    none = node(f"S.hookHits({json.dumps(o)}, {json.dumps({'voice': on})})")
    assert none == {"words": ["TYPED NOTE", "SPREADSHEET", "RADIO"], "dare": "THEN TRY TO ARGUE WITH IT"}
    assert "RADIO" not in node(f"S.hookHits({json.dumps(o)}, {json.dumps({'intake': on})})")["words"]  # cut order 6


def test_if_blocks_keep_only_set_names():
    text = "A · [[IF thai-note: {{sealed_envelope_pass}} of 9 · ]]B"
    assert node(f"S.ifBlocks({json.dumps(text)}, R6, {{}})") == "A · B"
    assert node(f"S.ifBlocks({json.dumps(text)}, R6, {{'thai-note': true}})") == "A · {{sealed_envelope_pass}} of 9 · B"
    assert node(f"S.cell({json.dumps(text)}, R6, {{'thai-note': true, sealed_envelope_pass: 7}})")["text"] == "A · 7 of 9 · B"


GATE_NAMES = {"hero_rules_active", "hero_rules_off", "hero_recheck", "xray_source_label", "reader_label", "breakit_fast_s", "breakit_full_s",
              "hero_heel_limit", "replay_drags", "copilot_lift_gap", "copilot_scripted_replies", "xray_blocked_tokens", "xray_tokens", "hero_sentence",
              "cold_replan_speed"}  # cold_replan_speed: shot 1's own playback rate of the replan, so "sped up N×" is the rate it plays at  # the director's own, from the flow at boot; never fill-v6.json


def test_v6_rule_count_comes_from_data_and_no_spark_verb_overclaims():
    director = (STATIC / "stage-director.js").read_text()
    assert "put('hero_rules_active', (d.rule_counts || {}).active)" in director
    assert "Muse Spark says" not in director.replace("Muse Spark says which rule broke", "") and "says why" not in director
    seven = json.dumps(v6_shot(7))
    assert "{{hero_rules_active}} active rules" in seven and "62" not in re.sub(r"\d+\.\d+", "", seven)
    stage = (STATIC / "stage.js").read_text()
    assert "explains the plan" not in stage and "selects and orders checked statements" in stage
    for name in ("stage-v6replan.js", "stage-director.js"):
        assert not re.search(r"\b62 rules|sixty-two", (STATIC / name).read_text())


def test_v6_names_all_come_from_the_script_and_fill_is_in_sync():
    sync = _sync_fill()
    text = json.dumps(v6(), ensure_ascii=False)
    keys = sync.v6_keys()
    used = {name for name in re.findall(r"\{\{([a-z_]+)\}\}", text)} | set(re.findall(r"\[\[IF ([a-z-]+):", text))
    assert used - GATE_NAMES - set(film_figures()) <= keys, used - GATE_NAMES - set(film_figures()) - keys
    values = json.loads((VIDEO / "fill-v6.json").read_text())
    assert set(values) <= keys
    page = (STATIC / "stage-fillv6.js").read_text()
    assert json.loads(page[page.index("{"): page.rindex("}") + 1]) == values and "window.STAGE_FILL_V6" in page


def test_sync_fill_v6_fails_closed(tmp_path):
    sync = _sync_fill()
    out, fill = tmp_path / "v6.js", tmp_path / "fill.json"
    args = ["--fill", str(tmp_path / "a"), "--out", str(tmp_path / "o"), "--fill-v5", str(tmp_path / "b"), "--out-v5", str(tmp_path / "o5"),
            "--fill-v6", str(fill), "--out-v6", str(out)]
    fill.write_text(json.dumps({"sealed_envelope_pass": 190, "repo_url": "https://example.org/x", "thai-note": True}))
    assert sync.main(args) == 0 and '"sealed_envelope_pass": 190' in out.read_text()
    before = out.read_text()
    for bad in ({"made_up": 1}, {"sealed_envelope_pass": "lots"}, {"thai-note": "yes"}, {"repo_url": "{{repo_url}}"}, {"sealed_envelope_pass": ""}):
        fill.write_text(json.dumps(bad))
        assert sync.main(args) == 1, bad
        assert out.read_text() == before


def test_v6_cards_drop_in_final_and_chip_in_draft():
    js = (STATIC / "stage-director.js").read_text()
    assert "const pendShown = !V6 || DRAFT;" in js
    assert "measured" not in v6()["cards"]  # shot 8 "What we measured" is cut from the film (D267)
    sealed = node("S.barCard(R6.cards.sealed, R6, {})")
    assert sealed["title"] is None and sealed["rows"][0]["value"] is None
    assert "the engine isn't competitive at large scale yet" in sealed["notes"]  # the limits stay on the card, filled or not
    assert not any("IF lifts-claim" in n or "{{" in n for n in sealed["notes"])
    full = node("S.barCard(R6.cards.sealed, R6, {sealed_envelope_pass: 20, sealed_envelope_maps: 24})")
    assert full["rows"][0]["width_pct"] == 100 * 20 / 24 and full["title"].startswith("20 of 24 held-out test voyages")
    assert "if (!c.complete && !pendShown) return null;" in js  # no sourced scope: no number on the map


def test_v6_held_out_card_carries_the_d284_label_and_waits_for_its_numbers():
    """logic7 L1 / D286: plan results are held-out test voyages, never a sealed or locked test run once; shot 11's numbers come from p113's fill."""
    c, eleven = v6()["cards"]["sealed"], v6_shot(11)
    assert eleven["title"] == "Held-out test" and "LOCKED" not in c["eyebrow"] and "RUN ONCE" not in c["eyebrow"]
    assert c["pending"] == "3.1" and c["fill_from"].startswith("experiments/mvp/video/fill-v6.json") and "final-3/story-figures.json" in c["fill_from"]  # where p113's numbers come from
    empty = node("S.barCard(R6.cards.sealed, R6, {})")
    for words in ("frozen pipeline", "the third attempt: two earlier runs stopped on our own file guard and are published", "not the pre-registered sealed claim"):
        assert words in empty["notes"], words  # the D284 label stays on the card, filled or not
    assert empty["title"] is None and empty["partial"] is None  # no figures: the segment drops, never a zero
    partial = node("S.barCard(R6.cards.sealed, R6, {overlap1_same: 7, overlap1_rows: 9, overlap1_verdict: 8, overlap2_same: 5, overlap2_rows: 5})")["partial"]
    assert partial == "The first attempt matched the final run on 7 of the 9 rows it opened (status and lifts; 8 of 9 on verdict alone); the second on 5 of 5"
    assert node("S.barCard(R6.cards.sealed, R6, {overlap1_same: 7, overlap1_rows: 9})")["partial"] is None  # whole or absent
    js = (STATIC / "stage-director.js").read_text()
    assert "c.partial ? `<div class=\"dbc-partial\">${esc(c.partial)}</div>` : ''" in js


def test_v6_batch_confirmation_is_disclosed_only_over_a_batch_package():
    from app import v6_package
    ten = v6_shot(10)["captions"]
    batch = next(c for c in ten if "Confirmed by:" in c["text"])
    assert batch["when"] == "package_batch_confirmed" and batch["otherwise"] == "the bay plan and the note to the master print the confirmed instruction"
    assert batch["text"].startswith("Confirmed by: {{package_confirmed_by}}; the planner's tap earlier is the demo's confirmation")
    said = film_figures()["package_confirmed_by"]  # the package's own words, from its receipt
    assert said["value"] == v6_package.BATCH_LABEL and said["source"].endswith("receipt.json")
    js, story = (STATIC / "stage-director.js").read_text(), (STATIC / "stage-story.js").read_text()
    assert "'package_batch_confirmed'" in story
    assert f"CFG.runtime.package_batch_confirmed = ((pack.receipt || {{}}).reader || {{}}).confirmed_by === '{v6_package.BATCH_LABEL}';" in js
    assert "planner-confirmed" not in json.dumps(v6()).lower()


def locked_test_outside_the_reader(blob: str) -> list[str]:
    """Every "locked test" must be the reader's: its sentences, or the x-ray's "not the locked test" about the reader's validation sentences."""
    return [blob[max(0, m.start() - 40):m.end() + 20] for m in re.finditer(r"locked test", blob, re.I)
            if not re.match(r" sentences", blob[m.end():]) and not re.search(r"validation sentences, not the $", blob[:m.start()])]


def test_locked_test_is_only_the_reader_s_and_plans_are_never_sealed_or_run_once():
    blobs = {"shots": json.dumps(v6(), ensure_ascii=False), "v6.py": (ROOT / "app/v6.py").read_text()}
    blobs.update({p.name: p.read_text() for p in STATIC.glob("stage-v6*.js")})
    for name, blob in blobs.items():
        assert not locked_test_outside_the_reader(blob), (name, locked_test_outside_the_reader(blob))
    shots = blobs["shots"].lower()
    for phrase in ("run once", "sealed test", "sealed envelope result", "locked test voyages", "sealed maps"):
        assert phrase not in shots, phrase
    assert locked_test_outside_the_reader("3 of 4 locked test voyages") and not locked_test_outside_the_reader("118 of 128 locked test sentences")


def test_v6_projection_stays_labelled_through_the_pull_back():
    c, twelve = v6()["cards"]["forward"], v6_shot(12)
    assert c["eyebrow"] == "Projection" and c["fine"][0].startswith("inputs: ") and c["fine"][1].startswith("what would have to be true: ")
    assert "none of it has been measured" in c["fine"][1] and c["fine"][2].startswith("check: world terminal revenue")
    pilot = next(o for o in twelve["overlays"] if o["type"] == "pilot")
    chip = next(o for o in twelve["overlays"] if o["type"] == "chip")
    end = next(o for o in twelve["overlays"] if o["type"] == "end")
    assert pilot["to"] - pilot["from"] >= 11 and chip["text"] == "Projection" and chip["from"] <= pilot["from"] and chip["to"] >= end["from"]  # labelled until the end card covers it
    assert ".dov-pilot {" in (STATIC / "stage-director.css").read_text() and "MAP_SVG_SMALL = mapSvg(0.4)" in (STATIC / "stage-director.js").read_text()  # the place name at 40 %
    served = {name: {"value": row["value"]} for name, row in film_figures().items()}
    assert not node("S.pilotCard(R6.cards.forward, R6, {})")["complete"]  # the projection card carries no figure of its own
    full = node(f"S.pilotCard(R6.cards.forward, Object.assign({{}}, R6, {{measured: {json.dumps(served)}}}), {{}})")
    assert full["complete"] and full["big"] == f"${served['impact_bill_billion']['value']} billion a year" and full["headline"] == f"≈ ${served['impact_mid_billion']['value']} billion a year"


def test_v6_lower_third_is_on_every_shot_and_stand_in_ships_say_so():
    assert v6()["lower_third"] == "Synthetic ships and cargo, simulated port"
    js = (STATIC / "stage-director.js").read_text()
    assert "$('dir-lower').textContent = CFG.lower_third" in js
    assert "if (s.show_tag && V6STATE && (V6STATE.stand_ins || {}).ship) rt.r.tag" in js
    for sid in (2, 7, 8, 9, 10, 12):
        assert v6_shot(sid).get("show_tag"), sid


def test_v6_shot_seven_labels_and_gauge_words_come_from_the_gate():
    caps = v6_shot(7)["captions"]
    assert "Stored plan, computed on {{change_plan_date}}, {{hero_recheck}}" == caps[0]["text"]  # the date is the facts file's, the recheck this launch's
    assert "stay green" not in json.dumps(v6_shot(7))
    walked = [c for c in caps if "crane frames walked" in c["text"]]  # only over a walk of every frame of the plan on screen
    assert {c["when"] for c in walked} == {"hero_walked"} and all("repaired" not in c["text"] and "{{change_sequence}}" in c["text"] for c in walked)  # "repaired" or "not repaired": the facts file's label
    assert "CFG.runtime.hero_walked = walked;" in (STATIC / "stage-director.js").read_text()
    js = (STATIC / "stage-director.js").read_text()
    assert "put('hero_gauges_line', (d.gauges || {}).line)" in js and "if (g.status === 'shown') put('hero_recheck', g.recheck);" in js
    assert "const f = g.status === 'shown' ? d.facts : null, walked = !!f && f.frames_passed && f.frames > 0;" in js  # walked and repaired: the facts file's
    assert "CFG.runtime.hero_walked_repaired = walked && f.repaired;" in js and "put('hero_heel_limit', (d.heel || {}).limit_deg);" in js
    for retired in ("hero_plan_date", "hero_peak_heel", "hero_boxes", "hero_forced_boxes", "hero_rearranged_boxes", "hero_extra_lifts", "hero_lifts_before", "hero_frames", "hero_engine"):
        assert f"'{retired}'" not in js and "{{" + retired + "}}" not in json.dumps(v6()), retired  # the old hero's figures are gone from the film


def test_measured_values_beat_fill_and_say_where_they_came_from():
    got = node("S.value('change_boxes', {measured: {change_boxes: {value: 190, source: 'gate'}}, fill: 'v6'}, {change_boxes: 1})")
    assert got == {"value": "190", "source": "gate"}
    assert node("S.value('demo_url', {fill: 'v6'}, {demo_url: 'u'})")["source"].endswith("fill-v6.json")


def test_the_film_never_says_approved_over_a_plan():
    verdict = next(o for o in v6_shot(7)["fallback"]["overlays"] if o["type"] == "verdict")
    assert verdict["stamp"] == "✓ FULL CHECKER PASSED"
    assert "o.stamp || '✓ CHECKER PASS · DEPARTURE APPROVED'" in (STATIC / "stage-director.js").read_text()


def test_the_v5_rehearsal_never_starts_the_flow():
    js = (STATIC / "stage-director.js").read_text()
    assert "const V6 = PICK.variant === 'v6';" in js and "if (V6 && window.StageV6)" in js


V6_BANNED = (*("negotiates", "argues", "crane moves", "wrong bays", "beats Spark", "learned sight", "learned physics", "beyond simple rules", "industry", "StowLM planned", "real-time", "instant",
               "certified", "class-approved", "production", "solves real ships", "no plan exists", "stays on the laptop"),)


def test_banned_wording_is_not_in_any_v6_string_or_page():
    text = {"shots": json.dumps(v6(), ensure_ascii=False).lower()}
    for path in [*STATIC.glob("stage-v6*.js"), *STATIC.glob("stage-v6*.css")]:
        text[path.name] = re.sub(r"\bapp/tests\S*", "", path.read_text().lower())
    for name, blob in text.items():
        for phrase in V6_BANNED:
            assert phrase.lower() not in blob, (name, phrase)
    assert "approved" not in text["shots"] and "private" not in text["shots"]


def test_the_measure_is_extra_lifts_never_crane_lifts():
    """The audit's class of miss: a plan's lift count called "crane lifts". A crane's own count of steps may keep the words."""
    blobs = [json.dumps(v6(), ensure_ascii=False), (STATIC / "stage-story.js").read_text(), (STATIC / "stage-v6replan.js").read_text()]
    for blob in blobs:
        assert not re.search(r"(more|fewer|same) crane lifts|crane lifts,? including|\d crane lifts", blob, re.I)


def test_v6_a_caption_says_only_what_the_evidence_supports():
    five, js = v6_shot(5)["captions"], (STATIC / "stage-director.js").read_text()
    assert five[0]["otherwise"] == "{{reader_label}} · {{reader_adapter_name}} · each read once" and "put('reader_label', reading.label || (reading.reader || {}).label)" in js  # no reading, no "Recorded answer"
    assert all(c["when"] == "reader_figures" for c in five if "{{reader_exact}}" in c["text"] or "Muse Spark" in c["text"])  # the reader's figures only beside its own reading
    assert "never guessed" not in json.dumps(v6())  # D267: measured false
    assert "`Token “<code>${esc(d.first_block.token)}</code>” blocked: " in (STATIC / "stage-v6xray.js").read_text()  # shot 6's heading names a token, never read as a count
    assert "Muse Spark" not in json.dumps(v6_shot(10))  # draft 4: the hero's note is no longer credited to Spark
    nine = v6_shot(9)["captions"]
    assert all(c.get("when") == "copilot_delivered" for c in nine if "real tools" in c["text"] or "lift gap" in c["text"])  # only over the delivered take
    assert "CFG.runtime.copilot_delivered = !!take;" in js
    assert "No model reading" not in js


def test_v6_mask_card_says_it_is_not_a_recording():
    assert "Explanation card, not a recording" in v6()["cards"]["mask"]["lines"]


def test_a_stored_plan_says_stored_and_a_live_label_needs_a_live_run():
    js = (STATIC / "stage-v6replan.js").read_text()
    assert "Stored plan, computed on ${date}${tail}" in js and "rechecked now" not in js and "f ? f.plan_date : null" in js  # the facts file's date
    assert "run.live ? `Live, ${run.live.elapsed_s} s`" in js
    assert 'f"Frontier Engine {known[-1]} · search software"' in (ROOT / "app/v6.py").read_text()  # the credit comes from each plan's own evidence


# ------------------------------------------------------------------ v5 rehearsal: the shot list against script.md


def test_durations_titles_and_sum_follow_the_script():
    c, table = cfg(), script_rows()
    assert c["script"] == "experiments/mvp/video/script.md"
    assert [str(s["id"]) for s in c["shots"]] == ["1", "2", "3a", "3b", "3c", "4", "5", "6", "7", "8", "9"] == list(table)
    assert {str(s["id"]): s["duration_s"] for s in c["shots"]} == {k: v["duration"] for k, v in table.items()}
    assert {str(s["id"]): s["title"] for s in c["shots"]} == {k: v["title"] for k, v in table.items()}
    target = re.search(r"shot durations sum to (\d+) s", SCRIPT.read_text())
    assert sum(s["duration_s"] for s in c["shots"]) == int(target[1]) == c["total_s"] == 174


def test_shot_five_has_three_beats_that_fill_it():
    five = shot(5)
    beats = five["beats"]
    assert [b["id"] for b in beats] == ["5a", "5b", "5c"]
    assert beats[0]["from"] == 0 and beats[0]["to"] == beats[1]["from"] and beats[1]["to"] == beats[2]["from"] and beats[2]["to"] == five["duration_s"]
    assert node("[S.findShot(R.shots, '5a'), S.findShot(R.shots, '5c').index, S.findShot(R.shots, '3a'), S.findShot(R.shots, '10').index]") == [
        {"index": 6, "beat": beats[0]}, 6, {"index": 2, "beat": None}, -1]
    assert [b["stage"] for b in five["brief"]] == ["refused", "refused", "impossible"]


def test_timelines_inside_each_shot():
    for s in cfg()["shots"]:
        dur = s["duration_s"]
        for key in ("captions", "overlays", "focus"):
            for it in s.get(key, []):
                assert 0 <= it["from"] < it["to"] <= dur, (s["id"], it)
        for t, pos in s.get("play", []):
            assert 0 <= t <= dur and (isinstance(pos, (int, float)) or pos == "end" or re.fullmatch(r"xray([+-]\d+)?", pos))
        for k in s.get("camera", []):
            assert 0 <= k["t"] <= dur and k["zoom"] >= 1
        last = 0
        for b in s.get("brief", []):
            assert b["from"] == last and b["to"] <= dur, s["id"]
            last = b["to"]


def test_captions_are_verbatim_from_the_script():
    table = script_rows()
    for s in cfg()["shots"]:
        for cap in s.get("captions", []):
            if cap.get("extra"):
                continue
            for seg in cap["text"].split(" · "):
                assert seg in table[str(s["id"])]["caption"].replace("Speech to text", "speech to text"), (s["id"], seg)


def test_every_script_caption_segment_reaches_its_shot():
    table = script_rows()
    for s in cfg()["shots"]:
        blob = json.dumps(s, ensure_ascii=False).replace('\\"', '"')
        for seg in table[str(s["id"])]["caption"].split(" · "):
            assert seg in blob, (s["id"], seg)


def test_extra_captions_are_labels_without_numbers():
    for s in cfg()["shots"]:
        for cap in s.get("captions", []):
            if cap.get("extra"):
                assert not re.search(r"\d", cap["text"]), cap


def test_reading_is_held_long_enough_to_read():
    track = {b["stage"]: b for b in shot("3a")["brief"]}
    assert list(track) == ["listening", "transcript", "reading"]
    confirm = next(o for o in shot("3a")["overlays"] if o["type"] == "confirm")
    assert confirm["from"] - track["reading"]["from"] >= 5  # the rule is on screen before the planner confirms it
    three = shot("3b")
    end = next(t for t, pos in three["play"] if pos == "end")
    assert end <= three["rules_reveal"]["from"]  # rule ticks come after the plan completes


def test_limits_stay_on_the_lifts_card_for_the_whole_shot():
    eight, card = shot(8), cfg()["cards"]["lifts"]
    assert card["limits"] == ["12–22 boxes", "2 ports", "2 hatch covers", "most of the saving is projected later-port lifts"]
    lifts = next(o for o in eight["overlays"] if o["type"] == "lifts")
    assert lifts["from"] < 1 and lifts["to"] > eight["duration_s"] - 1
    assert "Search software saves these, not the fine-tune." == card["who"]
    assert "a simple two-step planner, modelled from one field expert's description" in card["two_step"]


# ------------------------------------------------------------------ numbers
def test_placeholders_are_the_scripts_and_nothing_is_typed_in():
    text = re.sub(r"\[\[IF [a-z-]+: .*?\]\]", "", SCRIPT.read_text())
    script = set(PLACEHOLDER.findall(text)) - {"…"}  # the script's header names the pattern
    config = shots_config()
    used = set(PLACEHOLDER.findall(json.dumps(config, ensure_ascii=False)))
    for card in config["cards"].values():  # cards name their keys
        used |= {row["key"] for row in card.get("rows", [])}
    assert used <= script, used - script
    # the voice-over's own numbers: spoken from the same fill at 4.2, not on screen, or on the miss branch only
    assert script - used == {"hero extra lifts", "loosening requests refused", "real planner sentences"}, script - used
    assert "measured" not in config


def test_no_provisional_or_development_number_is_in_the_shot_list():
    v5 = {k: v for k, v in shots_config().items() if k != "variants"}  # v6's card carries the script's own finished findings
    raw = json.dumps(v5, ensure_ascii=False)
    for value in ("94.53", "83.33", "14.28", "23.40", "13.59", "24.19", "45/55", "48/55", "23/55", "37.5", "19.47",
                  "60,120", "5.39", "$578", "$110", "$2,248", "2,224", "39 %", "44 %", "81 %", "64.5"):
        assert value not in raw, value


def test_fixed_facts_only_in_captions():
    """Every digit left in a caption, once placeholders are removed, is one of STORY-NEEDS's safe fixed facts or the script's words."""
    safe = ("9,325", "2024–25", "$15,700", "Q4 2025", "12–22", "12-weight", "20-foot", "30B", "500-box", "each 10 %", "bay 1", "bay 2",
            "class 8", "Class 8", "4 high")
    for s in cfg()["shots"]:
        for cap in s.get("captions", []):
            bare = ALL_PLACEHOLDERS.sub("", cap["text"])
            for item in safe:
                bare = bare.replace(item, "")
            assert not re.search(r"\d", bare), (s["id"], bare)
    needs = (VIDEO / "STORY-NEEDS.md").read_text()
    assert "9,325 calls (BSAA 2024–25, p. 6)" in needs and "$15,700 a day (Contships)" in needs


def test_fill_v5_is_keyed_by_the_scripts_placeholders_and_the_page_is_in_sync():
    sync = _sync_fill()
    values = json.loads((VIDEO / "fill-v5.json").read_text())
    assert set(values) <= sync.v5_keys()
    text = (STATIC / "stage-fillv5.js").read_text()
    assert json.loads(text[text.index("{"): text.rindex("}") + 1]) == values


def test_values_reach_cells_and_missing_ones_stay_missing():
    got = node("[S.cell('{{NUMBER: test maps}} maps', R, {'test maps': 30}), S.cell('{{NUMBER: test maps}} maps', R, {}), S.value('REPO_URL', R, {REPO_URL: 'u'}), S.value('NOPE', R, {})]")
    assert got[0] == {"text": "30 maps", "missing": []}
    assert got[1] == {"text": "{{NUMBER: test maps}} maps", "missing": ["test maps"]}
    assert got[2]["value"] == "u" and got[2]["source"].endswith("fill-v5.json") and got[3] is None
    js = (STATIC / "stage-director.js").read_text()
    assert "STORY.value(name, CFG, FILL)" in js and "if (missing && !DRAFT) continue;" in js


def test_director_drops_a_caption_segment_in_final_and_chips_it_in_draft():
    js = (STATIC / "stage-director.js").read_text()
    start = js.index("  function getPath(o, path)")
    fn = js[start:js.index("  // ------------------------------------------------------------------ catalogue")]
    head = """const STORY = require(%s); const num = (v) => typeof v === 'number' && isFinite(v); const fmtInt = String;
const esc = (s) => String(s); const CFG = {}; const FILL = {};""" % json.dumps(str(STATIC / "stage-story.js"))
    text = "A {{NUMBER: hero replan minutes}} min · plain words"
    out = {}
    for draft in ("true", "false"):
        code = f"{head}\nconst DRAFT = {draft};\n{fn}\nconsole.log(JSON.stringify(fill({json.dumps(text)}, null)));"
        out[draft] = json.loads(subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True).stdout)
    assert "PENDING: hero replan minutes" in out["true"]["html"] and "plain words" in out["true"]["html"]
    assert out["false"]["html"] == "plain words"


def _sync_fill():
    spec = importlib.util.spec_from_file_location("sync_fill", ROOT / "experiments/mvp/ui/sync_fill.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_sync_fill_writes_empty_without_files_and_fails_closed(tmp_path):
    sync = _sync_fill()
    out, out5 = tmp_path / "stage-fill.js", tmp_path / "stage-fillv5.js"
    args = lambda fill, fill5: ["--fill", str(fill), "--out", str(out), "--fill-v5", str(fill5), "--out-v5", str(out5),  # noqa: E731
                                "--fill-v6", str(tmp_path / "absent6.json"), "--out-v6", str(tmp_path / "stage-fillv6.js")]
    assert sync.main(args(tmp_path / "absent.json", tmp_path / "absent5.json")) == 0
    assert "window.STAGE_FILL = {};" in out.read_text() and "window.STAGE_FILL_V5 = {};" in out5.read_text()
    fill, fill5 = tmp_path / "fill.json", tmp_path / "fill5.json"
    fill.write_text(json.dumps({"SMALL_PASS": 9, "VAL_MOVES": "30.43", "SMALL_SPLIT": "3 / 7 / 2"}))
    fill5.write_text(json.dumps({"test maps": 30, "reader accuracy %": "97.5", "lifts vs random %": "+0.8", "REPO_URL": "https://example.org/x"}))
    assert sync.main(args(fill, fill5)) == 0
    assert '"SMALL_PASS": 9' in out.read_text() and '"test maps": 30' in out5.read_text()
    before, before5 = out.read_text(), out5.read_text()
    for bad in ({"INVENTED_KEY": 1}, {"SMALL_MOVES": "25 %"}, {"SMALL_PASS": "{{SMALL_PASS}}"}, {"SMALL_PASS": [9]}):
        fill.write_text(json.dumps(bad))
        assert sync.main(args(fill, fill5)) == 1, bad
        assert out.read_text() == before
    fill.write_text("{}")
    for bad in ({"invented key": 1}, {"test maps": "thirty"}, {"reader accuracy %": "{{NUMBER: reader accuracy %}}"}, {"test maps": ""}, {"test maps": [30]}, {"test maps": "PENDING"}):
        fill5.write_text(json.dumps(bad))
        assert sync.main(args(fill, fill5)) == 1, bad
        assert out5.read_text() == before5 and out.read_text() == before
    from app.server import STAGE_ASSET
    assert STAGE_ASSET.fullmatch("stage-fillv5.js") and STAGE_ASSET.fullmatch("stage-story.js")


# ------------------------------------------------------------------ evidence cards
def bars(card: str, values: dict) -> dict:
    return node(f"S.barCard(R.cards.{card}, R, {json.dumps(values)})")


def test_a_bar_card_with_no_values_is_pending_never_a_blank_bar_or_zero():
    c = bars("readers", {})
    assert not c["complete"] and c["title"] is None and c["pending"] == "1.1"
    for row in c["rows"]:
        assert row["pending"] == "1.1" and row["value"] is None and row["width_pct"] is None and row["text"] is None
    assert bars("chance", {})["rows"][0]["pending"] == "test v3"


def test_bars_are_to_true_scale_from_the_values_only():
    c = bars("readers", {"reader accuracy %": "97.5", "untrained Glimmer accuracy %": "40", "Spark accuracy %": "95", "sealed sentences": 128})
    assert [r["width_pct"] for r in c["rows"]] == [40, 97.5, 95]
    assert [r["text"] for r in c["rows"]] == ["40 %", "97.5 %", "95 %"] and c["complete"]
    assert c["title"].startswith("128 held-back sentences")


def test_a_partly_filled_card_keeps_the_gap_pending_and_rejects_values_off_the_scale():
    c = bars("readers", {"reader accuracy %": "97.5"})
    assert c["rows"][1]["width_pct"] == 97.5 and c["rows"][0]["pending"] == "1.1" and not c["complete"]
    assert bars("readers", {"reader accuracy %": "120"})["rows"][1]["value"] is None
    assert bars("readers", {"reader accuracy %": "n/a"})["rows"][1]["value"] is None
    assert bars("readers", {"reader accuracy %": 0})["rows"][1]["width_pct"] == 0  # a measured zero is a value


def test_lifts_card_needs_both_means_and_shares_one_scale():
    none = node("S.liftsCard(R.cards.lifts, R, {})")
    assert none["headline"] is None and all(r["pending"] == "test v3" and r["width_pct"] is None for r in none["rows"])
    one = node("S.liftsCard(R.cards.lifts, R, {'pipeline mean lifts': 14})")
    assert all(r["width_pct"] is None for r in one["rows"])
    full = node("S.liftsCard(R.cards.lifts, R, {'pipeline mean lifts': 10, 'two-step mean lifts': 20, 'lifts saved vs two-step %': 50})")
    assert [r["width_pct"] for r in full["rows"]] == [50, 100] and full["headline"].startswith("50 % fewer") and full["complete"]


def test_live_and_worth_cards_are_pending_until_filled():
    live = node("S.liveCard(R.cards.live, R, {'reader adapter MB': 64})")
    assert [r["text"] for r in live["rows"]] == [None, None, "64 MB"] and live["rows"][0]["pending"] == "4.3 live take" and not live["complete"]
    worth = node("S.worthCard(R.cards.worth, R, {'$ per call per 10 %, mid': 500})")
    assert worth["pending"] == "1.5" and not worth["complete"] and worth["headline"] is None
    full = node("S.worthCard(R.cards.worth, R, {'$ per call per 10 %, mid': 500, '$ per call per 10 %, low': 100, '$ per call per 10 %, high': 900})")
    assert full["complete"] and "$500" in full["headline"] and full["range"] == "low $100 · high $900"


def test_reader_bars_are_one_model_and_spark_is_a_reference_line():
    c = bars("readers", {"reader accuracy %": 90, "untrained Glimmer accuracy %": 40, "Spark accuracy %": 95})
    assert [r["reference"] for r in c["rows"]] == [False, False, True]
    assert "without our fine-tune" in c["rows"][0]["label"] and "with our fine-tune" in c["rows"][1]["label"]
    assert c["rows"][1]["own"] and c["rows"][0]["at"] < c["rows"][1]["at"] < c["rows"][2]["at"]
    assert "rules" not in json.dumps(cfg()["cards"]["readers"]).lower()  # the 10 % rule-matching baseline is withdrawn (D224)


SLOTS = [{"id": "0010102", "bay": 1, "row": 1, "tier": 2}, {"id": "0010104", "bay": 1, "row": 1, "tier": 4}, {"id": "0030102", "bay": 3, "row": 1, "tier": 2}]


def plan(*moves):
    return {"geometry": {"slots": SLOTS}, "initial": {"ship": []}, "steps": [{"box": b, "src": "B1", "dst": d} for b, d in moves]}


def test_what_moved_comes_from_the_two_plans_steps():
    before = plan(("A", "0010102"), ("B", "0010104"))
    after = plan(("A", "0010102"), ("B", "0030102"))
    d = node(f"S.bayDiff({json.dumps(before)}, {json.dumps(after)})")
    assert d["ok"] and d["moved"] == ["B"] and [b["bay"] for b in d["bays"]] == [1, 3]
    bay1 = {c["id"]: c for c in d["bays"][0]["cells"]}
    assert bay1["0010104"]["before"] == {"box": "B", "moved": True} and bay1["0010104"]["after"] is None
    assert bay1["0010102"]["after"] == {"box": "A", "moved": False}
    restowed = plan(("A", "0010102"), ("A", "0030102"))  # the box's last slot counts
    assert node(f"[...S.finalSlots({json.dumps(restowed)}).entries()]") == [["A", {"slot": "0030102", "cells": ["0030102"]}]]


def test_what_moved_fails_closed():
    assert not node("S.bayDiff(null, null)")["ok"]
    ghost = plan(("A", "0999902"))
    assert not node(f"S.bayDiff({json.dumps(plan(('A', '0010102')))}, {json.dumps(ghost)})")["ok"]
    js = (STATIC / "stage-director.js").read_text()
    assert "gate.boxes_moved !== d.moved.length" in js  # a count that disagrees with the gate reads pending


def test_cards_render_pending_in_final_mode_too():
    js = (STATIC / "stage-director.js").read_text()
    for name in ("barRow", "barsHtml", "liftsHtml", "worthHtml", "liveHtml"):
        body = js[js.index(f"function {name}("):]
        assert "DRAFT" not in body[:body.index("\n  }\n")], name
    assert "pendChip(r.pending)" in js and "pendCard(c.pending)" in js


# ------------------------------------------------------------------ plans, stand-ins, labels
@historical("experiments/mvp/runs/stowlm-instruction-binding/instances/random.small.29000201.0.7/result.json")  # the v5 film's stand-in; v6 shots name no run
def test_plan_selectors_use_catalogue_fields_and_shown_plans_exist():
    c = cfg()
    for sel in [c["hero_plan"], c["standin_plan"], *(s["plan"] for s in c["shots"] if "plan" in s)]:
        assert set(sel) <= set(SELECTOR_FIELDS) and any(sel.values())
        if sel.get("run") and sel.get("instance"):
            assert (RUNS / sel["run"] / "instances" / sel["instance"] / "result.json").is_file(), sel
    assert [s["id"] for s in c["shots"] if s.get("hero")] == [1, "3a", "3b", 4, 9]
    assert all(s.get("hero") or s.get("plan") for s in c["shots"])


def test_catalogue_entries_carry_the_selector_fields():
    from app.flow import DemoService, _run_name
    src = (ROOT / "app/flow.py").read_text()
    catalog_src = src[src.index("    def catalog(self):"):src.index("    def replay(self, plan_id):")]
    for field in SELECTOR_FIELDS.values():
        assert f'"{field}"' in catalog_src, field
    assert DemoService  # imported
    assert _run_name({"run_folder": str(RUNS / "base-mask/tiny")}) == "base-mask/tiny"
    assert _run_name({}) is None


GOLD = {"id": "plan-1", "planner": "solver.gold_warmstart", "run": "showcase-v2-gold", "instance_id": HERO}
BINDING = {"id": "plan-9", "planner": "stowlm", "run": "stowlm-instruction-binding", "instance_id": HERO}
HERO_PLAN = {"id": "plan-30", "planner": "frontier", "run": "hero-v5", "instance_id": "x"}
CASE = {"id": "dg_keep_away-04", "sentence": "Class 3 — sorry, class 8 — one bay from bay 1 at minimum.", "rule": "IMDG class 8 stays at least 1 bay from bay 01.", "gate": {"status": "shown", "after": {"plan_id": "plan-17", "weighted": 12.0}, "before": {"plan_id": "plan-16", "weighted": 12.0}}}


def resolve(sid, plans: list, brief=None):
    return node(f"S.resolveShot(R.shots.find((x) => String(x.id) === '{sid}'), R, {json.dumps(plans)}, 'active', {json.dumps(brief)})")


def test_hero_shots_show_the_hero_when_it_lands_and_a_labelled_stand_in_until_then():
    assert resolve(1, [GOLD, BINDING, HERO_PLAN], {"prepared": [CASE]})["state"] == "hero"
    hero = resolve(1, [HERO_PLAN], None)
    assert hero["id"] == "plan-30" and hero["tag"] == ""
    stand = resolve(1, [GOLD, BINDING], {"prepared": [CASE]})
    assert stand["state"] == "standin" and stand["id"] == "plan-17" and stand["tag"] == cfg()["standin_tag"] and "not the hero" in stand["tag"]
    assert resolve(4, [BINDING], {"prepared": [CASE]})["id"] == "plan-9"  # shot 4 needs the plan with a bound explanation


def test_stand_in_case_needs_the_fresh_gate():
    for gate in ({"status": "pending"}, {"status": "not_shown"}, {"status": "shown", "after": None}):
        r = resolve(1, [BINDING], {"prepared": [{**CASE, "gate": gate}]})
        assert r["id"] == "plan-9" and r["tag"] == cfg()["standin_tag"] and "brief" not in r
    missing = resolve(1, [], None)
    assert missing["missing"] and missing["id"] == "active"


def test_the_hero_sentence_never_sits_on_a_stand_in():
    c = cfg()
    assert node(f"S.sentenceFor({{state: 'hero'}}, {{}}, R)") == c["hero_sentence"]
    assert node(f"S.sentenceFor({{state: 'standin', brief: {json.dumps(CASE)}}}, {{}}, R)") == CASE["sentence"]
    assert node("S.sentenceFor({state: 'standin'}, {instruction: {presented: true, sentence: 'Recorded.'}}, R)") == "Recorded."
    assert node("S.sentenceFor({state: 'standin'}, {instruction: {presented: false, sentence: 'Recorded.'}}, R)") == ""


def test_hero_captions_show_only_over_the_hero():
    ok = "{meta: {checker_passed: true, departure_approved: true}}"
    hero_only = [c for sid in ("3a", "3b") for c in shot(sid)["captions"] if c.get("when") == "hero"]
    assert len(hero_only) == 5
    for cap in hero_only:
        assert node(f"S.captionText({json.dumps(cap)}, {{state: 'hero'}}, {ok}, R)") == cap["text"]
        assert node(f"S.captionText({json.dumps(cap)}, {{state: 'standin'}}, {ok}, R)") is None
        assert node(f"S.captionText({json.dumps(cap)}, {{state: 'hero'}}, {{meta: {{}}}}, R)") is None


def test_stand_in_label_is_set_in_every_mode():
    js = (STATIC / "stage-director.js").read_text()
    activate = js[js.index("  function activate(rt) {"):js.index("  // ----", js.index("  function activate(rt) {"))]
    assert "standinTag(rt.r, rt.B)" in activate and "$('dir-standin')" in activate
    assert "DRAFT &&" in activate[activate.index("dir-slot"):]  # only the slot chip is draft-only
    css = (STATIC / "stage-director.css").read_text()
    assert "#dir-standin {" in css


def test_step_labels_come_from_the_evidence_never_from_a_name_in_the_director():
    steps = [*shot("3a")["overlays"][0]["steps"], *shot("3b")["overlays"][0]["steps"]]
    r = {"state": "standin", "brief": {"reading": {"reader": {"label": "Reader X", "on_device": True}}}}
    B = {"meta": {"planner_label": "Plan label", "checker_passed": True, "departure_approved": True}}
    rows = node(f"S.stepModels({json.dumps(steps)}, {json.dumps(r)}, {json.dumps(B)}, null)")
    assert [x["model"] for x in rows] == [None, "Reader X", "The planner", "Plan label", "Full checker"] and rows[3]["where"] == "recorded"
    assert rows[1]["where"] == "on device"
    none = node(f"S.stepModels({json.dumps(steps)}, null, {{}}, null)")
    assert [x["model"] for x in none] == [None, None, "The planner", None, None]
    clip = node(f"S.stepModels({json.dumps(steps)}, null, {{}}, {{heard_by: 'Voice model'}})")
    assert clip[0]["model"] == "Voice model"
    spark = node(f"S.stepModels({json.dumps(steps)}, null, {{instruction: {{parse: {{model: 'muse-spark-1.3'}}}}}}, null)")
    assert spark[1] == {**steps[1], "model": "muse-spark-1.3", "where": "recorded reading"}


def test_a_plan_the_checker_did_not_approve_is_never_on_screen():
    js = (STATIC / "stage-director.js").read_text()
    assert "rt.blocked = true" in js and "rt.blocked || rt.gap ? '1'" in js
    assert node("S.approved({meta: {checker_passed: true, departure_approved: false}})") is False
    assert node("S.approved({meta: {checker_passed: true, departure_approved: true}})") is True


# ------------------------------------------------------------------ shot 3 and 5: the brief interface
def test_brief_track_is_a_pure_function_of_time():
    track = shot("3a")["brief"]
    got = node(f"[0, 6, 8, 15, 23.9].map((t) => S.briefAt({json.dumps(track)}, t).stage)")
    assert got == ["listening", "listening", "transcript", "reading", "reading"]
    assert node(f"S.briefAt({json.dumps(track)}, 99).stage") == "reading" and node("S.briefAt([], 1)") is None
    assert node(f"S.briefAt({json.dumps(shot('3b')['brief'])}, 5)") is None  # the ship has the frame


def test_director_codes_to_the_brief_interface_with_a_safe_slot():
    js = (STATIC / "stage-director.js").read_text()
    assert "stage: row.stage, recorded: true" in js and "if (api && api.show)" in js
    assert "api.show(null)" in js  # leaves the frame with the shot


# ------------------------------------------------------------------ shot 4: statements light their checker lines
SPARK_ENTRY = ROOT / "experiments/mvp/explain-cache/random.small.29000201.0.7__stowlm.json"


def fake_facts():
    return {"planned_moves_by_type": {"load": 22, "relocate": 15, "restow_off": 10, "restow_on": 10, "hatch_cover_off": 3, "hatch_cover_on": 3},
            "lifts_now": 63, "weighted": 67.0, "later_restow_lifts": 20, "later_hatch_cover_lifts": 6,
            "checker_margins": [{"metric": "max_racking_ratio", "margin": 0.34, "part": "lashing", "meaning": "transverse racking, 34% of the limit spare"}],
            "checks_ran": ["lashing", "moves", "stability"], "violations_by_check": {}, "violation_counts": {}, "margins_omitted": 17, "violations_omitted": 0}


PARTS = {"lashing": "lashing", "moves": "move replay", "stability": "stability"}


def test_links_follow_sparks_order_and_every_statement_has_a_real_line():
    from app.explanation_links import link_statements
    entry = json.loads(SPARK_ENTRY.read_text())
    links = link_statements(entry["text"], entry["statements"], fake_facts(), PARTS)
    ids = [s["id"] for s in links["statements"]]
    chosen = [i for group in [entry["selection"]["headline"], *entry["selection"]["points"]] for i in group]
    assert ids == [i for i in sorted(chosen, key=lambda i: entry["text"].index(next(s["text"] for s in entry["statements"] if s["id"] == i)))]
    keys = {line["key"] for line in links["lines"]}
    for s in links["statements"]:
        assert set(s["lines"]) <= keys, s["id"]
    by_id = {s["id"]: s for s in links["statements"]}
    assert by_id["total"]["lines"] == ["moves.total", "moves.weighted"] and by_id["tightest_limit"]["lines"] == ["limit.tightest"]
    assert by_id["clean_checks"]["lines"] == ["check.lashing", "check.moves", "check.stability"]
    line = {x["key"]: x["value"] for x in links["lines"]}
    assert line["moves.total"] == "63" and line["moves.restow"] == "20" and line["moves.hatch"] == "6" and line["violations"] == "0"


def test_a_template_gets_no_links_and_unmatched_text_gets_none():
    from app.explanation_links import explanation_links, link_statements
    assert explanation_links({}, {"source": "template", "text": "x"}) is None
    assert explanation_links({}, {"source": "spark-cached", "text": ""}) is None
    assert link_statements("nothing matches", [{"id": "total", "kind": "work", "text": "63 crane lifts"}], fake_facts(), PARTS) is None
    text = "chosen line\nOther checked statements: leftover line"
    got = link_statements(text, [{"id": "total", "kind": "work", "text": "chosen line"}, {"id": "loads", "kind": "work", "text": "leftover line"}], fake_facts(), PARTS)
    assert [s["id"] for s in got["statements"]] == ["total"]  # a statement Spark did not pick is not lit


def test_spark_panel_is_pending_over_a_template():
    assert not node("S.sparkLines({source: 'template', text: 'x'})")["bound"]
    assert not node("S.sparkLines({source: 'spark-cached', links: null})")["bound"]
    unlinked = {"source": "spark-cached", "links": {"statements": [{"id": "x", "text": "t", "lines": ["nope"]}], "lines": [{"key": "a"}]}}
    assert not node(f"S.sparkLines({json.dumps(unlinked)})")["bound"]
    good = {"source": "spark-cached", "links": {"statements": [{"id": "x", "text": "t", "lines": ["a"]}, {"id": "y", "text": "u", "lines": []}], "lines": [{"key": "a"}]}}
    bound = node(f"S.sparkLines({json.dumps(good)})")
    assert bound["bound"] and [s["id"] for s in bound["statements"]] == ["x"]  # a statement with no source line isn't shown as lit
    cap = next(c for c in shot(4)["captions"])
    assert cap["when"] == "spark_explained"
    assert node(f"S.captionText({json.dumps(cap)}, {{}}, {{explanation: {{source: 'template'}}}}, R)") is None


def test_statements_light_one_at_a_time():
    assert node("[0, 1, 2.9, 3, 9.9, 20].map((t) => S.litIndex(3, 1, 10, t))") == [-1, 0, 0, 0, 2, 2]
    assert node("S.litIndex(0, 1, 10, 5)") == -1


# ------------------------------------------------------------------ wording (docs/headline.md)
BANNED = ("beats Spark", "learned sight", "learned physics", "beyond simple rules", "industry level", "StowLM planned",
          "historical comparator", "instructions nobody wrote code for", "the job software can't do",
          "software alone can't read a brief")
RETIRED = ("our model of industry practice", "common two-step practice")  # bound explain-cache text may keep the first; it isn't in app source


def app_text_files():
    for folder, pattern in ((ROOT / "app", "*.py"), (STATIC, "*.js"), (STATIC, "*.html"), (STATIC, "*.css"), (ROOT / "app", "*.md"), (ROOT / "app", "*.json")):
        yield from folder.glob(pattern)


def test_banned_wording_is_not_in_the_app():
    for path in app_text_files():
        text = path.read_text().lower()
        if path.name == "stage-fill.js":  # the retired 30 Oct numbers; the page no longer loads this file
            continue
        for phrase in (*BANNED, *RETIRED):
            assert phrase.lower() not in text, (path.name, phrase)


def test_the_retired_fill_file_is_off_the_page():
    html = (STATIC / "stage.html").read_text()
    assert 'src="/stage-fillv5.js"' in html and 'src="/stage-fill.js"' not in html


def test_two_step_and_search_labels():
    from app.stage import PLANNER_LABELS
    long_form = "a simple two-step planner, modelled from one field expert's description"
    assert PLANNER_LABELS["baseline.twostep"].lower() == long_form
    assert cfg()["two_step_long"] == long_form
    assert "Search software" in PLANNER_LABELS["solver.gold_warmstart"] and "StowLM" not in PLANNER_LABELS["solver.gold_warmstart"]
    assert "StowLM" not in PLANNER_LABELS["solver.gold_warmstart.instruction"]
    for name in ("stage.js", "app.js"):
        assert "two-step baseline" in (STATIC / name).read_text().lower()
    assert "our emulation of published methods" in (STATIC / "app.js").read_text()
    for path in app_text_files():
        assert "industry standard" not in path.read_text().lower(), path.name


def test_kpi_tiles_keep_this_slice():
    html = (STATIC / "stage.html").read_text()
    assert html.count("this slice") >= 2 and "Moves vs two-step" in html


# ------------------------------------------------------------------ KPI + money off the v5 take
def test_kpi_and_money_tiles_are_off_every_shot():
    assert all(s["hide_kpis"] and s["hide_money"] for s in cfg()["shots"])
    css = (STATIC / "stage-director.css").read_text()
    assert "body.video.dir-no-kpis #kpis { visibility: hidden; }" in css
    assert "body.video.dir-no-money #kpi-usd, body.video.dir-no-money #kpi-thb { display: none; }" in css


# ------------------------------------------------------------------ the rest of the page
def test_hero_animation_run_is_never_a_plan():
    """HERO POLICY: the full-ship run is NOT APPROVED; no shot selects it."""
    assert "showcase-hero-animation" not in (STATIC / "stage-shots.js").read_text()


def test_x_clip_is_declared_and_portrait():
    spans = cfg()["clip_x"]["spans"]
    assert [x["shot"] for x in spans] == ["1", "3a", "3b", "5a"] and sum(x["to"] - x["from"] for x in spans) == 15  # x-post.md
    js = (STATIC / "stage-director.js").read_text()
    assert "const W = CLIP ? 1080 : 1920, H = CLIP ? 1920 : 1080" in js and "P.get('clip') === 'x'" in js
    assert "--clip" in (ROOT / "experiments/mvp/ui/dev/record_shots.py").read_text()


def test_generated_voice_is_a_config_flag():
    assert cfg()["generated_voice"] is False
    assert "CFG.generated_voice ? `<div>${esc(o.voice_line || 'Voice-over: generated voice')}</div>` : ''" in (STATIC / "stage-director.js").read_text()
    assert next(o for o in v6_shot(12)["overlays"] if o["type"] == "end")["voice_line"] == "Narration: generated voice"  # script-v6 shot 12's end card


def test_end_card_carries_the_scope_line():
    nine = shot(9)
    lines = " ".join(next(o for o in nine["overlays"] if o["type"] == "end")["lines"])
    assert "Not a class-approved loading computer." in lines and "{{REPO_URL}}" in lines and "Built 26 Oct – 4 Nov 2026" in lines


@pytest.mark.parametrize("name", ["stage-director.js", "stage-shots.js", "stage-story.js", "stage-fillv5.js", "stage-fillv6.js", "stage.js", "stage-scene.js", "stage-v6.js", "stage-v6replan.js"])
def test_director_scripts_parse(name):
    subprocess.run(["node", "--check", str(STATIC / name)], check=True)


def test_stage_page_loads_story_before_director_before_renderers():
    html = (STATIC / "stage.html").read_text()
    order = [html.index(f'src="/{n}"') for n in ("stage-fillv5.js", "stage-fillv6.js", "stage-story.js", "stage-shots.js", "stage-director.js", "stage-scene.js", "stage.js")]
    assert order == sorted(order)
    assert 'href="/stage-director.css"' in html


def test_recorder_takes_shots_and_variant_from_the_page():
    src = (ROOT / "experiments/mvp/ui/dev/record_shots.py").read_text()
    assert "window.__director.shots" in src and "--variant" in src
    js = (STATIC / "stage-director.js").read_text()
    assert "still_t" in js and "variant: PICK.variant" in js


def test_override_card_stays_for_shot_seven_and_picks_a_real_step():
    seven = shot(7)
    assert seven["xray"][0]["to"] - seven["xray"][0]["from"] <= 3.5  # three seconds in script draft 2
    assert any(o["type"] == "override" for o in seven["overlays"])
    assert seven["plan"]["run"] == "stowlm-showcase" and "xray_pick" not in seven  # first recorded override, never staged
    js = (STATIC / "stage-director.js").read_text()
    assert "pick = (want && contentOv(want)) ? want : xs.find(contentOv) || want || xs[0] || null" in js


# ------------------------------------------------------------------ /api/stage/showcase (kept for the plan picker tooling)
@historical("experiments/mvp/runs/showcase-v2-twostep/instances/*/outcome.json", "experiments/mvp/runs/showcase-v2-gold-1700-attempt1/bound.json")  # plan picker tooling
def test_showcase_status_reads_real_runs_and_says_why_a_number_is_missing():
    from app.stage_showcase import showcase_status
    got = showcase_status()
    assert got["order"] == [HERO, SECOND, THIRD]
    calls = {c["instance_id"]: c for c in got["calls"]}
    assert calls[HERO]["twostep"]["moves"] == 37 and calls[HERO]["lower_bound"] == 15 and calls[HERO]["gold"]["approved"] is True
    assert calls[SECOND]["twostep"]["moves"] == 29 and calls[SECOND]["lower_bound"] == 9
    third = calls[THIRD]
    assert third["twostep"]["moves"] is None and third["twostep"]["reason"] == "dead end"
    assert third["gold"]["approved"] is False and "no approved gold plan" in third["gold"]["reason"]
    assert third["lower_bound"] == 13


def _run(tmp, iid, result, outcome=None, name="stowlm-showcase"):
    folder = tmp / name / "instances" / iid
    folder.mkdir(parents=True)
    (folder / "result.json").write_text(result if isinstance(result, str) else json.dumps(result))
    if outcome is not None:
        (folder / "outcome.json").write_text(json.dumps(outcome))


def test_stowlm_state_three_states_and_fail_closed(tmp_path):
    from app.stage_showcase import stowlm_state
    assert stowlm_state(HERO, tmp_path) == {"state": "none"}
    _run(tmp_path, HERO, {"status": "dead_end", "plan": None, "message": "stuck", "metrics": {"dead_end_depths": [17], "n_actions": 17}})
    dead = stowlm_state(HERO, tmp_path)
    assert dead["state"] == "failed" and dead["kind"] == "dead_end" and dead["dead_end_depth"] == 17 and dead["title"].startswith("StowLM dead end")
    _run(tmp_path, SECOND, {"status": "ok", "plan": {}}, {"checker_passed": False})
    assert stowlm_state(SECOND, tmp_path)["kind"] == "rejected"
    _run(tmp_path, THIRD, {"status": "ok", "plan": {}}, {"checker_passed": True, "count": {"weighted_nonproductive": 31.0}})
    assert stowlm_state(THIRD, tmp_path) == {"state": "ok", "run": "stowlm-showcase", "moves": 31.0}
    _run(tmp_path, "garbled", "{not json")
    assert stowlm_state("garbled", tmp_path)["kind"] == "error"


def test_showcase_route_is_served():
    src = (ROOT / "app/server.py").read_text()
    assert '"/api/stage/showcase"' in src and "showcase_status" in src
    assert "test-91" not in (ROOT / "app/stage_showcase.py").read_text().replace("(D155, D157)", "")


def test_shot_11_names_the_pipeline_its_card_was_measured_on_and_the_tag_comes_back_after():
    """logic14 M16: the 24/24 card was measured on v3; beside it the corner tag says so, from the two manifests' digests."""
    eleven = v6_shot(11)
    assert eleven["build_tag"] == "3.1 on v3 ({{held_out_manifest}}); demo and package on v4 ({{package_manifest}})"
    assert not [s["id"] for s in v6()["shots"] if s.get("build_tag") and s["id"] != 11]
    figures = film_figures()
    assert figures["held_out_manifest"]["value"] != figures["package_manifest"]["value"]
    js = (STATIC / "stage-director.js").read_text()
    assert "if (!rt.s.build_tag) { el.innerHTML = BUILD_TAG.html; el.hidden = BUILD_TAG.hidden;" in js and "el.hidden = f.empty;" in js
