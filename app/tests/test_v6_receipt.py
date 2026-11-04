"""Demo v6: the reader bars' display rounding, the rebuilt hero package's receipt card and its confirmation wording, and the
recording's binding. Every expectation about the package is read from its own files; no hero fact is typed here."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import v6, v6_package
from app.v6_fixtures import Withheld

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app/static"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


# ---------------------------------------------------------------- reader bars
@pytest.mark.parametrize("value, shown, exact", [(46.875, "47 %", "exact: 46.875 %"), (92.1875, "92 %", "exact: 92.1875 %"),
                                                 (0.5, "1 %", "exact: 0.5 %"), (90.0, "90 %", "exact: 90 %"), (12, "12 %", "exact: 12 %")])
def test_bar_labels_round_half_up_and_keep_the_exact_value(value, shown, exact):
    assert v6.shown_pct(value) == shown and v6.exact_pct(value) == exact


def test_reader_bars_carry_both_and_keep_the_width_exact():
    bars = v6._reader_bars({"reader_exact_pct": 92.1875, "untrained_exact_pct": 46.875})
    assert bars["with"] == 92.1875 and bars["shown"] == {"without": "47 %", "with": "92 %"}
    assert bars["exact"]["with"] == "exact: 92.1875 %"


@needs_node
def test_the_page_shows_the_rounded_value_with_the_exact_one_in_its_title():
    harness = """
    const fs = require('fs'), defs = [];
    global.window = { StageV6: { chapter: (c) => defs.push(c) }, V6FX: {}, StageStory: {} };
    eval(fs.readFileSync(process.argv[1] + '/stage-v6reading.js', 'utf8'));
    const esc = (s) => String(s).replace(/"/g, '&quot;');
    const b = JSON.parse(process.argv[2]);
    console.log(JSON.stringify([defs[0].barLabel(b, 'without', esc), defs[0].barLabel(b, 'with', esc)]));
    """
    bars = v6._reader_bars({"reader_exact_pct": 92.1875, "untrained_exact_pct": 46.875})
    out = json.loads(subprocess.run(["node", "-e", harness, str(STATIC), json.dumps(bars)], capture_output=True, text=True, check=True).stdout)
    assert out == ['<em title="exact: 46.875 %">47 %</em>', '<em title="exact: 92.1875 %">92 %</em>']


# ---------------------------------------------------------------- confirmation wording
@pytest.mark.parametrize("source", ["user_authorized_batch:package_regeneration", "batch:regen", "BATCH-run", "auto_batch"])
def test_a_batch_source_is_a_demo_fixture_by_its_pattern(source):
    assert v6_package.confirmation_label(source) == v6_package.BATCH_LABEL == "user-authorised batch regeneration, no planner present (demo fixture)"


def test_the_batch_label_is_the_package_s_own_words():
    """pipeline/package/confirmation.py (read, never imported: its package runs code): a v4 receipt carries this source, a v3 one maps to it."""
    import ast
    tree = ast.parse((v6.ROOT / "experiments/mvp/pipeline/package/confirmation.py").read_text())
    words = next(node.value.value for node in tree.body if isinstance(node, ast.Assign) and node.targets[0].id == "BATCH_CONFIRMATION_SOURCE")
    assert v6_package.BATCH_LABEL == words
    assert v6_package.confirmation_label(words) == words  # v4: the package's own sentence, unchanged
    assert v6_package.confirmation_label("user_authorized_batch:package_regeneration") == words  # v3's raw source, mapped to it


@pytest.mark.parametrize("source", ["user_authorized_batch:package_regeneration", "user-authorised batch regeneration, no planner present (demo fixture)"])
def test_shot_10_s_line_reads_the_package_s_sentence_for_v3_and_v4(source):
    receipt = {"steps": [{"stage": "confirmation", "notes": json.dumps({"confirmation_source": source})}], "pipeline_sha256": "c" * 64}
    assert v6_package.package_reader(receipt)["confirmed_by"] == "user-authorised batch regeneration, no planner present (demo fixture)"


@pytest.mark.parametrize("source", ["batchelor", "", None, "user_authorized"])
def test_no_pattern_no_label(source):
    assert v6_package.confirmation_label(source) is None


def test_the_planners_own_press_is_the_planner():
    assert v6_package.confirmation_label(v6.CONFIRM_SOURCE) == "the planner, on screen"


def test_the_note_never_shows_a_raw_source():
    text = "Confirmed instruction: Close it. Confirmed by: user_authorized_batch:package_regeneration.\nNext line."
    assert v6_package.note_as_shown(text) == f"Confirmed instruction: Close it. Confirmed by: {v6_package.BATCH_LABEL}.\nNext line."
    v4 = f"Confirmed instruction: Close it. Confirmed by: {v6_package.BATCH_LABEL}.\nNext line."
    assert v6_package.note_as_shown(v4) == v4  # v4's note already says it in words
    assert v6_package.note_as_shown("Confirmed instruction: Close it. Confirmed by: someone_else.\n") == "Confirmed instruction: Close it.\n"
    assert v6_package.note_as_shown("No such line.") == "No such line."


# ---------------------------------------------------------------- the rebuilt hero package
@pytest.fixture
def hero_package(monkeypatch, tmp_path):
    copy = tmp_path / "package"
    shutil.copytree(v6_package.package_dir(), copy)
    hero = tmp_path / "hero.json"
    hero.write_text(json.dumps({**v6.hero_config(), "package_dir": str(copy)}))
    monkeypatch.setattr(v6_package, "HERO_FILE", hero)
    monkeypatch.setattr(v6_package, "verification_state", lambda directory: {"status": "passed", "reason": None, "package_sha256": "p", "checked_on": None})
    return copy


def receipt_of(directory: Path) -> dict:
    return json.loads((directory / "receipt.json").read_text())


def test_the_hero_package_is_for_the_hero_rule(hero_package):
    receipt = receipt_of(hero_package)
    assert v6_package._package_rules(receipt) == [v6_package._rule_key(v6.hero()["instruction"]["rule"])]
    v6_package.require_hero_plan(hero_package)


def test_a_package_under_another_rule_is_withheld(hero_package):
    receipt = receipt_of(hero_package)
    other = [{**row, "reading": {**row["reading"], "constraint": {**row["reading"]["constraint"], "bay": row["reading"]["constraint"].get("bay", 0) + 2}}}
             for row in receipt["rules"]]
    (hero_package / "receipt.json").write_text(json.dumps({**receipt, "rules": other}))
    with pytest.raises(Withheld, match="another rule"):
        v6_package.require_hero_plan(hero_package)


def test_the_receipt_card_reads_the_package_receipt(hero_package):
    receipt = receipt_of(hero_package)
    parser = receipt["rules"][0]["parser"]
    shown = v6_package.view()["receipt"]["reader"]
    assert shown == {"name": parser["name"], "model": parser["model"], "adapter": parser["adapter_sha256"][:12],
                     "pipeline": receipt["pipeline_sha256"][:12], "confirmed_by": v6_package.BATCH_LABEL}


def test_no_batch_label_reaches_the_chapter(hero_package):
    data = json.dumps(v6_package.view())
    assert "user_authorized_batch" not in data and "package_regeneration" not in data


def test_a_renamed_receipt_field_is_a_gap_not_a_crash():
    assert v6_package.package_reader({}) is None
    assert v6_package.package_reader({"pipeline_sha256": "a" * 64})["pipeline"] == "a" * 12


@needs_node
def test_the_card_line_shows_only_mapped_words():
    harness = """
    const fs = require('fs'), defs = [];
    global.window = { StageV6: { chapter: (c) => defs.push(c) } };
    eval(fs.readFileSync(process.argv[1] + '/stage-v6package.js', 'utf8'));
    const x = JSON.parse(process.argv[2]);
    console.log(JSON.stringify([defs[0].readerHtml(x, String), defs[0].readerHtml(null, String)]));
    """
    x = {"name": "r", "adapter": "a" * 12, "pipeline": "b" * 12, "confirmed_by": v6_package.BATCH_LABEL}
    line, none = json.loads(subprocess.run(["node", "-e", harness, str(STATIC), json.dumps(x)], capture_output=True, text=True, check=True).stdout)
    assert none == "" and "reader: r" in line and f"adapter {'a' * 12}" in line and f"built on pipeline {'b' * 12}" in line
    assert "Confirmed by: user-authorised batch regeneration, no planner present (demo fixture)" in line


# ---------------------------------------------------------------- the recording
def test_record_refuses_a_package_that_isnt_the_heros(hero_package, monkeypatch):
    receipt = receipt_of(hero_package)
    (hero_package / "receipt.json").write_text(json.dumps({**receipt, "plan_sha256": "0" * 64}))
    monkeypatch.setattr(v6_package, "start_verify", lambda fields: pytest.fail("no run for another plan"))
    with pytest.raises(Withheld):
        v6_package.record()


def test_record_binds_the_index_and_every_file(hero_package, monkeypatch, tmp_path):
    runs = {False: {"id": "v", "status": "done", "passed": True, "elapsed_s": 1.0, "checks": [], "package_sha256": v6_package.package_digest(hero_package)},
            True: {"id": "t", "status": "done", "passed": False, "elapsed_s": 1.0, "checks": []}}
    monkeypatch.setattr(v6_package, "start_verify", lambda fields: runs[fields["tamper"]])
    monkeypatch.setattr(v6_package, "job", lambda job_id: next(r for r in runs.values() if r["id"] == job_id))
    monkeypatch.setattr(v6_package, "RECORDED", tmp_path / "recorded.json")
    v6_package.record()
    saved = json.loads((tmp_path / "recorded.json").read_text())
    assert saved["package"] == v6_package.package_binding(hero_package) and saved["package_pipeline_sha256"] == receipt_of(hero_package)["pipeline_sha256"]
    assert saved["receipt_sha256"] == v6_package._sha256(hero_package / "receipt.json")
    assert v6_package._recording(v6_package.package_binding(hero_package)) == saved


def test_every_manifest_hash_on_screen_comes_from_data(hero_package, monkeypatch):
    """M9: shot 10's package manifest is the shown package's receipt; 3.1's is the held-out run's story figures. A re-stamped package shows its own."""
    receipt = receipt_of(hero_package)
    assert v6._package_manifest_figures()[2] == {"package_manifest": receipt["pipeline_sha256"][:8] + "…"}
    held = json.loads(v6.HELD_OUT_FIGURES.read_text())["run"]["manifest_sha256"]["value"]
    assert v6._held_out_manifest()[2] == {"held_out_manifest": held[:8] + "…"}
    (hero_package / "receipt.json").write_text(json.dumps({**receipt, "pipeline_sha256": "d" * 64}))
    assert v6._package_manifest_figures()[2] == {"package_manifest": "d" * 8 + "…"}
    assert v6._package_confirmation()[2] == {"package_confirmed_by": v6_package.BATCH_LABEL}
    shots = (v6.ROOT / "app/static/stage-shots.js").read_text()
    for digest in (receipt["pipeline_sha256"], held):
        assert digest[:8] not in shots  # never typed in
