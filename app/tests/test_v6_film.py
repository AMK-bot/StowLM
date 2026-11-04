"""Film honesty, code review 7 findings 4, 5, 8 and 9, and the one config line that brings the adopted reader's recordings in.

Chapters run unmodified under node (v6_film_harness.js); figures and readings through app/v6.py. Every hero fact comes from hero().
The recordings below are synthetic and live in tmp_path: nothing here is a real reading."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import v6
from experiments.mvp.pipeline.contract.schema import sha256_json

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
HARNESS = Path(__file__).with_name("v6_film_harness.js")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def chapter(**scenario) -> dict:
    run = subprocess.run(["node", str(HARNESS)], input=json.dumps(scenario), capture_output=True, text=True, check=True, timeout=30)
    return json.loads(run.stdout)


def story(expr: str):
    code = f"global.window = globalThis; const S = require({json.dumps(str(STATIC / 'stage-story.js'))}); console.log(JSON.stringify({expr}));"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


# ------------------------------------------------------------------ finding 4: live is earned by the exact plan on screen
@needs_node
def test_a_live_plan_that_loads_is_labelled_live():
    out = chapter(chapter="replan")
    assert "Live, 7 s" in out["labels"] and out["rule"] == "The live rule" and "<b>4</b>" in out["big"]
    assert "could not be displayed" not in out["note"]


@needs_node
@pytest.mark.parametrize("scenario", [{"live_bundle": "fail"}, {"live_morph": "unfit"}, {"wrong_plan": True}], ids=["bundle fails", "no morph", "another plan came back"])
def test_a_live_plan_that_does_not_load_keeps_the_stored_label_and_says_so(scenario):
    out = chapter(chapter="replan", **scenario)
    assert "Live" not in out["labels"] and "Stored plan, computed on 1 Nov 2026" in out["labels"]
    assert "could not be displayed" in out["note"] and "could not be displayed" in out["labels"]
    assert out["rule"] == "The stored rule" and "<b>4</b>" not in out["big"]  # no live figure beside a stored plan
    assert "then better" not in out["trail"] and "first checked plan" not in out["trail"]


@needs_node
def test_a_stored_plan_shows_the_facts_file_and_its_caveat_and_drops_them_without_it():
    out = chapter(chapter="replan", stored_only=True)
    assert "Stored plan, computed on 1 Nov 2026" in out["labels"] and 'class="v6r-caveat">Pointwise forced means the old slot is forbidden.' in out["labels"]
    assert out["split"] == "1 box the rule forced out" and "<b>12</b>" in out["big"]
    bare = chapter(chapter="replan", stored_only=True, no_facts=True)  # no facts file: no date, no split, no caveat, no lifts; the plan still plays
    assert "computed on" not in bare["labels"] and "v6r-caveat" not in bare["labels"] and bare["split"] == "1 box moved" and bare["big"] == ""


# ------------------------------------------------------------------ finding 5: "Live, Wi-Fi off" comes from the reading, never from a mode
ADAPTER = "a" * 64
TUNED = {"kind": "stowlm_reader", "on_device": True}
GLIMMER = {"parser": "GlimmerParser", "model": "muse-glimmer-30b", "adapter_sha256": ADAPTER}
LIVE_READ = {"status": "done", "sentence": "Say it"}


@needs_node
@pytest.mark.parametrize("reading, live", [
    ({"mode": "live", "sentence": "Say it", "reader": TUNED, "evidence": GLIMMER, "live_read": LIVE_READ}, True),
    ({"mode": "recorded", "sentence": "Say it", "reader": TUNED, "evidence": GLIMMER, "live_read": None}, False),  # a recording of the right reader
    ({"mode": "stored", "sentence": "Say it", "reader": {"kind": "none", "on_device": False}, "evidence": {}, "live_read": None}, False),  # the stand-in rule no model read
    ({"mode": "live", "sentence": "Say it", "reader": {"kind": "muse_spark", "on_device": False}, "evidence": {"parser": "SparkParser"}, "live_read": LIVE_READ}, False),
    ({"mode": "live", "sentence": "Say it", "reader": TUNED, "evidence": {**GLIMMER, "adapter_sha256": None}, "live_read": LIVE_READ}, False),
    ({"mode": "live", "sentence": "Say it", "reader": TUNED, "evidence": GLIMMER, "live_read": {"status": "done", "sentence": "Another sentence"}}, False),
    ({"mode": "live", "sentence": "Say it", "reader": TUNED, "evidence": GLIMMER, "live_read": {"status": "running", "sentence": "Say it"}}, False),
    ({"mode": "live", "sentence": "Say it", "reader": TUNED, "evidence": GLIMMER}, False),
    ({}, False),
], ids=["earned", "recorded", "stand-in", "spark", "no adapter", "another sentence", "not finished", "no live read", "nothing"])
def test_only_a_completed_live_read_of_this_sentence_by_the_tuned_reader_is_live(reading, live):
    assert story(f"S.readLive({json.dumps(reading)})") is live


def test_no_configured_mode_reaches_the_live_caption():
    director = (STATIC / "stage-director.js").read_text()
    assert "V6STATE.modes" not in director and "CFG.runtime.reader_live = STORY.readLive(reading)" in director
    assert "STORY.readLive(d)" in (STATIC / "stage-v6reading.js").read_text()
    assert "stage-story.js" in (STATIC / "stage.html").read_text()


def test_no_reading_is_called_live_today():
    assert v6.reading()["mode"] != "live" and v6.reading()["live_read"] is None


# ------------------------------------------------------------------ finding 8: a broken x-ray player is a gap before the shot is committed
@needs_node
def test_a_working_player_plays():
    out = chapter(chapter="xray", player="ok")
    assert out["prepared"] and out["seek"] and out["drew"]
    late = chapter(chapter="xray", script="ok", player="ok")  # present from the start, and fetched by a script that loads
    assert late["prepared"]


@needs_node
@pytest.mark.parametrize("scenario", [{"script": "error"}, {"script": "ok"}, {"player": "throw"}, {"player": "reject"}, {"player": "nothing"}],
                         ids=["script fails", "script has no player", "mount throws", "mount rejects", "mount gives nothing"])
def test_a_broken_player_reaches_the_director_before_the_shot(scenario):
    out = chapter(chapter="xray", **scenario)
    assert out["prepared"] is False and out["after"] is False and out["seek"] is False and not out["drew"]


# ------------------------------------------------------------------ finding 9: every figure from one bound source
def v6_config() -> dict:
    text = (STATIC / "stage-shots.js").read_text()
    return json.loads(text[text.index("{", text.index("window.STAGE_SHOTS")): text.rindex("}") + 1])["variants"]["v6"]


def strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for item in node:
            yield from strings(item)
    elif isinstance(node, dict):
        for item in node.values():
            yield from strings(item)


# The only digits a v6 string may keep: the contract's design limit, names, plan labels and dates of the build. None of them is a measurement.
NOT_A_FIGURE = ("up to 195 boxes", "500 to 800 TEU", "up to 70 %", "12-weight rule", "Muse Glimmer 30B", "20-foot", "built 26 Oct – 4 Nov 2026", "v6:before",
                "recorded footage goes here at 4.3", "in 2024: BSAA Annual Report 2024–25",
                "3.1 on v3", "package on v4",  # a task id and the two manifest versions (M9); their hashes are figures
                "CC BY 4.0",  # the music licence (D298)
                "Built 26 Oct – 4 Nov 2026", "every 1 %")  # the end card's build dates; shot 12's step, the unit the exposure is cut into (its dollars are a figure)


def test_the_js_carries_no_figure():
    config = v6_config()
    assert not config.get("measured")
    shown = {key: config[key] for key in ("cards", "shots", "lower_third")}
    shown["cards"] = {name: {k: v for k, v in card.items() if k != "fill_from"} for name, card in shown["cards"].items()}  # a file path, never on screen
    for text in strings(shown):
        left = re.sub(r"\{\{[^{}]*\}\}", "", text)
        for words in NOT_A_FIGURE:
            left = left.replace(words, "")
        if text in ("3.1", "1.14") or re.fullmatch(r"\d+[a-z]?", text):  # task ids of a card's pending chip; a shot's id or beat
            continue
        assert not re.search(r"\d", left), text


def test_every_placeholder_a_shot_needs_is_served_or_filled_at_boot():
    served = set(v6.figures()["figures"])
    assert {"intake_dev_spark_pct", "reader_exact", "reader_spark_gap", "reader_sealed_total", "xray_study_sentences", "impact_low_billion", "impact_port_share_pct"} <= served
    text = json.dumps(v6_config(), ensure_ascii=False)
    chapters = set(re.findall(r"said\(\s*'([a-z_]+)'\s*\)", (ROOT / "app/v6.py").read_text()))  # the reading chapter's bars read a few too
    for name in served:
        assert "{{" + name + "}}" in text or name in chapters, name  # one reader, one use: nothing served for nothing


def test_every_figure_carries_scope_source_and_the_sha256_of_its_bytes():
    figures = v6.figures()["figures"]
    assert figures
    for name, row in figures.items():
        assert str(row["value"]).strip() and row["scope"], name
        path = ROOT / row["source"]
        assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"], name


def test_figures_read_their_fixtures_not_a_copy():
    figures = v6.figures()["figures"]
    parser = json.loads((ROOT / figures["reader_parser_exact"]["source"]).read_text())
    assert figures["reader_parser_exact"]["value"] == str(parser["exact_match_count"]) and figures["reader_sealed_total"]["value"] == str(parser["count"])
    study = json.loads((ROOT / figures["xray_study_sentences"]["source"]).read_text())
    assert study["adapter"]["adapter_sha256"] == v6._adoption()["adopted"]["adapter_sha256"]  # the adopted reader's arm, never another's
    assert figures["xray_study_sentences"]["value"] == str(study["adapter_arm"]["sentences"])
    adoption = v6._adoption()
    assert figures["reader_exact"]["value"] == str(adoption["sealed_128"][adoption["outcome"]]["exact_match_count"])
    methods = json.loads((ROOT / figures["intake_dev_spark_pct"]["source"]).read_text())["development_methods"]
    spark = next(row for name, row in methods.items() if "parser" not in name.lower())
    assert figures["intake_dev_spark_pct"]["value"] == f"{100 * spark['fields_correct'] / spark['fields_total']:.2f}"


def test_the_only_literal_figures_name_their_source_in_one_small_file():
    raw = json.loads(v6.FILM_FIGURES.read_text())
    for name, row in raw["figures"].items():
        assert row["value"] not in (None, "") and row["scope"] and (ROOT / row["named_source"].split(",")[0]).exists(), name


def test_a_figure_with_no_source_is_absent_and_the_others_stay(monkeypatch, tmp_path):
    monkeypatch.setattr(v6, "MASK_STUDY", tmp_path)  # the mask study's result files are gone
    out = v6.figures()
    assert not [name for name in out["figures"] if name.startswith("xray_study_")] and "stake_calls" in out["figures"]
    assert "xray_study" in out["gaps"]


def test_a_film_figure_that_loses_its_named_source_is_absent(monkeypatch, tmp_path):
    bad = tmp_path / "film-figures.json"
    raw = json.loads(v6.FILM_FIGURES.read_text())
    next(iter(raw["figures"].values()))["named_source"] = "docs/no-such-file.md, nowhere"
    gone = next(iter(raw["figures"]))
    bad.write_text(json.dumps(raw))
    monkeypatch.setattr(v6, "FILM_FIGURES", bad)
    served = v6.figures()["figures"]
    assert gone not in served and len(served) > 1


def test_a_reshaped_fixture_costs_its_own_figures_only(monkeypatch, tmp_path):
    (tmp_path / "parser.json").write_text('{"count": []}')
    monkeypatch.setattr(v6, "READER_PARSER", tmp_path / "parser.json")
    assert "reader_sealed_total" not in v6.figures()["figures"] and "reader_exact" in v6.figures()["figures"]
    monkeypatch.setattr(v6, "FILM_FIGURES", tmp_path / "gone.json")
    assert "stake_calls" not in v6.figures()["figures"]  # never a raise


# logic7 L1 / D286: the film card's partial-run line, from the published comparisons of both interrupted attempts with the final run
HELD_OUT = ROOT / "experiments/mvp/pipeline/sealed/runs/final-3/story-figures.json"
D284_LABEL = "held-out maps, frozen pipeline, run after two interrupted attempts; not the pre-registered sealed claim"


def test_the_partial_run_line_reads_both_published_comparisons():
    figures, data = v6.figures()["figures"], json.loads(HELD_OUT.read_text())
    overlaps = [attempt["overlap"] for attempt in data["interrupted_attempts"]]
    assert len(overlaps) == 2  # both interrupted attempts, never pooled into the result, only compared with it
    for n, o in enumerate(overlaps, 1):  # per attempt, never pooled (logic8 F8): status and lifts, plus attempt 1's verdict-only count
        assert figures[f"overlap{n}_rows"]["value"] == str(o["overlap_rows"]["value"]) and figures[f"overlap{n}_same"]["value"] == str(o["same_status_and_pass_lifts"]["value"])
    assert figures["overlap1_same"]["source"] == str(HELD_OUT.relative_to(ROOT)) and D284_LABEL in figures["overlap1_same"]["scope"]
    assert figures["overlap1_verdict"]["value"] == str(overlaps[0]["same_audited_L0_pass_verdict"]["value"])
    assert "overlap_same" not in figures  # the pooled count is retired


@pytest.mark.parametrize("change", ["gone", "label", "one_attempt"])
def test_no_published_comparison_no_partial_run_line(monkeypatch, tmp_path, change):
    path = tmp_path / "story-figures.json"
    if change != "gone":
        data = json.loads(HELD_OUT.read_text())
        if change == "label":
            data["run"]["label"]["value"] = "sealed, run once"
        else:
            data["interrupted_attempts"] = data["interrupted_attempts"][:1]
        path.write_text(json.dumps(data))
    monkeypatch.setattr(v6, "HELD_OUT_FIGURES", path)
    out = v6.figures()
    assert not any(k.startswith("overlap") for k in out["figures"]) and "partial_runs" in out["gaps"]
    assert "reader_exact" in out["figures"]  # a clean gap: the other figures stay


def test_the_figures_route_answers():
    calls = []

    class Handler:
        def json_response(self, status, body):
            calls.append((status, body))

    v6.handle_get(Handler(), "/api/v6/figures", {})
    assert calls[0][0] == 200 and calls[0][1]["available"] is True and calls[0][1]["figures"]


@needs_node
def test_only_a_bound_figure_reaches_the_film():
    good = {"value": "45", "scope": "development", "source": "x/y.json", "sha256": "b" * 64}
    bad = {"no_source": {**good, "source": ""}, "no_hash": {**good, "sha256": "nope"}, "no_scope": {**good, "scope": ""}, "no_value": {**good, "value": ""}}
    got = story(f"S.figureValues({json.dumps({'good': good, **bad})})")
    assert list(got) == ["good"] and got["good"]["value"] == "45" and "x/y.json" in got["good"]["source"] and "development" in got["good"]["source"]
    assert story("S.figureValues(null)") == {}


CARD_FILLS = {"sealed_envelope_pass": {"value": "20"}, "sealed_envelope_maps": {"value": "24"}}


@needs_node
def test_no_source_no_bar_and_no_denominator_on_the_card():
    card = v6_config()["cards"]["sealed"]
    empty = story(f"S.barCard({json.dumps(card)}, {{}}, {{}})")
    assert not empty["complete"] and all(r["text"] is None for r in empty["rows"])
    bare_scale = {key: row for key, row in CARD_FILLS.items() if key != "sealed_envelope_maps"}
    assert not story(f"S.barCard({json.dumps(card)}, {{measured: {json.dumps(bare_scale)}}}, {{}})")["complete"]
    full = story(f"S.barCard({json.dumps(card)}, {{measured: {json.dumps(CARD_FILLS)}}}, {{}})")
    assert full["complete"] and full["rows"][0]["width_pct"] == 100 * 20 / 24


@needs_node
def test_a_zero_is_a_figure():
    assert story("S.cell('{{a}} of 3', {measured: {a: {value: '0'}}}, {})")["text"] == "0 of 3"


def test_the_director_takes_figures_in_their_own_guard_and_the_chapter_bars_read_them():
    director = (STATIC / "stage-director.js").read_text()
    assert "STORY.figureValues" in director and "FLOW.api('figures')" in director
    assert "126" not in (ROOT / "app/v6.py").read_text() and "of 128" not in (ROOT / "app/v6.py").read_text()


# ------------------------------------------------------------------ the adopted reader's recordings: one config line
def real_hero() -> dict:
    return HERO.setdefault("config", v6.hero())


HERO: dict = {}
ADOPTED = "c" * 64  # synthetic, like everything these rows say


def row(**over) -> dict:
    """One line of reader_final/readings/readings.jsonl, in that file's shape."""
    hero = real_hero()
    sentence = over.pop("sentence", hero["instruction"]["sentence"])
    context = over.pop("context", hero["context"])
    evidence = {"parser": "GlimmerParser", "model": "muse-glimmer-30b", "adapter_sha256": ADOPTED, "masked": True, "wall_s": 6.5, "raw": json.dumps(hero["instruction"]["rule"]), **over.pop("evidence", {})}
    return {"key": sha256_json({"sentence": sentence, "context": context}), "sentence": sentence, "status": "read", "evidence": evidence, **over}


@pytest.fixture
def recorded(monkeypatch, tmp_path):
    config = real_hero()
    adoption = tmp_path / "adoption.json"
    adoption.write_text(json.dumps({"adopted": {"adapter_sha256": ADOPTED, "path": str(tmp_path)}, "outcome": "the-arm", "sealed_128": {"the-arm": {"exact_match_pct": 77.0}, "untrained": {"exact_match_pct": 33.0}}}))
    (tmp_path / "adapters.safetensors").write_bytes(b"0" * 2_500_000)
    monkeypatch.setattr(v6, "ADOPTIONS", (adoption,))
    folder = tmp_path / "readings"
    folder.mkdir()
    monkeypatch.setattr(v6, "hero", lambda: {**config, "reader_recordings": str(folder)})

    def use(*rows, timing=None):
        (folder / "readings.jsonl").write_text("".join(json.dumps(item) + "\n" for item in rows) + '{"half a li')
        if timing:
            (folder / "offline-timing.json").write_text(json.dumps(timing))
    return use


def test_the_config_line_is_there_and_off():
    config = json.loads(v6.HERO_FILE.read_text())
    assert "reader_recordings" in config and config["reader_recordings"] is None


def test_no_recording_keeps_the_stand_in(monkeypatch):
    monkeypatch.setattr(v6, "_worded_reading", lambda config: (_ for _ in ()).throw(LookupError("no reading")))  # the hero wording read: undo it for the stand-in state
    now = v6.reading()
    assert now["mode"] == "stored" and now["reader"]["kind"] != "stowlm_reader" and now["stand_in_note"] and "reader" in v6.stand_ins()


def test_a_recording_of_the_adopted_reader_replaces_the_stand_in(recorded):
    recorded(row(sentence="Another sentence entirely."), row(), timing={"adapter_sha256": ADOPTED, "reader_seconds": 4.5, "reader_peak_gb": 9.5})
    now = v6.reading()
    assert now["mode"] == "recorded" and now["label"] == v6.RECORDED and now["reader"]["kind"] == "stowlm_reader" and now["reader"]["on_device"] is True
    assert now["evidence"]["adapter_sha256"] == ADOPTED and now["seconds"] == 4.5 and now["peak_gb"] == 9.5 and now["adapter_mb"] == 2.5
    assert now["stand_in_note"] is None and "reader" not in v6.stand_ins() and v6._replan_rule()["stand_in_note"] is None
    assert now["bars"]["with"] == 77.0 and now["bars"]["without"] == 33.0 and now["live_read"] is None
    assert "raw" not in now["evidence"]  # model text never reaches the page


def test_timing_for_another_adapter_is_not_this_readers_time(recorded):
    recorded(row(), timing={"adapter_sha256": "0" * 64, "reader_seconds": 1.0, "reader_peak_gb": 1.0})
    now = v6.reading()
    assert now["seconds"] == 6.5 and now["peak_gb"] is None


@pytest.mark.parametrize("over", [{"sentence": "Another sentence entirely."}, {"context": {"id": "another ship"}}, {"status": "malformed"}, {"on_device": False},
                                  {"evidence": {"adapter_sha256": "0" * 64}}, {"evidence": {"masked": False}},
                                  {"evidence": {"parser": "SparkParser", "model": "muse-spark-1.3", "adapter_sha256": None}},
                                  {"evidence": {"raw": json.dumps({"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 999}})}},
                                  {"evidence": {"raw": json.dumps({"schema_version": "stowlm.reader/2", "constraint": {"type": "one_port_per_hatch"}})}}],
                         ids=["another sentence", "another instance", "malformed", "not on device", "another adapter", "unmasked", "spark", "off this ship", "another rule"])
def test_a_recording_that_is_not_this_reading_is_refused_as_a_clean_gap(recorded, over):
    recorded(row(**over))
    out = v6.feature(v6.reading)
    assert out["available"] is False and "recording" in out["reason"]
    assert "reader" in v6.stand_ins() and v6._replan_rule()["stand_in_note"]  # the replan keeps its stored rule, still labelled


def test_recordings_that_have_not_landed_are_a_clean_gap(recorded):
    assert v6.feature(v6.reading)["available"] is False and "reader" in v6.stand_ins()


def test_peak_heel_comes_only_from_a_full_walk_of_the_plan_on_screen(monkeypatch, tmp_path):
    config = v6.hero()
    got = v6._peak_heel(None, {}, config)
    walk = json.loads(v6.HERO_GAUGES.read_text())
    if got is not None:  # bound: this plan's bytes, one frame per lift and the first
        row = walk["plans"]["after"]
        assert got["peak_deg"] == round(row["max_abs_heel_deg"], 3) and got["frames"] == row["lifts"] + 1 and got["limit_deg"] > 0
    walk["plans"]["after"]["plan_sha256"] = "0" * 64
    (tmp_path / "g.json").write_text(json.dumps(walk))
    monkeypatch.setattr(v6, "HERO_GAUGES", tmp_path / "g.json")
    assert v6._peak_heel(None, {}, config) is None  # another plan's walk is no figure
