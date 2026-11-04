"""Focused saved-result report checks; no solver/model imports or GPU use."""
import json
from fractions import Fraction
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("report_polish_val", Path(__file__).with_name("report_polish_val.py"))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def inventory(tmp_path, missing=False):
    jobs, groups = [], []
    for arm in ("8.2", "twostep"):
        for name in r.SETS:
            jobs.append({"arm": arm, "set": name, "instance_id": "map", "before_moves": 10 if arm == "8.2" else 8,
                         "fingerprint": f"{arm}-{name}", "requested_polish_s": 15,
                         "historical_checker_s": 12.0, "status": "missing_plan" if missing else "ready"})
            groups.append({"arm": arm, "set": name, "source_rows": f"{arm}/{name}/rows.json", "expected_maps": 1,
                           "n_rows": 1, "n_passed": 1, "n_ready": 0 if missing else 1, "n_missing_plan": int(missing)})
    (tmp_path / "inventory.json").write_text(json.dumps({"jobs": jobs, "groups": groups}))
    return jobs


def save(tmp_path, job, after):
    path = tmp_path / job["arm"] / job["set"] / "map.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {**job, "status": "completed", "after_moves": after, "improved": after < job["before_moves"],
           "own_report_passed": True, "final_report_passed": True, "assignments_preserved": True,
           "ballast_preserved": True, "requested_polish_s": 15, "polish_s": 21.5, "own_check_s": 12,
           "candidate_check_s": 10, "qualification_check_s": 11, "acceptance_check_s": 9,
           "check_s_total": 42, "total_s": 50}
    path.write_text(json.dumps(row))
    return path


def test_exact_sign_and_ties():
    assert r.sign_test(4, 0) == {"n_discordant": 4, "p": 0.125, "p_exact": "1/8"}
    assert r.sign_test(0, 0)["p"] == 1
    result = r.compare({"a": 1, "b": 1, "c": 2}, {"a": 3, "b": 2, "c": 2}, {"a", "b", "c"})
    assert (result["fewer"], result["more"], result["equal"]) == (2, 0, 1)
    assert result["sign_test"]["n_discordant"] == 2
    assert Fraction(result["sign_test"]["p_exact"]) == Fraction(1, 2)


def test_complete_and_same_maps_paired(tmp_path):
    jobs = inventory(tmp_path)
    for job in jobs:
        save(tmp_path, job, 7 if job["arm"] == "8.2" else 6)
    report = r.build(tmp_path)
    assert report["complete"] and report["polish_run"]
    own = next(s for s in report["summaries"] if s["arm"] == "8.2" and s["set"] == "combined")
    assert (own["mean_before_own_passes"], own["mean_after_completed"], own["improved"]) == (10, 7, 2)
    assert own["polish_s"]["mean"] == 21.5  # actual overruns must remain visible
    polished = next(c for c in report["comparisons"] if c["set"] == "combined" and c["right_polished"])
    unpolished = next(c for c in report["comparisons"] if c["set"] == "combined" and not c["right_polished"])
    assert polished["mean_right"] == 6 and polished["more"] == 2
    assert unpolished["mean_right"] == 8 and unpolished["fewer"] == 2
    assert "21.50" in r.render(report)


def test_missing_plans_never_silently_count_as_unchanged(tmp_path):
    jobs = inventory(tmp_path, missing=True)
    # Even a stale result cannot replace an inventory-declared missing plan.
    save(tmp_path, jobs[0], 7)
    report = r.build(tmp_path)
    assert not report["complete"] and not report["polish_run"]
    assert len(report["missing_plans"]) == 4
    assert all(s["mean_after_completed"] is None for s in report["summaries"])
    assert all(s["polish_s"]["total"] is None for s in report["summaries"])
    assert all(c["mutual"] == 0 for c in report["comparisons"])
    text = r.render(report)
    assert "polish NOT RUN" in text and "— (not run)" in text
    assert "Original validation checker cost (historical)" in text
    assert "12.00 / 12.00" in text


def test_partial_pair_uses_completed_mutual_subset(tmp_path):
    jobs = inventory(tmp_path)
    save(tmp_path, jobs[0], 7)
    save(tmp_path, jobs[2], 6)
    report = r.build(tmp_path)
    pair = next(c for c in report["comparisons"] if c["set"] == "combined" and c["right_polished"])
    assert not report["complete"] and not pair["complete"]
    assert (pair["mutual"], pair["expected_mutual"]) == (1, 2)
    assert pair["mean_left"] == 7


def test_nonpassing_or_worse_result_is_unusable(tmp_path):
    jobs = inventory(tmp_path)
    path = save(tmp_path, jobs[0], 11)
    report = r.build(tmp_path)
    assert not report["polish_run"] and "unusable" in report["issues"][0]["issue"]
    row = json.loads(path.read_text())
    row.update(after_moves=8, before_moves=9)
    path.write_text(json.dumps(row))
    report = r.build(tmp_path)
    assert not report["polish_run"] and "objective differs" in report["issues"][0]["issue"]


def test_empty_inventory_cannot_claim_complete(tmp_path):
    (tmp_path / "inventory.json").write_text('{"jobs": [], "groups": []}')
    report = r.build(tmp_path)
    assert not report["complete"] and report["source_problems"]


def test_stale_fingerprint_cannot_count_as_completed(tmp_path):
    jobs = inventory(tmp_path)
    path = save(tmp_path, jobs[0], 7)
    row = json.loads(path.read_text())
    row["fingerprint"] = "stale"
    path.write_text(json.dumps(row))
    report = r.build(tmp_path)
    assert not report["polish_run"]
    assert "fingerprint" in report["issues"][0]["issue"]


def test_requested_budget_is_from_jobs_even_when_not_run(tmp_path):
    inventory(tmp_path, missing=True)
    path = tmp_path / "inventory.json"
    inv = json.loads(path.read_text())
    for job in inv["jobs"]:
        job["requested_polish_s"] = 30
    path.write_text(json.dumps(inv))
    report = r.build(tmp_path)
    assert all(s["requested_polish_s"] == [30] for s in report["summaries"])
