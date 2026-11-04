"""logic14 M12 and p22: playback-aware words, one clock for the verify, the shot 5 rows adding up, the copilot take's own request.

    uv run --offline --no-sync pytest app/tests/test_v6_logic14.py -q
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import v6

STATIC = Path(__file__).resolve().parents[1] / "static"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run(name: str, expr: str, play: bool):
    code = ("const fs=require('fs'),vm=require('vm');const w={" + ("__play:{}," if play else "") + "StageV6:{chapter(d){w.C=d;}},StageStory:{},V6FX:{}};"
            f"vm.runInNewContext(fs.readFileSync({json.dumps(str(STATIC / name))},'utf8'),{{window:w}});"
            f"process.stdout.write(JSON.stringify({expr}));")
    return json.loads(subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True).stdout)


@needs_node
def test_a_replayed_verify_never_says_it_ran_here():
    assert run("stage-v6package.js", "w.C.runLabel({live: true})", True) == "Recorded verify from the app, replayed at its recorded pace"
    assert run("stage-v6package.js", "w.C.runLabel({live: true})", False) == "Live verify, ran here"
    source = (STATIC / "stage-v6package.js").read_text()
    assert "'Play the recorded verify'" in source and "'Play the recorded tamper check'" in source


@needs_node
def test_the_verify_banner_and_rows_read_one_clock():
    m = v6.package()["verification"]
    for kind in ("verify", "tamper"):
        secs = run("stage-v6package.js", f"w.C.runSecs({json.dumps(m[kind])})", False)
        assert secs == max(c["at_s"] for c in m[kind]["checks"])
    assert run("stage-v6package.js", "w.C.runSecs({wall_s: 3, checks: []})", False) == 3


@needs_node
def test_shot_5_names_the_readings_its_list_leaves_out():
    reading = v6.reading()
    w, rows = reading["wordings"], reading["prepared"]["rows"]
    line = run("stage-v6reading.js", f"w.C.wordingsLine({json.dumps(w)}, {json.dumps(rows)})", False)
    typed = w["typed_exact"] - sum(1 for r in rows if not r["heard"] and r["as_expected"] and not r["refused"])
    radio = w["radio_exact"] - sum(1 for r in rows if r["heard"] and r["as_expected"] and not r["refused"])
    if typed or radio:
        assert "not shown)" in line and (f"{radio} radio reading" in line if radio else True)
    assert run("stage-v6reading.js", "w.C.notShown({typed_exact: 2, radio_exact: 1}, [{heard: false, as_expected: true}, {heard: false, as_expected: true}, {heard: true, as_expected: true}])", False) == ""


def test_the_copilot_take_names_its_own_request():
    k = v6.copilot_delivered()
    assert k["request"] and all("tiers" in line for line in k["request"])
    assert k["request_equation"] and k["request_equation"] != v6.copilot()["proved"]["equation"]
    assert "${req}" in (STATIC / "stage-v6copilot.js").read_text()


@needs_node
def test_playback_branches_for_the_reader_and_break_it():
    label = v6.reading()["reader"]["label"]
    assert run("stage-v6reading.js", f"w.C.where({json.dumps(label)})", True) == label.replace("on this laptop", "recorded on the presenter's laptop")
    assert run("stage-v6reading.js", f"w.C.where({json.dumps(label)})", False) == label
    assert "Recorded verdict, replayed at its recorded pace" in (STATIC / "stage-v6breakit.js").read_text()


def test_the_replan_shows_no_lift_gap():
    source = (STATIC / "stage-v6replan.js").read_text()
    assert "liftGapLine" not in source and "<i>→</i><b>${count(lifts)}" not in source


@needs_node
def test_a_replayed_verify_never_earns_rechecked_now():
    d = {"label": "Stored plan, checked", "fresh": "Stored plan, rechecked now", "package_sha256": "a"}
    job = {"status": "done", "passed": True, "package_sha256": "a", "checks": [{"name": "files", "state": "passed"}]}
    assert run("stage-v6package.js", f"w.C.outcome({json.dumps(d)}, {json.dumps(job)}).label", True) == d["label"]
    assert run("stage-v6package.js", f"w.C.outcome({json.dumps(d)}, {json.dumps(job)}).label", False) == d["fresh"]
