"""Shots 7 and 9 read the lattice's own facts files at runtime; film figures cite files that exist (third pass, items 1, 2, 4)."""
import hashlib
import json
from pathlib import Path

import pytest

from app import v6

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads(v6.HERO_FILE.read_text())
CHANGE = ROOT / CONFIG["change_facts"]
IMPOSSIBLE = ROOT / CONFIG["impossible_facts"]
pytestmark = pytest.mark.skipif(not (CHANGE.is_file() and IMPOSSIBLE.is_file()), reason="lattice facts not on disk")


def hero_with(tmp_path, monkeypatch, **paths):
    config = {**CONFIG, **{k: str(v) for k, v in paths.items()}}
    path = tmp_path / "hero.json"
    path.write_text(json.dumps(config))
    monkeypatch.setattr(v6, "HERO_FILE", path)


def reshaped(tmp_path, source: Path, change) -> Path:
    data = json.loads(source.read_text())
    change(data)
    out = tmp_path / source.name
    out.write_text(json.dumps(data))
    return out


def test_shot_seven_numbers_are_the_facts_file_s_own():
    raw, facts = json.loads(CHANGE.read_text()), v6.change_facts()
    assert facts["boxes"] == raw["boxes"]["count"] and facts["forced_ids"] == sorted(raw["pointwise_forced"]["container_ids"])
    assert facts["moved"] == raw["changed_final_positions"]["count"] and facts["rearranged"] == raw["changed_final_positions"]["additional_count"]
    assert (facts["lifts_before"], facts["lifts_after"]) == (raw["extra_lifts"]["baseline"], raw["extra_lifts"]["selected"])
    assert facts["frames"] == raw["native_frames"]["n_frames"] and facts["caveat"] == " ".join(raw["attribution"].split())  # the file's words, not ours
    assert facts["engine"] == raw["producer"]["engine_version"] and facts["computed_utc"] == raw["computed_utc"]
    served = v6.figures()["figures"]
    assert served["change_peak_heel"]["value"] == f"{raw['native_frames']['peak_abs_heel_deg']:.2f}"
    fill = json.loads((ROOT / "experiments/mvp/video/fill-v6.json").read_text())  # the film's fill and the data never give two numbers for one thing
    shared = {"hero_boxes": facts["boxes"], "hero_forced_boxes": facts["forced"], "hero_peak_heel": float(served["change_peak_heel"]["value"]),
              "hero_plan_date": served["change_plan_date"]["value"]}
    assert {k: fill[k] for k in shared if k in fill} == {k: v for k, v in shared.items() if k in fill}
    assert served["change_plan_date"]["source"] == CONFIG["change_facts"] and served["change_plan_date"]["sha256"] == hashlib.sha256(CHANGE.read_bytes()).hexdigest()


@pytest.mark.parametrize("change", [
    lambda d: d.update(schema_version="stowlm.something-else/1"),
    lambda d: d.update(instance_sha256="0" * 64),
    lambda d: d["sources"]["selected"].update(plan_sha256="0" * 64),
    lambda d: d["pointwise_forced"].update(count=d["pointwise_forced"]["count"] + 1),
    lambda d: d.pop("attribution"),
    lambda d: d["extra_lifts"].update(delta=0),
], ids=["shape", "another ship", "another plan", "counts", "no caveat", "lifts"])
def test_a_reshaped_or_foreign_facts_file_is_a_clean_gap(tmp_path, monkeypatch, change):
    hero_with(tmp_path, monkeypatch, change_facts=reshaped(tmp_path, CHANGE, change))
    out = v6.figures()
    assert "change" in out["gaps"] and not any(name.startswith("change_") for name in out["figures"])
    with pytest.raises((ValueError, KeyError)):
        v6.change_facts()


def test_an_absent_facts_file_is_a_clean_gap(tmp_path, monkeypatch):
    hero_with(tmp_path, monkeypatch, change_facts=tmp_path / "none.json", impossible_facts=tmp_path / "none.json")
    out = v6.figures()
    assert {"change", "impossible"} <= set(out["gaps"]) and not any(name.startswith(("change_", "impossible_")) for name in out["figures"])


def test_shot_nine_reads_the_stored_proved_refusal_whole():
    raw, facts = json.loads(IMPOSSIBLE.read_text()), v6.impossible_facts()
    whole = raw["arithmetic_proof"]["all_requested_rules"]
    assert (facts["needed"], facts["allowed"], facts["equation"]) == (whole["cells_needed"], whole["cells_allowed"], whole["equation"])
    assert facts["sentences"] == raw["sentences"] and facts["checker_called"] is raw["checker_called"] is False
    served = v6.figures()["figures"]
    assert (served["impossible_needed"]["value"], served["impossible_left"]["value"]) == (str(whole["cells_needed"]), str(whole["cells_allowed"]))
    if "shortfall_cells" in whole:  # the file's own shortfall, the gap between its counts (shot 9's "47 short", D292)
        assert served["impossible_short"]["value"] == str(whole["shortfall_cells"]) == str(whole["cells_needed"] - whole["cells_allowed"])


@pytest.mark.parametrize("change", [
    lambda d: d.update(status="unproved"),
    lambda d: d["arithmetic_proof"]["all_requested_rules"]["public_proof"].update(proven_impossible=False),
    lambda d: d["arithmetic_proof"]["all_requested_rules"].update(cells_allowed=999),
    lambda d: d.update(instance_sha256="0" * 64),
], ids=["not stored proved", "not proved", "fits", "another ship"])
def test_a_refusal_that_isn_t_proved_here_is_a_clean_gap(tmp_path, monkeypatch, change):
    hero_with(tmp_path, monkeypatch, impossible_facts=reshaped(tmp_path, IMPOSSIBLE, change))
    out = v6.figures()
    assert "impossible" in out["gaps"] and "impossible_needed" not in out["figures"]


def test_the_copilot_shows_the_refusal_as_stored_and_proved_never_recorded():
    js = (ROOT / "app/static/stage-v6copilot.js").read_text()
    assert "Stored refusal, computed on ${esc(p.date)}" in js and "proved by arithmetic before planning" in js
    assert "${d.proved && c === d.main && !o ? provedHtml(d.proved, esc) : ''}" in js  # only under the recorded "No", never beside an offer
    assert '"proved": attempt(impossible_facts)' in (ROOT / "app/v6.py").read_text()


def test_every_film_figure_cites_a_file_that_exists_and_matches_its_sha256():
    rows = json.loads(v6.FILM_FIGURES.read_text())["figures"]
    for name, row in rows.items():
        assert (ROOT / row["named_source"].split(",")[0]).is_file(), name  # a figure with no real source has no place in the film
        if row.get("sha256"):
            assert hashlib.sha256((ROOT / row["named_source"].split(",")[0]).read_bytes()).hexdigest() == row["sha256"], name
    served = v6.figures()["figures"]
    assert set(rows) <= set(served)  # none dropped for a missing source
    for name, row in served.items():
        path = ROOT / row["source"]
        assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"], name


def test_every_optional_app_input_the_freeze_sees_exists():
    for path in (v6.FILL, *v6.INTAKE_RESULTS, ROOT / CONFIG["change_facts"], ROOT / CONFIG["impossible_facts"], ROOT / CONFIG["xray_trace"]):
        assert Path(path).is_file(), path
