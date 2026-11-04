"""Demo v6 opening: intake, voice and reading chapters. Offline, fixtures only, no Chrome."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import v6

STATIC = Path(v6.__file__).with_name("static")
BANNED = re.compile(r"\b(approved|certified|industry|production|instant|real-time)\b", re.I)


@pytest.fixture(scope="module")
def intake():
    return v6.intake()


def test_fixture_is_cached(intake):
    assert v6.intake() is intake, "/api/v6/state must not reparse 12 MB per call"


def test_plain_questions_every_card_answerable(intake):
    assert intake["questions"][0]["reason"] == "cross_document_conflict"
    assert {q["reason"] for q in intake["questions"]} >= {"cross_document_conflict", "amendment_review"}
    for question in intake["questions"]:
        assert not re.search(r"gross_mass_kg|demo-|_\w+:|error", question["text"])
        assert question["scope"] == "document" or question["box"] in question["text"]
        assert sum(option["recorded"] for option in question["options"]) == 1
        assert all(len(option["label"]) <= 24 for option in question["options"])


def test_a_conflict_says_who_said_what(intake):
    texts = [q["text"] for q in intake["questions"]]
    assert any("tally photo says" in t and "Where does box" in t for t in texts)
    assert any("VGM declaration says" in t and "really weigh" in t for t in texts)
    assert any("amendment says" in t for t in texts)


def test_every_flight_points_at_its_real_source(intake):
    docs = {doc["id"]: doc for doc in intake["documents"]}
    for flight in intake["flights"]:
        doc = docs.get(flight["document"])
        if doc is None:  # a paper with no slot on the desk: the page drops this flight
            continue
        if doc["kind"] == "email":
            assert flight["quote"] in doc["text"] and flight["from"] in flight["quote"]
        elif doc["kind"] == "photo":
            assert flight["from"] in intake["photo_lines"][flight["photo"]]
        elif doc["kind"] == "sheet":
            column, row = ord(flight["cell"][0]) - 65, int(flight["cell"][1:]) - 1
            assert doc["grid"][row][column] == flight["from"]
    on_desk = {flight["document"] for flight in intake["flights"]}
    assert {"booking_email", "gate_csv", "quay_photo"} <= on_desk
    assert all(any(f["row"] == y for f in intake["flights"]) for y in range(len(intake["rows"])))


def test_it_plays_the_authority_demo_and_says_it_isnt_the_whole_ship(intake):
    demo = v6.INTAKE_AUTHORITY / "demo"
    assert v6._intake_case() == demo
    final = json.loads((demo / "final.json").read_text())
    assert len(intake["rows"]) == len(final["rows"]) and all("?" not in row["after"].values() for row in intake["rows"])
    assert "not the whole ship" in intake["list_scope"] and str(len(final["rows"])) in intake["list_scope"]
    assert "not a whole ship" in intake["summary"]["scope"] and "synthetic, not accuracy" in intake["summary"]["scope"]


def test_a_document_question_shows_once(intake):
    reading = json.loads((v6.INTAKE_AUTHORITY / "demo/reading.json").read_text())
    asked = [q for q in reading["questions"] if q["row"] is None]
    cards = [q for q in intake["questions"] if q["scope"] == "document"]
    assert asked and len(cards) == len(asked) and all(card["row"] is None and card["column"] is None for card in cards)
    assert all(len(card["options"]) == 1 and "whole sheet" in card["text"] for card in cards)
    assert intake["questions"][0]["reason"] == "cross_document_conflict", "the film holds on the conflict, both documents lit"


def test_derived_facts_are_labelled_derived(intake):
    reading = json.loads((v6.INTAKE_AUTHORITY / "demo/reading.json").read_text())
    closed = {(s["row"], s["field"]) for s in reading["field_sources"] if s.get("evidence") == "closed_world_declaration" and s.get("value") in (None, False)}
    assert "derived" in intake["derived_note"] and "tare" in intake["derived_note"] and "approximate" in intake["derived_detail"]
    marked = [(y, column) for y, row in enumerate(intake["rows"]) for column in row["derived"]]
    assert marked and all((y, column) in closed and intake["rows"][y]["before"][column] == "none" for y, column in marked)
    assert all(row["before"]["dg"] != "none" for row in intake["rows"] if "dg" not in row["derived"])


def test_closing_figures_match_their_source(intake):
    """The README's table and its results file both, so a disagreement between them fails here too."""
    source = json.loads((v6.INTAKE_DEVELOPMENT).read_text())["development_methods"]
    spark, parser = source["Muse Spark + software"], source["Literal-header parser"]
    table = intake["summary"]["table"]
    assert table["head"][1:] == ["Muse Spark + software", "Literal-header parser"] and table["scope"] == "development, realistic synthetic documents"
    rows = {row[0]: row[1:] for row in table["rows"]}
    assert rows["Fields"] == [f"{100 * m['fields_correct'] / m['fields_total']:.2f} %" for m in (spark, parser)]
    assert rows["Rows exact"] == [f"{m['rows_exact']} of {m['rows_total']}" for m in (spark, parser)]
    assert rows["Traps stopped"] == [f"{m['planted_problems_stopped']} of {m['planted_problems_total']}" for m in (spark, parser)]
    assert rows["Boxes stopping"] == [f"{100 * m['boxes_stopped'] / m['rows_total']:g} %" for m in (spark, parser)]
    readme = (v6.PIPELINE / "intake/README.md").read_text()
    for name, row in (("Correct resolved business fields", 0), ("Rows exact", 1), ("Planted problems found by field + reason", 2), ("Planner workload", 3)):
        cells = [cell.strip() for cell in next(line for line in readme.splitlines() if line.startswith(f"| {name}")).split("|")[1:-1]]
        spark_cell, parser_cell = cells[3], cells[4]  # the final development replay
        shown = table["rows"][row]
        for cell, value in ((spark_cell, shown[1]), (parser_cell, shown[2])):
            assert value.split(" ")[0] in cell.replace("/", " ").replace("(", " ").replace("%", " ").split()


def test_the_sealed_result_takes_over_with_no_code_change(monkeypatch, tmp_path):
    sealed = tmp_path / "metrics.json"
    monkeypatch.setattr(v6, "INTAKE_RESULTS", (sealed, v6.INTAKE_DEVELOPMENT))
    assert v6.intake_figures()["scope"] == "development, realistic synthetic documents"
    sealed.write_text(json.dumps({"evaluation_split": "sealed_intake_v1", "caption": "6 labelled synthetic cases, 72 boxes", "methods": {"Muse Spark + software": method(90, 12), "Literal-header parser": method(50, 30)}}))
    figures = v6.intake_figures()
    assert figures["scope"] == "Sealed, scored once · 6 labelled synthetic cases, 72 boxes" and figures["intake_pct"] == 90


def test_no_new_results_means_no_figures_at_all(intake, monkeypatch):
    summary = json.dumps(intake["summary"])
    assert "synthetic" in intake["summary"]["scope"].lower()
    assert not re.search(r"216|269|52 of|251|1071|1096|1155", summary) and "metrics" not in intake and "heldout" not in intake
    assert "Held-out" not in summary and "held-out" not in summary, "the development replay is spent, never held-out"
    monkeypatch.setattr(v6, "INTAKE_RESULTS", ())
    monkeypatch.setattr(v6, "_fill", lambda: {})
    assert v6.intake_figures() is None


def method(fields: int, stopped: int) -> dict:
    return {"fields_correct": fields, "fields_total": 100, "boxes_stopped": stopped, "rows_total": 40, "planted_problems_stopped": 15, "planted_problems_total": 16, "silent_wrong_values": 0}


@pytest.mark.parametrize("data, lines", [
    ({"intake_field_accuracy_pct": 91.5, "intake_baseline_name": "Literal-header parser", "intake_baseline_pct": 40, "intake_documents": 40},
     ["Muse Spark: 91.5 % of fields", "Literal-header parser: 40 % · 40 held-out synthetic documents"]),
    ({"intake_field_accuracy_pct": 91.5, "intake_baseline_name": "Literal-header parser", "intake_baseline_pct": 40, "intake_scope": "sealed, 6 synthetic cases"},
     ["Muse Spark: 91.5 % of fields", "Literal-header parser: 40 % · sealed, 6 synthetic cases"]),
])
def test_story_numbers_land_with_the_parser_beside_them(monkeypatch, tmp_path, data, lines):
    path = tmp_path / "results.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(v6, "INTAKE_RESULTS", (tmp_path / "absent.json", path))
    assert v6._intake_summary(v6.intake_figures(), 8, True)["lines"] == lines


def test_a_results_file_is_a_table_beside_the_parser(monkeypatch, tmp_path):
    data = {"evaluation_split": "realistic_v7_blind", "caption": "5 labelled synthetic cases, 40 boxes", "methods": {"Muse Spark + software": method(90, 12), "Literal-header parser": method(50, 30)}}
    path = tmp_path / "results.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(v6, "INTAKE_RESULTS", (path,))
    table = v6._intake_summary(v6.intake_figures(), 8, True)["table"]
    assert table["head"] == ["", "Muse Spark + software", "Literal-header parser"] and table["scope"] == "Held-out · 5 labelled synthetic cases, 40 boxes"
    assert table["rows"] == [["Fields", "90 %", "50 %"], ["Traps stopped", "15 of 16", "15 of 16"], ["Boxes stopping", "30 %", "75 %"]]


@pytest.mark.parametrize("data", [
    {"caption": "x", "methods": {"Muse Spark + software": {"fields_correct": 9, "fields_total": 10}}},  # no parser
    {"methods": {"Muse Spark + software": {"fields_correct": 9, "fields_total": 10}, "Literal-header parser": {"fields_correct": 5, "fields_total": 10}}},  # no scope
    {"intake_field_accuracy_pct": 90, "intake_documents": 5},
    {"caption": "x", "methods": {"Muse Spark + software": {"fields_correct": 9, "fields_total": 0}, "Literal-header parser": {"fields_correct": 5, "fields_total": 10}}},
    {"before_answers": {"fields_correct": 216, "fields_total": 269}, "status": "complete"},  # the old shape is not a result
    {"rows": [1, 2]}, [1, 2], "junk",
])
def test_a_figure_without_baseline_or_scope_is_no_figure(monkeypatch, tmp_path, data):
    path = tmp_path / "results.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(v6, "INTAKE_RESULTS", (path,))
    assert v6.intake_figures() is None


# ---------------------------------------------------------------- a fixture that changes shape costs a panel, not the chapter
def edit(path: Path, change) -> None:
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


@pytest.fixture
def case(monkeypatch, tmp_path):
    copy = tmp_path / "demo"
    copy.mkdir()
    for name in ("documents.json", "reading.json", "final.json", "answers.json", "metrics.json"):
        data = json.loads((v6.INTAKE_DEMO / name).read_text())
        if isinstance(data, dict) and "records" in data:
            data["records"] = [{"mode": "recorded", "model": "muse-spark"}]
        (copy / name).write_text(json.dumps(data))
    monkeypatch.setattr(v6, "INTAKE_DEMO", copy)
    monkeypatch.setattr(v6, "INTAKE_AUTHORITY", tmp_path / "none")
    monkeypatch.setattr(v6, "INTAKE_REALISTIC", tmp_path / "none")
    monkeypatch.setattr(v6, "INTAKE", tmp_path / "none")
    monkeypatch.setattr(v6, "INTAKE_RESULTS", (tmp_path / "none.json",))
    monkeypatch.setattr(v6, "_memo", {})
    return copy


def unknown_question(data):
    data["questions"].append({"id": "QZXU:pod:brand_new", "row": 2, "field": "pod", "prompt": "RAW PROMPT", "candidates": [], "reason": "brand_new"})


@pytest.mark.parametrize("file, change, check", [
    ("reading.json", unknown_question, lambda b: any(q["reason"] == "brand_new" and q["box"] in q["text"] and "RAW PROMPT" not in q["text"] and q["check"] == "declared" for q in b["questions"]) and len(b["questions"]) == 8),
    ("reading.json", lambda d: d["questions"].append({"id": "x", "row": 1, "field": "amendment", "prompt": "p", "reason": "amendment_review"}), lambda b: any(q["column"] is None and q["after"] == "Answered" for q in b["questions"])),
    ("reading.json", lambda d: d["questions"].append({"id": "x", "row": None, "field": None, "prompt": "p", "reason": "whole_document"}), lambda b: any(q["row"] is None for q in b["questions"])),
    ("reading.json", lambda d: d["questions"].append({"bad": True}), lambda b: len(b["questions"]) >= 7),
    ("reading.json", lambda d: d.update(asked=d.pop("questions")), lambda b: b["questions"] == [] and b["rows"]),
    ("reading.json", lambda d: d.update(sources=d.pop("field_sources")), lambda b: b["flights"] == [] and b["photo_lines"] == []),
    ("reading.json", lambda d: d.update(lines=d.pop("rows")), lambda b: len(b["rows"]) == 13),
    ("reading.json", lambda d: d.pop("records"), lambda b: b["mode"] == "recorded" and b["model"] is None),
    ("reading.json", lambda d: d["rows"][0].update(gross_mass_kg="heavy", reefer=True, reefer_setpoint_c="cold"), lambda b: b["rows"][0]["before"]["mass"] == "?"),
    ("reading.json", lambda d: [s.pop("quote", None) for s in d["field_sources"][:40]], lambda b: b["rows"]),
    ("reading.json", lambda d: [q.update(candidates=["{not json", 7]) for q in d["questions"]], lambda b: b["questions"]),
    ("final.json", lambda d: d.update(lines=d.pop("rows")), lambda b: b["questions"] == [] and b["summary"]["headline"] == "13 rows read"),
    ("documents.json", lambda d: d.pop(2), lambda b: len(b["documents"]) == 2),
    ("documents.json", lambda d: d[0].pop("id"), lambda b: len(b["documents"]) == 2),
    ("metrics.json", lambda d: d.pop("before_answers"), lambda b: b["summary"]["lines"] == []),
    ("metrics.json", lambda d: d.pop("reading_wall_s", None), lambda b: b["wall_s"] is None),
    ("answers.json", lambda d: d.clear(), lambda b: b["questions"]),
])
def test_a_changed_fixture_loses_its_panel_not_the_chapter(case, file, change, check):
    edit(case / file, change)
    block = v6.feature(v6.intake)
    assert block["available"] is True and check(block), block.get("reason")
    json.dumps(block)
    for question in block["questions"]:
        assert sum(option["recorded"] for option in question["options"]) == 1 and question["text"]


@pytest.mark.parametrize("name, change", [("final.json", None), ("answers.json", None), ("documents.json", None), ("documents.json", {"renamed": []}), ("final.json", [1])])
def test_a_missing_file_is_a_missing_panel(case, name, change):
    (case / name).unlink() if change is None else (case / name).write_text(json.dumps(change))
    assert v6.feature(v6.intake)["available"] is True


def test_no_reading_at_all_is_a_clean_gap(case):
    (case / "reading.json").write_text("{half a fi")
    block = v6.feature(v6.intake)
    assert block["available"] is False and block["reason"]


def test_the_old_recording_still_plays(case):
    block = v6.feature(v6.intake)
    assert len(block["rows"]) == 13 and len(block["questions"]) == 7 and "booking email" in block["questions"][0]["text"]
    assert {f["from"] for f in block["flights"] if f["row"] == 0 and f["column"] == "pod"} == {"HKHKG", "VNSGN"}


def test_new_documents_take_their_slot_by_role():
    kinds = {}
    roles = {"booking": "email", "shipping_instructions": "sheet", "tally": "photo", "amendment": "text", "vgm": "text"}
    for role, kind in roles.items():
        assert v6._doc_view({"id": f"demo-{role}", "role": role, "text": "a,b\n", "mime_type": "text/plain"}, kinds)["kind"] == kind


def test_a_realistic_recording_plays_through_the_same_seam(monkeypatch, tmp_path):
    """The v7 file shapes: role-named documents, cell-bound sources, new reasons, a box shut out. Reading stands in for the final."""
    source = v6.INTAKE_REALISTIC / "development_02"
    demo = tmp_path / "realistic/demo"
    demo.mkdir(parents=True)
    reading = json.loads((source / "reading.json").read_text())
    reading["records"] = [{"mode": "recorded", "model": "muse-spark"}]
    final = {**reading, "status": "complete", "rows": reading["rows"][1:]}
    for name, data in (("reading.json", reading), ("final.json", final), ("assembly.json", {}), ("documents.json", json.loads((source / "documents.json").read_text()))):
        (demo / name).write_text(json.dumps(data))
    monkeypatch.setattr(v6, "INTAKE_REALISTIC", tmp_path / "realistic")
    monkeypatch.setattr(v6, "INTAKE_AUTHORITY", tmp_path / "none")
    monkeypatch.setattr(v6, "INTAKE_RESULTS", (tmp_path / "none.json",))
    monkeypatch.setattr(v6, "_memo", {})
    block = v6.feature(v6.intake)
    assert block["available"] is True and len(block["rows"]) == 8 and block["questions"]
    assert {doc["kind"] for doc in block["documents"]} >= {"email", "sheet", "photo"} and block["flights"]
    assert sum(doc["id"] in {"booking_email", "gate_csv", "quay_photo"} for doc in block["documents"]) == 3
    ids = {doc["id"] for doc in block["documents"]}
    assert all(flight["document"] in ids for flight in block["flights"])
    assert all(sum(option["recorded"] for option in question["options"]) == 1 for question in block["questions"])


def synthetic_wav(path: Path, seconds: float, rate: int = 16000) -> None:
    import math
    import struct
    import wave
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1), stream.setsampwidth(2), stream.setframerate(rate)
        stream.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(i / 7) * (0.5 + 0.5 * math.sin(i / rate * 9)))) for i in range(int(seconds * rate))))


@pytest.fixture
def clip(monkeypatch, tmp_path):
    config = v6.hero()
    config.pop("radio", None)  # these tests are the separate clip's: with no hero-wording files configured it stands in (test_v6_radio.py has the rest)
    fixed = tmp_path / "instruction.json"  # with no wording files the selection names no rule, so the same rule comes from a fixture file
    fixed.write_text(json.dumps({"instance_sha256": config["instance_sha256"], "canonical_rules": config["instruction"]["canonical_rules"], "sentences": [config["instruction"]["sentence"]]}))
    config["instructions"] = {**config["instructions"], config["slot"]: str(fixed)}
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "audio").mkdir()
    for suffix in ("radio", "clean"):
        name = f"{config['clip']['id']}-{suffix}.json"
        (tmp_path / "checkpoints" / name).write_text((v6.VOICE / "checkpoints" / name).read_text())
    wav = f"{config['clip']['id']}-radio.wav"
    shipped = v6.VOICE / "audio" / wav
    if shipped.is_file():
        (tmp_path / "audio" / wav).write_bytes(shipped.read_bytes())
    else:  # the stock-TTS clip isn't shipped (licence unclear): a synthetic 16-bit take of the recorded length stands in for these panel tests
        seconds = json.loads((tmp_path / "checkpoints" / f"{config['clip']['id']}-radio.json").read_text())["asr_evidence"]["audio_seconds"]
        synthetic_wav(tmp_path / "audio" / wav, seconds)
    hero = tmp_path / "hero.json"
    hero.write_text(json.dumps(config))
    monkeypatch.setattr(v6, "VOICE", tmp_path)
    monkeypatch.setattr(v6, "HERO_FILE", hero)
    monkeypatch.setattr(v6, "FILL", tmp_path / "nofill.json")
    paths = {suffix: tmp_path / "checkpoints" / f"{config['clip']['id']}-{suffix}.json" for suffix in ("radio", "clean")}
    return {**paths, "wav": tmp_path / "audio" / wav, "hero": hero}


@pytest.mark.parametrize("change, check", [
    (lambda c: c["clean"].unlink(), lambda b: b["fixed"] is None and b["words"]),
    (lambda c: c["wav"].unlink(), lambda b: b["peaks"] == [] and b["audio"] is None and b["audio_note"] == "audio not included in this copy" and b["words"]),
    (lambda c: c["wav"].write_bytes(b"not a wav"), lambda b: b["peaks"] == [] and b["audio"] is None and b["audio_note"] is None),
    (lambda c: edit(c["radio"], lambda d: d.pop("asr_evidence")), lambda b: b["seconds"] is None),
    (lambda c: edit(c["radio"], lambda d: d.pop("answer")), lambda b: b["refused_first"]["refusal"] is None),
    (lambda c: edit(c["radio"], lambda d: d.update(answer={"refusal": "no"})), lambda b: b["refused_first"]["refusal"] is None),
    (lambda c: edit(c["hero"], lambda d: d["clip"].pop("fixes")), lambda b: not any(w["fix"] for w in b["words"])),
])
def test_voice_loses_a_panel_not_the_chapter(clip, change, check):
    change(clip)
    block = v6.feature(v6.voice)
    assert block["available"] is True and check(block)


def test_voice_with_no_transcript_is_a_clean_gap(clip):
    edit(clip["radio"], lambda d: d.update(text=d.pop("transcript")))
    assert v6.feature(v6.voice)["available"] is False


SPARK = {"parser": "SparkParser", "model": "muse-spark-1"}
GLIMMER = {"parser": "GlimmerParser", "model": "muse-glimmer-30b", "adapter_sha256": "a" * 64}


@pytest.fixture
def take(clip):
    """A frozen take of this hero's own rule, named by ``readings``: what the chapter gets when a real reading lands."""
    now = v6.hero()
    edit(clip["clean"], lambda d: d.update(transcript=now["instruction"]["sentence"], answer=now["instruction"]["rule"], parser_evidence=SPARK))
    edit(clip["hero"], lambda d: d.update(readings={now["slot"]: d["clip"]["id"]}))
    return clip


@pytest.mark.parametrize("change, check", [
    (lambda c: c["radio"].unlink(), lambda b: b["refused_first"]["refusal"] is None),
    (lambda c: edit(c["clean"], lambda d: d.pop("parser_evidence")), lambda b: b["reader"]["kind"] == "unknown"),
    (lambda c: edit(c["clean"], lambda d: d.update(parser_evidence="x")), lambda b: b["reader"]["kind"] == "unknown"),
    (lambda c: edit(c["clean"], lambda d: d.update(parser_latency_s="soon")), lambda b: b["seconds"] is None),
    (lambda c: edit(c["hero"], lambda d: d.pop("reader_note_recorded")), lambda b: b["stand_in_note"] == ""),
    (lambda c: c["hero"].with_name("nofill.json").write_text('{"reader_exact_pct": "x", "untrained_exact_pct": 2}'), lambda b: b["bars"] is None),
])
def test_reading_loses_a_panel_not_the_chapter(take, change, check):
    change(take)
    block = v6.feature(v6.reading)
    assert block["available"] is True and block["mode"] == "recorded" and check(block) and block["rule"]


@pytest.mark.parametrize("change", [lambda d: d.pop("answer"), lambda d: d.pop("transcript"), lambda d: d.update(answer="yes"),
                                    lambda d: d.update(answer={"constraint": {"type": "close_bay"}}), lambda d: d.update(answer={"constraint": {"type": "close_bay", "bay": 1}})])
def test_a_recording_that_isnt_this_rule_never_reaches_the_page_and_nothing_claims_a_model_read_it(take, change):
    edit(take["clean"], change)
    block = v6.feature(v6.reading)
    assert block["available"] is True and block["mode"] == "stored" and block["label"] is None and block["reader"] == v6.NO_READER
    assert block["sentence"] == v6.hero()["instruction"]["sentence"] and "stands in" in block["stand_in_note"]


def test_no_instruction_means_nothing_to_confirm(clip, monkeypatch):
    config = v6.hero()
    monkeypatch.setattr(v6, "hero", lambda: {**config, "instruction": None})
    assert v6.feature(v6.reading)["available"] is False


def test_voice_is_the_frozen_take(clip):
    data, fixes = v6.voice(), v6.hero()["clip"]["fixes"]
    assert data["transcript"] == v6._clip("radio")["transcript"]
    assert [w["heard"] for w in data["words"] if w["fix"]] == list(fixes)  # the data keeps them; the chapter stages none (H1)
    assert data["refused_first"]["refusal"]["code"] == "ambiguous" and len(data["peaks"]) > 100 and data["audio_note"] is None
    assert data["label"] == "Stock text-to-speech voice · simulated radio" and data["lines"] is None


SHIPPED_CLIP = v6.VOICE / "audio" / f"{v6.hero()['clip']['id']}-radio.wav"


@pytest.mark.skipif(not SHIPPED_CLIP.is_file(), reason=f"{SHIPPED_CLIP.relative_to(v6.ROOT)} (stock TTS) isn't shipped in this copy; the panel tests use a synthetic take")
def test_the_frozen_take_s_waveform_is_the_recorded_clip(monkeypatch):
    monkeypatch.setattr(v6, "hero_wording", lambda: (_ for _ in ()).throw(LookupError("the separate clip's test")))
    data = v6.voice()
    assert data["peaks"] == v6._peaks(SHIPPED_CLIP) and len(data["peaks"]) > 100 and data["audio"] == "/api/v6/asset/radio.wav"


def test_once_the_wording_files_land_the_take_is_the_chosen_wordings_own():
    pick = v6.optional(v6.hero_wording)
    if not pick or not pick["radio"]:
        pytest.skip("the radio readings have not landed")
    data = v6.voice()
    assert data["transcript"] == pick["radio"]["transcript"] and data["sentence"] == pick["sentence"] and data["fixed"] is None
    assert not any(w.get("fix") for w in data["words"]) and data["refused_first"]["refusal"] is None
    assert data["label"].startswith("Stock text-to-speech voice · simulated radio") and sum(line["chosen"] for line in data["lines"]) == 1


def test_with_no_recording_the_page_says_no_model_read_it(monkeypatch):
    config = v6.hero()
    monkeypatch.setattr(v6, "hero", lambda: {**config, "readings": {}})
    monkeypatch.setattr(v6, "_worded_reading", lambda config: (_ for _ in ()).throw(LookupError("no reading")))  # the hero wording read: undo it for the stand-in state
    data = v6.reading()
    assert data["reader"] == v6.NO_READER and data["reader"]["on_device"] is False and data["label"] is None and data["mode"] == "stored"
    assert data["bars"] is None or "stand_in_note" in data
    assert "stands in" in data["stand_in_note"] and "task" not in data["stand_in_note"]
    assert "it can slip, so the planner confirms" in data["scope_line"] and "never guessed" not in data["scope_line"]  # D267: measured false, withdrawn


@pytest.mark.parametrize("evidence, words, local, stands_in", [(SPARK, "cloud", False, True), (GLIMMER, "on this laptop", True, False)])
def test_reading_is_labelled_from_evidence(take, evidence, words, local, stands_in):
    edit(take["clean"], lambda d: d.update(parser_evidence=evidence))
    data = v6.reading()
    assert data["mode"] == "recorded" and data["label"] == "Recorded answer"
    assert data["reader"]["on_device"] is local and words in data["reader"]["label"]
    assert bool(data["stand_in_note"]) is stands_in
    if stands_in:
        assert "no model has read" not in data["stand_in_note"] and "task" not in data["stand_in_note"]


def test_reader_bars_need_the_readers_own_number(monkeypatch):
    assert v6._reader_bars({}) is None and v6._reader_bars({"reader_exact_pct": 90}) is None
    bars = v6._reader_bars({"reader_exact_pct": 90.1, "untrained_exact_pct": 12})
    assert bars["with"] == 90.1 and "126 of 128" in bars["comparisons"]


def test_local_claims_only_behind_evidence():
    source = (STATIC / "stage-v6reading.js").read_text()
    # D296: the live span says only "Live", earned by a finished live read, never a setting; where it ran ("on this laptop") is reader_label's, on evidence
    assert "${STORY.readLive(d) ? '<span class=\"local\">Live</span>' : ''}" in source and source.count("'<span class=\"local\">Live") == 1
    assert "on this laptop" not in source and "Wi-Fi off" not in source, "that label comes from reader_label, on evidence"
    story = (STATIC / "stage-story.js").read_text()
    assert "r.mode === 'live' && who.kind === 'stowlm_reader' && who.on_device === true" in story and "read.status === 'done'" in story


def test_shot_three_quotes_the_recorded_figures():
    """The caption names its figures; /api/v6/figures serves them, and they're the chapter's own recorded figures."""
    shots, figures, served = (STATIC / "stage-shots.js").read_text(), v6.intake_figures(), {k: f["value"] for k, f in v6.figures()["figures"].items()}
    assert "development: Spark {{intake_dev_spark_pct}} % of fields, a parser with no model {{intake_dev_parser_pct}} %" in shots
    assert "{{intake_demo_boxes}} sample boxes, another voyage" in shots
    assert float(served["intake_dev_spark_pct"]) == figures["intake_pct"] and float(served["intake_dev_parser_pct"]) == figures["baseline_pct"]
    assert served["intake_demo_boxes"] == str(len(v6.intake()["rows"]))
    assert not re.search(r"\b(70\.79|36\.03)\b", shots)  # never typed in (code7 finding 9)


def test_screen_words_obey_the_headline(intake):
    text = json.dumps([intake["summary"], [q["text"] for q in intake["questions"]], v6.voice()["label"], v6.reading()["scope_line"]])
    for name in ("stage-v6intake.js", "stage-v6voice.js", "stage-v6reading.js"):
        text += (STATIC / name).read_text()
    assert not BANNED.search(text)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_chapters_register_and_hook_has_a_hole():
    harness = """
    const fs = require('fs'), defs = [];
    global.window = { StageV6: { chapter: (c) => defs.push(c), seek: async (id, t) => [id, t] } };
    global.devicePixelRatio = 1; global.addEventListener = () => {};
    for (const f of ['intake', 'voice', 'reading']) eval(fs.readFileSync(process.argv[1] + `/stage-v6${f}.js`, 'utf8'));
    console.log(JSON.stringify({ chapters: defs.map((c) => [c.id, c.order, c.duration_s]), hole: window.StageV6Hook.hole }));
    """
    run = subprocess.run(["node", "-e", harness, str(STATIC)], capture_output=True, text=True, check=True)
    out = json.loads(run.stdout)
    assert out["chapters"] == [["intake", 10, 16], ["hook", 5, 10], ["voice", 20, 11], ["reading", 30, 16]]
    assert out["hole"][0] < out["hole"][1] <= 10
