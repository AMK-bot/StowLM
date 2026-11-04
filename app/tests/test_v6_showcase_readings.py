"""Prepared sentences as the adopted reader read them (experiments/mvp/showcase/reader/recordings.json), through app/v6_hero.json
``showcase_readings``: pinned by sha256, the adopted adapter only, counts from the rows, misreads and refusals kept in place.

The synthetic recordings below are written into tmp_path in the producer's shape (showcase/reader/record.py): none is a real reading.
The pinned real file is checked only against itself, never against numbers written here."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import v6
from experiments.mvp.pipeline.contract.schema import sha256_json

ADAPTER = v6._adoption()["adopted"]["adapter_sha256"]
CONTEXT = {"ship": "synthetic", "bays": [1, 2, 3, 4]}
RULE = {"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 4}}
OTHER = {"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 3}}
REFUSAL = {"schema_version": "stowlm.reader/2", "refusal": {"refused": True, "code": "unsupported", "reason": "x"}}


def row(sentence, status, reading, expected=RULE, context=CONTEXT, adapter=ADAPTER, kind="prepared_hero_wording"):
    return {"kind": kind, "source": "synthetic", "sentence": sentence, "expected": expected, "context_sha256": sha256_json(context),
            "reading": reading, "status": status, "label": "StowLM Reader · test label",
            "evidence": {"adapter_sha256": adapter, "masked": True, "wall_s": 2.0}}


def recording(rows, adapter=ADAPTER, misreads=None, counts=None):
    if counts is None:
        counts = {}
        for r in rows:
            counts.setdefault(r["kind"], {}).setdefault(r["status"], 0)
            counts[r["kind"]][r["status"]] += 1
    return {"schema_version": "stowlm.showcase-reader-recordings/1", "label": "synthetic", "adopted": {"name": "test-reader", "adapter_sha256": adapter},
            "counts": counts, "misreads": [r for r in rows if r["status"] == "misread"] if misreads is None else misreads, "recordings": rows}


ROWS = [row("Close four.", "exact", RULE), row("Skip four.", "misread", REFUSAL), row("Four shut.", "misread", OTHER),
        row("Other ship.", "exact", RULE, context={"ship": "other"}), row("Elsewhere wrong.", "misread", OTHER, expected=OTHER, kind="prepared_brief"),
        row("No rule here.", "no_stored_rule", RULE, expected=None, kind="copilot")]


@pytest.fixture
def pin(tmp_path, monkeypatch):
    def write(data, sha=None):
        path = tmp_path / "recordings.json"
        path.write_text(json.dumps(data))
        digest = sha or hashlib.sha256(path.read_bytes()).hexdigest()
        monkeypatch.setattr(v6, "_settings", lambda: {"showcase_readings": {"path": str(path), "sha256": digest}})
        return path
    return write


def test_counts_come_from_the_rows_and_misses_stay_in_place(pin):
    pin(recording(ROWS))
    data = v6.showcase_readings()
    assert data["label"] == "Recorded answer" and data["reader"] == "test-reader" and data["adapter_sha256"] == ADAPTER
    c = data["counts"]
    assert c["total"] == len(ROWS) and c["missed"] == sum(r["status"] == "misread" for r in ROWS) and c["as_expected"] == c["total"] - c["missed"] - c["no_stored_rule"]  # D296: a row with no stored rule isn't counted as expected
    assert c["refused"] == 1 and c["wrong_rule"] == 2
    view = v6.prepared_for(RULE, CONTEXT)
    assert [r["sentence"] for r in view["rows"]] == ["Close four.", "Skip four.", "Four shut."]  # this rule, this ship; misses kept, in file order
    assert view["rows"][1]["refused"] and view["rows"][1]["refusal_code"] == "unsupported"
    assert view["rows"][2]["read"] == "Nothing loads into bay 03." and not view["rows"][2]["as_expected"]
    assert [r["sentence"] for r in view["elsewhere"]] == ["Elsewhere wrong."]  # the file's other misses, named beside them
    assert {k: view["here"][k] for k in ("total", "as_expected", "missed")} == {"total": 3, "as_expected": 1, "missed": 2}
    assert view["here"]["typed"] == {"total": 3, "as_expected": 1} and view["here"]["radio"] == {"total": 0, "as_expected": 0}


@pytest.mark.parametrize("break_it", ["sha", "adapter", "row_adapter", "status", "misreads", "counts", "schema"])
def test_anything_but_the_pinned_adopted_recording_is_a_gap(pin, break_it):
    rows = [dict(r) for r in ROWS]
    data = recording(rows)
    if break_it == "adapter":
        data = recording(rows, adapter="0" * 64)
    elif break_it == "row_adapter":
        data = recording([row("x", "exact", RULE, adapter="1" * 64)] + rows)
    elif break_it == "status":
        data = recording([row("x", "great", RULE)] + rows)
    elif break_it == "misreads":
        data = recording(rows, misreads=[])
    elif break_it == "counts":
        data = recording(rows, counts={"prepared_hero_wording": {"exact": 99}})
    elif break_it == "schema":
        data = {**data, "schema_version": "something-else/1"}
    pin(data, sha="f" * 64 if break_it == "sha" else None)
    with pytest.raises(ValueError):
        v6.showcase_readings()
    assert v6.optional(lambda: v6.prepared_for(RULE, CONTEXT)) is None  # the panel that cites it is a clean gap


def test_a_rule_with_no_prepared_sentence_on_this_ship_is_a_gap(pin):
    pin(recording(ROWS))
    with pytest.raises(LookupError):
        v6.prepared_for({"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 1}}, CONTEXT)


def test_the_pinned_file_is_the_one_the_config_names_and_reads_clean():
    """The real recordings: whatever the pin says, the counts equal the file's own rows and its misreads list."""
    pin = v6.hero_config()["showcase_readings"]
    path = v6.ROOT / pin["path"]
    raw = json.loads(path.read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == pin["sha256"]
    data = v6.showcase_readings()
    assert data["reader"] == raw["adopted"]["name"] and data["adapter_sha256"] == raw["adopted"]["adapter_sha256"]
    assert data["counts"]["total"] == len(raw["recordings"]) and data["counts"]["missed"] == len(raw["misreads"])
    assert data["sha256"] == pin["sha256"] and data["source"] == pin["path"]


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@needs_node
def test_the_panel_names_every_miss_as_what_it_is(pin):
    pin(recording(ROWS))
    view = v6.prepared_for(RULE, CONTEXT)
    script = Path(v6.ROOT / "app/static/stage-v6reading.js").read_text()
    code = ("const defs=[];global.window={StageV6:{chapter:(d)=>defs.push(d)},StageStory:{},V6FX:{}};" + script +
            ";const esc=(s)=>String(s).replace(/[&<>\"]/g,'');process.stdout.write(defs[0].prepared(" + json.dumps(view) + ",'Close four.',esc));")
    html = subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True).stdout
    assert "Recorded answer" in html and "test-reader" in html and ADAPTER[:12] in html
    assert "refused as unsupported" in html and "misread as “Nothing loads into bay 03.”" in html and "Elsewhere wrong." in html
    assert "typed wordings read to this rule" not in html  # S3: the 8 wordings' counts replace it, and only with their data
    assert f"all {view['counts']['total']} prepared sentences" in html
    blank = subprocess.run(["node", "-e", code.replace(json.dumps(view), "null")], check=True, capture_output=True, text=True).stdout
    assert blank == ""


@needs_node
def test_the_panel_says_the_eight_wordings_counts_from_data():
    """S3: shot 5's counts line, from the radio selection's own counts; without them the line goes."""
    script = Path(v6.ROOT / "app/static/stage-v6reading.js").read_text()
    w = {"total": 8, "written": 7, "template": True, "typed_exact": 5, "radio_exact": 3}
    code = ("const defs=[];global.window={StageV6:{chapter:(d)=>defs.push(d)},StageStory:{},V6FX:{}};" + script +
            ";process.stdout.write(JSON.stringify([defs[0].wordingsLine(" + json.dumps(w) + "), defs[0].wordingsLine(null)]));")
    line, none = json.loads(subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True).stdout)
    assert line == "Of 8 wordings (7 written + the template): 5 typed and 3 radio transcripts read exactly; the rest refused · selected wording; counts, not a rate"
    assert none == ""
    real = v6.wording_counts()
    assert real == {**real, "total": int(v6.figures()["figures"]["radio_wordings"]["value"]), "written": int(v6.figures()["figures"]["radio_written"]["value"])}


@needs_node
def test_the_recorded_time_says_it_is_a_warm_median():
    """M3: a recorded reading's time is the timing run's warm median, network blocked; a live read keeps its own "Read in"."""
    script = Path(v6.ROOT / "app/static/stage-v6reading.js").read_text()
    d = v6.reading()
    code = ("const defs=[];global.window={StageV6:{chapter:(d)=>defs.push(d)},StageStory:{},V6FX:{}};" + script +
            ";const c=defs[0], d=" + json.dumps(d) + ";process.stdout.write(JSON.stringify([c.secondsFact(d), c.secondsFact(c.liveData(d, {seconds: 5.09}))]));")
    recorded, live = json.loads(subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True).stdout)
    said = v6.figures()["figures"]["reader_seconds_said"]["value"]
    assert recorded == ["Reading time", f"About {said} s a sentence (warm median, network blocked for the process)"]
    assert live == ["Read in", "5.1 s"]
