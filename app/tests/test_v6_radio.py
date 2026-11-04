"""The hero wording (D268 as amended, D270 H5) and its radio take, shots 4 and 5.

Every row below is synthetic, written into tmp_path in the producers' shapes (radio/read_transcripts.py, reader_final/hero_candidates.py):
nothing here is a real reading or a real transcript."""
from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

import pytest

from app import v6
from app.tests.test_v6_gates import answer, ask, demo, v6_server  # noqa: F401 - demo, v6_server are fixtures

RULE = {"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 4}}
REFUSED = json.dumps({"schema_version": "stowlm.reader/2", "refusal": {"refused": True, "code": "unsupported", "reason": "x"}})
OTHER_RULE = json.dumps({"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 5}})
FALLBACK = "Template wording T."


def radio_row(sentence, transcript, binds, condition="radio", wav=None):
    return {"id": f"{sentence}-{condition}", "sentence": sentence, "expected_rule": RULE, "condition": condition, "transcript": transcript,
            "binds": binds, "verdict": "binds_exactly" if binds else "misread", "reading": json.dumps(RULE) if binds else REFUSED, **({"wav_path": wav} if wav else {})}


def typed(sentence, binds, case=None, evidence=None, reading=None):
    return {"sentence": sentence, "binds": binds, "case": case, "evidence": evidence, "reading": reading or (json.dumps(RULE) if binds else REFUSED)}


WORDINGS = [("Wording A.", "wording a, they four", False), ("Wording B.", "Wording B, Bay 04", True), ("Wording C.", "Wording C bay 4", True),
            (FALLBACK, "template bay 4", True)]


def rows(**over):
    out = []
    for sentence, heard, binds in WORDINGS:
        out += [radio_row(sentence, heard, True, "clean"), radio_row(sentence, heard, over.get(sentence, binds))]
    return out


# ------------------------------------------------------------------ the selection rule, pure
def test_the_first_wording_read_exactly_both_typed_and_over_the_line_wins():
    pick = v6.select_hero_wording(rows(), [typed("Wording A.", True), typed("Wording B.", False), typed("Wording C.", True), typed(FALLBACK, True)], FALLBACK)
    assert pick["sentence"] == "Wording C." and pick["reason"] == "first_exact" and pick["index"] == 2  # A misread over radio, B misread typed
    assert pick["typed_exact"] == 3 and pick["radio_exact"] == 3 and len(pick["wordings"]) == 4  # misses counted, clean takes not
    assert pick["written"] == 3 and pick["misses_refused"] is True


def test_a_miss_that_became_another_rule_is_not_called_refused():
    out = rows()
    out[1] = {**out[1], "reading": OTHER_RULE}
    assert v6.select_hero_wording(out, [typed(s, True) for s, _, _ in WORDINGS], FALLBACK)["misses_refused"] is False
    assert v6.is_refusal(REFUSED) and v6.is_refusal(json.loads(REFUSED)) and not v6.is_refusal(OTHER_RULE) and not v6.is_refusal(None)


def test_with_none_read_exactly_both_ways_the_template_is_the_hero():
    pick = v6.select_hero_wording(rows(), [typed(s, False) for s, _, _ in WORDINGS], FALLBACK)
    assert pick["sentence"] == FALLBACK and pick["reason"] == "fallback" and pick["radio"]["sentence"] == FALLBACK and pick["typed_exact"] == 0


def test_a_wording_with_no_typed_reading_cannot_be_chosen_and_the_typed_count_waits():
    pick = v6.select_hero_wording(rows(), [typed("Wording C.", True)], FALLBACK)
    assert pick["sentence"] == "Wording C." and pick["typed_exact"] is None


def test_the_hero_case_beats_another_reading_of_the_same_sentence():
    pick = v6.select_hero_wording(rows(), [typed("Wording B.", False, "backup"), typed("Wording B.", True, "hero")], FALLBACK, "hero")
    assert pick["sentence"] == "Wording B."


def test_with_no_radio_rows_the_template_stands_without_a_take():
    pick = v6.select_hero_wording([], [], FALLBACK)
    assert pick == {**pick, "sentence": FALLBACK, "radio": None, "index": None, "typed_exact": None}


@pytest.mark.parametrize("heard, kept", [("Nothing in Bay 4 today", True), ("bay 04 closed", True), ("bay four", True), ("Bayfour", False),
                                         ("504 is closed", False), ("they day four", False), ("bay 14", False)])
def test_the_bay_number_survives_only_as_bay_then_the_number(heard, kept):
    assert v6.bay_heard(heard, 4) is kept


@pytest.mark.parametrize("data", [{}, {"rows": []}, {"rows": [{"sentence": "x"}]}, {"rows": [{**radio_row("x", "y", True), "binds": "yes"}]}, []])
def test_a_reshaped_readings_file_raises(data):
    with pytest.raises(ValueError):
        v6._radio_rows(data)


def test_typed_rows_read_both_producer_shapes():
    got = v6._typed_rows({"kept": [{"sentence": "a", "binds": True}], "misread": [{"sentence": "b", "binds": False}],
                          "readings": [{"sentence": "c", "correct": True, "case": "hero"}, {"sentence": "d"}]})
    assert [(r["sentence"], r["binds"]) for r in got] == [("a", True), ("b", False), ("c", True)]


# ------------------------------------------------------------------ the files, wired
def write_wav(path: Path, seconds: float, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1), out.setsampwidth(2), out.setframerate(rate)
        out.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 9))) for i in range(int(seconds * rate))))


@pytest.fixture
def landed(tmp_path, monkeypatch):
    folder = tmp_path / "radio"
    (folder / "audio").mkdir(parents=True)
    out = rows()
    for row in out:
        if row["condition"] == "radio":
            row["wav_path"] = f"audio/{row['sentence'][:9]}-radio.wav"
            write_wav(folder / row["wav_path"], 1.5)
            (folder / row["wav_path"]).with_name(Path(row["wav_path"]).stem + ".audio.json").write_text(json.dumps({"snr_db": 9.0, "bandpass_hz": [1, 2]}))
    (folder / "readings.json").write_text(json.dumps({"schema_version": "stowlm.radio-readings/1", "rows": out}))
    (folder / "manifest.json").write_text(json.dumps({"clips": [{k: v for k, v in r.items() if k not in ("binds", "verdict")} for r in out]}))
    (tmp_path / "typed.json").write_text(json.dumps({"kept": [{"sentence": s, "binds": True} for s, _, _ in WORDINGS[1:]],
                                                     "misread": [{"sentence": WORDINGS[0][0], "binds": False, "reading": REFUSED}]}))
    monkeypatch.setattr(v6, "ROOT", tmp_path)  # figures name their sources relative to the repo
    radio = {"readings": "radio/readings.json", "clips": "radio/manifest.json", "typed": ["absent.json", "typed.json"],
             "fallback_sentence": FALLBACK, "audio": None, "audio_label": None}
    settings = {"hero_slot": "hero", "radio": radio, "stand_ins": {"radio_wording": "Another rule's wording."}}
    monkeypatch.setattr(v6, "_settings", lambda: settings)
    monkeypatch.setattr(v6, "hero_reading", lambda: {"sentence": "Wording B.", "answer": RULE})
    return settings, folder


def test_the_landed_files_choose_the_hero_and_its_take_plays(landed):
    settings, folder = landed
    got = v6.voice()
    assert got["sentence"] == "Wording B." and got["transcript"] == "Wording B, Bay 04" and got["role"] == "hero" and got["note"] is None
    assert got["label"] == "Stock text-to-speech voice · simulated radio: band-limited, 9 dB noise" and got["fixed"] is None
    assert all("fix" not in w for w in got["words"]) and got["peaks"] and got["seconds"] == 1.5  # nothing staged to fix
    assert [line["chosen"] for line in got["lines"]] == [False, True, False, False] and [line["bay_heard"] for line in got["lines"]] == [False, True, True, True]
    assert v6.radio_wav().name.startswith("Wording B")


def test_its_figures_come_from_the_files(landed):
    served = v6.figures()["figures"]
    names = ("radio_selected", "radio_wordings", "radio_written", "radio_bay", "radio_lost_bay", "radio_snr_db")
    assert {k: served[k]["value"] for k in names} == {"radio_selected": "1", "radio_wordings": "4", "radio_written": "3", "radio_bay": "4", "radio_lost_bay": "1", "radio_snr_db": "9"}
    assert served["radio_selected"]["source"].endswith("readings.json") and "4 synthetic wordings" in served["radio_selected"]["scope"]
    assert not {"radio_typed_exact", "radio_radio_exact"} & set(served)  # shot 5's typed/radio line left the script (D296)


def test_shot_five_drops_its_line_when_a_miss_was_a_wrong_rule(landed):
    settings, folder = landed
    data = json.loads((folder / "readings.json").read_text())
    next(row for row in data["rows"] if row["condition"] == "radio" and not row["binds"])["reading"] = OTHER_RULE
    (folder / "readings.json").write_text(json.dumps(data))
    assert "radio_selected" not in v6.figures()["figures"] and "radio_read" in v6.figures()["gaps"]  # "exact, else refused" would be false


def test_before_the_readings_land_the_line_counts_come_from_the_manifest_and_the_reader_counts_wait(landed):
    settings, folder = landed
    (folder / "readings.json").unlink()
    served, gaps = v6.figures()["figures"], v6.figures()["gaps"]
    assert served["radio_wordings"]["value"] == "4" and served["radio_lost_bay"]["source"].endswith("manifest.json")
    assert not {"radio_selected", "radio_snr_db"} & set(served) and {"radio_read", "radio_channel"} <= set(gaps)
    with pytest.raises(LookupError):
        v6.hero_wording()


def test_a_wording_of_another_rule_plays_as_a_separate_clip(landed):
    settings, folder = landed
    v6.hero_reading = lambda: {"sentence": "the stored instruction", "answer": RULE}  # restored by monkeypatch's own undo
    got = v6.voice()
    assert got["role"] == "illustrative" and got["note"] == "Another rule's wording."


def test_your_own_recording_replaces_the_take_and_its_peaks(landed, tmp_path):
    settings, folder = landed
    write_wav(tmp_path / "mine.wav", 2.0, 24000)
    settings["radio"] = {**settings["radio"], "audio": "mine.wav", "audio_label": "My voice · simulated radio"}
    got = v6.voice()
    assert got["seconds"] == 2.0 and got["label"] == "My voice · simulated radio" and got["own_recording"] is True
    assert "radio_snr_db" not in v6.figures()["figures"]  # no channel record for a recording of your own: that segment drops
    write_wav(tmp_path / "mine.wav", v6.RADIO_MAX_S + 1, 8000)
    with pytest.raises(ValueError):
        v6.radio_wav()


def test_a_take_whose_bytes_changed_is_refused(landed):
    settings, folder = landed
    data = json.loads((folder / "readings.json").read_text())
    for row in data["rows"]:
        row["wav_sha256"] = "0" * 64
    (folder / "readings.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        v6.radio_wav()


# ------------------------------------------------------------------ D309: the public copy withholds the radio audio
@pytest.fixture
def withheld(tmp_path, monkeypatch):
    """The configured readings, copied to tmp_path with no audio/ beside them (the real files stay where they are), and no separate clip."""
    settings = v6._settings()
    real = v6.ROOT / settings["radio"]["readings"]
    if not real.is_file():
        pytest.skip("this copy has no radio readings")  # never in the dev tree; the public copy ships readings.json without audio/
    (tmp_path / "radio").mkdir()
    copy = tmp_path / "radio" / real.name
    copy.write_bytes(real.read_bytes())
    moved = {**settings, "radio": {**settings["radio"], "readings": str(copy), "audio": None}}
    monkeypatch.setattr(v6, "_settings", lambda: moved)
    monkeypatch.setattr(v6, "VOICE", tmp_path / "v5")
    pick = v6.hero_wording()
    assert pick["radio"] and not (copy.parent / pick["radio"]["wav_path"]).exists()
    return pick


def test_without_the_audio_the_hero_radio_chapter_plays_its_transcript_and_the_note(withheld):
    block = v6.feature(v6.voice)
    assert block["available"] is True and block["sentence"] == withheld["sentence"] and block["lines"]  # the hero wording, not the separate clip
    assert block["transcript"] == withheld["radio"]["transcript"] and block["words"]
    assert block["peaks"] == [] and block["audio"] is None and block["audio_note"] == "audio not included in this copy" and block["seconds"] is None
    assert block["label"] == "Stock text-to-speech voice · simulated radio"  # the line's own record went with the audio: no dB figure
    with pytest.raises(FileNotFoundError):
        v6.radio_wav()


def test_without_the_audio_the_routes_stay_clean(withheld, v6_server):
    voice = answer(ask(v6_server, "GET", "/api/v6/voice"))
    assert voice["available"] is True and voice["audio"] is None and voice["audio_note"] == "audio not included in this copy"
    response = ask(v6_server, "GET", "/api/v6/asset/radio.wav")
    assert response.status == 404 and answer(response) == {"error": "audio not included in this copy"}
    assert answer(ask(v6_server, "GET", "/api/v6/state"))["features"]["voice"]["available"] is True


def test_without_the_audio_shot_four_still_plays_its_note(withheld):
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    static = Path(__file__).resolve().parents[1] / "static"
    from app.tests.test_stage_director import node as director
    assert director("R6.shots.find((s) => s.id === 4).v6.chapter") == "voice"  # the film's shot 4 plays this chapter
    code = """
global.window = globalThis;
const el = () => new Proxy({ html: '' }, { get: (t, k) => k in t ? t[k] : k === 'querySelector' ? () => el() : k === 'querySelectorAll' ? () => [] : k === 'addEventListener' ? () => {} : k === 'firstElementChild' ? el() : undefined, set: (t, k, v) => { t[k === 'innerHTML' ? 'html' : k] = v; return true; } });
window.V6FX = { clamp: (x) => Math.max(0, Math.min(1, x)), outCubic: (x) => x, hash: () => 0, fit: () => 1, canvas: () => null, put: () => {} };
let def; window.StageV6 = { chapter(d) { def = d; } };
require(process.argv[1]);
const host = el(), d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const view = def.mount(host, d, { esc: String, waitAt: () => false, release() {}, next() {} });
view.enter(); for (let t = 0; t <= 10; t += 0.5) view.seek(t, false);
console.log(JSON.stringify({ html: host.html }));
"""
    data = v6.feature(v6.voice)
    run = subprocess.run(["node", "-e", code, str(static / "stage-v6voice.js")], input=json.dumps(data), capture_output=True, text=True, check=True, timeout=30)
    html = json.loads(run.stdout)["html"]
    assert '<p class="v6w-noaudio">audio not included in this copy</p>' in html and "<canvas" not in html and 'data-a="play"' not in html
    assert all(f">{w['heard']}</span>" in html for w in data["words"])
