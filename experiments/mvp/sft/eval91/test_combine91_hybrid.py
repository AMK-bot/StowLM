"""Synthetic/temp-only tests: never open evaluation result directories."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


c = module("combine91")
p = module("polish91")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def synthetic(tmp_path):
    for n in range(30):
        iid = f"synthetic-{n:02d}"
        base = tmp_path / "stowlm" / "instances"
        write(base / f"{iid}.json", {"instance_id": iid, "rung": "tiny" if n < 15 else "small",
                                     "input": f"synthetic/{iid}", "pipeline_s": 2})
        own_pass = n in (0, 1, 2)
        folder = base / iid
        write(folder / "outcome.json", {"outcome": c.OWN if own_pass else "dead end"})
        write(folder / "report.json", {"count": {"weighted_nonproductive": {0: 10, 1: 12, 2: 11}.get(n)}})
        write(folder / "result.json", {"synthetic_result": iid, "own_pass": own_pass})
        write(tmp_path / "twostep" / f"{iid}.json", {"instance_id": iid, "passed": n in (0, 1, 3),
              "outcome": "dead end", "weighted_nonproductive": {0: 9, 1: 10, 3: 8}.get(n), "s": 3})
    return tmp_path


def stowlm_polish(out):
    source = c.load_run(out / "stowlm")
    write(out / "polish_summary.json", {"label": c.STOWLM_HYBRID})
    for iid, own in source.items():
        if own["outcome"] == c.OWN:
            after = {"synthetic-00": 8, "synthetic-01": 10, "synthetic-02": 10}[iid]
            write(out / "polish" / f"{iid}.json", {"instance_id": iid, "rung": own["rung"],
                  "stowlm_lifts": own["objective"], "polished_lifts": after, "own_report_passed": True,
                  "requested_polish_s": 15, "source_result_sha256": sha(Path(own["plan_from"])), "polish_s": 20})


def twostep_polish(out):
    originals = c.load_twostep(out / "twostep")
    target = out / "twostep-polish"
    guard = {"protocol": c.GUARD_PROTOCOL, "stage": "complete", "valid": True, "all_matches": True,
             "n_expected": 30, "n_matches": 30, "n_mismatches": 0, "n_passing": 3, "n_polished": 3,
             "requested_polish_s": 15, "fingerprint": "synthetic-run", "rows": {}, "polish_sha256": {},
             "reference_sha256": {i: row["_source_sha256"] for i, row in originals.items()}}
    for iid, own in originals.items():
        row = {"instance_id": iid, "status": "skipped_not_passing", "run_fingerprint": guard["fingerprint"]}
        if own["outcome"] == c.OWN:
            after = {"synthetic-00": 7, "synthetic-01": 10, "synthetic-03": 7}[iid]
            row.update(status="completed", before_moves=own["objective"], after_moves=after,
                       fingerprint=f"per-map-{iid}", requested_polish_s=15, own_report_passed=True,
                       final_report_passed=True, assignments_preserved=True, ballast_preserved=True, polish_s=21)
        path = write(target / f"{iid}.json", row)
        if own["outcome"] == c.OWN:
            guard["rows"][iid] = str(path)
            guard["polish_sha256"][iid] = sha(path)
    write(target / "GUARD.json", guard)
    return guard


def test_original_rows_and_chosen_unchanged_without_hybrids(tmp_path):
    out = synthetic(tmp_path)
    summary, text, _ = c.combine(out)
    assert list(summary["rows"]) == ["StowLM own plan (pass@1, greedy)", "two-step (same pipeline, same finisher budget)"]
    assert len(summary["chosen"]) == 3
    assert summary["rows"][list(summary["rows"])[0]][None]["own_mean_lifts"] == 11
    assert "hybrid_status" not in summary and "hybrid_paired" not in summary
    assert "hybrid" not in text


def test_complete_hybrids_adjacent_and_never_selected(tmp_path):
    out = synthetic(tmp_path)
    original, _, _ = c.combine(out)
    stowlm_polish(out)
    twostep_polish(out)
    summary, text, _ = c.combine(out)
    assert list(summary["rows"])[-2:] == [c.STOWLM_HYBRID, c.TWOSTEP_HYBRID]
    assert summary["chosen"] == original["chosen"]
    pair = summary["hybrid_paired"][None]
    assert (pair["n"], pair["stowlm_mean"], pair["twostep_mean"]) == (2, 9, 8.5)
    assert (pair["fewer"], pair["equal"], pair["more"]) == (0, 1, 1)
    assert pair["sign_test_discordant"] == 1 and pair["sign_test_exact"] == "1"
    assert "Exact two-sided sign tests exclude ties" in text


@pytest.mark.parametrize("field,value", [("valid", False), ("all_matches", False), ("stage", "rerun_complete"),
    ("n_matches", 29), ("n_mismatches", 1), ("n_expected", 29), ("n_polished", 2),
    ("requested_polish_s", 30), ("reference_sha256", {}), ("polish_sha256", {})])
def test_invalid_guard_omits_hybrid_but_keeps_own_rows(tmp_path, field, value):
    out = synthetic(tmp_path)
    original, _, _ = c.combine(out)
    guard = twostep_polish(out)
    guard[field] = value
    write(out / "twostep-polish" / "GUARD.json", guard)
    summary, text, _ = c.combine(out)
    assert c.TWOSTEP_HYBRID not in summary["rows"]
    assert summary["chosen"] == original["chosen"]
    assert summary["hybrid_status"][c.TWOSTEP_HYBRID]["status"] == "unavailable"
    assert "No hybrid result admitted" in text


def test_original_outcome_edit_invalidates_equality_guard(tmp_path):
    out = synthetic(tmp_path)
    twostep_polish(out)
    path = out / "twostep" / "synthetic-29.json"
    row = json.loads(path.read_text())
    row["outcome"] = "different failure category"
    write(path, row)
    summary, _, _ = c.combine(out)
    assert c.TWOSTEP_HYBRID not in summary["rows"]
    assert "not bound to all original" in summary["hybrid_status"][c.TWOSTEP_HYBRID]["reason"]


@pytest.mark.parametrize("field,value", [("status", "error"), ("run_fingerprint", "stale"),
    ("requested_polish_s", 30), ("own_report_passed", False), ("final_report_passed", False),
    ("assignments_preserved", False), ("ballast_preserved", False), ("after_moves", 99),
    ("after_moves", float("nan")), ("polish_s", float("inf")), ("polish_s", True)])
def test_polished_admission_flags_and_finite_numbers_required(tmp_path, field, value):
    out = synthetic(tmp_path)
    guard = twostep_polish(out)
    path = out / "twostep-polish" / "synthetic-00.json"
    row = json.loads(path.read_text())
    row[field] = value
    write(path, row)
    guard["polish_sha256"]["synthetic-00"] = sha(path)
    write(out / "twostep-polish" / "GUARD.json", guard)
    summary, _, _ = c.combine(out)
    assert c.TWOSTEP_HYBRID not in summary["rows"]


def test_modified_polished_file_rejected_even_if_objective_still_legal(tmp_path):
    out = synthetic(tmp_path)
    twostep_polish(out)
    path = out / "twostep-polish" / "synthetic-00.json"
    row = json.loads(path.read_text())
    row["after_moves"] = 6
    write(path, row)
    summary, _, _ = c.combine(out)
    assert "differs from completion guard" in summary["hybrid_status"][c.TWOSTEP_HYBRID]["reason"]


def test_incomplete_optional_hybrids_do_not_abort_first_combine(tmp_path):
    out = synthetic(tmp_path)
    (out / "polish").mkdir()
    (out / "twostep-polish").mkdir()
    summary, text, _ = c.combine(out)
    assert len(summary["rows"]) == 2 and len(summary["chosen"]) == 3
    assert all(s["status"] == "unavailable" for s in summary["hybrid_status"].values())
    assert text.count("No hybrid result admitted") == 2


def test_stowlm_cache_source_and_budget_binding(tmp_path):
    out = synthetic(tmp_path)
    stowlm_polish(out)
    row_path = out / "polish" / "synthetic-00.json"
    row = json.loads(row_path.read_text())
    source = out / "stowlm" / "instances" / "synthetic-00" / "result.json"
    assert p.cache_matches(row, source, 15)
    assert not p.cache_matches(row, source, 30)
    assert not p.cache_matches({"own_report_passed": True}, source, 15)
    write(source, {"different_chosen_plan_with_same_objective": True})
    assert not p.cache_matches(row, source, 15)
    summary, _, _ = c.combine(out)
    assert c.STOWLM_HYBRID not in summary["rows"]


@pytest.mark.parametrize("field,value", [("polished_lifts", float("nan")), ("polish_s", float("inf")),
    ("requested_polish_s", 30), ("source_result_sha256", "stale")])
def test_stowlm_hybrid_fail_closed(tmp_path, field, value):
    out = synthetic(tmp_path)
    stowlm_polish(out)
    path = out / "polish" / "synthetic-00.json"
    row = json.loads(path.read_text())
    row[field] = value
    write(path, row)
    summary, _, _ = c.combine(out)
    assert c.STOWLM_HYBRID not in summary["rows"]


def test_sealed_cli_prints_no_result_values_even_with_invalid_hybrid(tmp_path):
    out = synthetic(tmp_path)
    stowlm_polish(out)
    guard = twostep_polish(out)
    guard["n_mismatches"] = 1
    write(out / "twostep-polish" / "GUARD.json", guard)
    process = subprocess.run([sys.executable, str(Path(c.__file__)), str(out), "--sealed"], capture_output=True, text=True)
    assert process.returncode == 0
    assert process.stdout == "combine91 wrote summary.json and SUMMARY.md; errors=0\n"
    assert not process.stderr
    assert (out / "SUMMARY.md").exists()


def test_exact_sign_test_non_ties_only():
    left = {str(i): {"rung": "tiny", "outcome": c.OWN, "objective": 1} for i in range(5)}
    right = {str(i): {"rung": "tiny", "outcome": c.OWN, "objective": 2 if i < 4 else 1} for i in range(5)}
    pair = c.hybrid_pair(left, right, None)
    assert (pair["fewer"], pair["equal"], pair["more"]) == (4, 1, 0)
    assert pair["sign_test_exact"] == "1/8" and pair["sign_test_p"] == 0.125
