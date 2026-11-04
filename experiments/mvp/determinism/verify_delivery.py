"""Read-only independent executive audit of D112 delivery artifacts."""
import copy
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text())


def main():
    matrix = read("matrix-current-1/summary.json")
    assert matrix["final"] and matrix["completed"] == 24
    assert matrix["all_reaped"] and not matrix["errors"]
    assert len(matrix["groups"]) == 4
    for group in matrix["groups"]:
        assert group["stable"] and not group["differences"]
        assert {(v["hashseed"], v["cache_mode"]) for v in group["variants"]} == {
            (seed, mode) for seed in (0, 1, 42) for mode in ("cold", "history-warm")
        }

    ab = read("asset-ab/summary.json")
    assert ab["valid"] and ab["final"] and len(ab["rows"]) == 6
    assert ab["source_code_identical_across_treatments"]
    assert ab["parameter_and_tokenizer_bytes_identical"]
    assert ab["all_code_fingerprints_match_source"]
    for row in ab["rows"]:
        raw = json.loads(Path(row["artifact"]).read_text())
        assert raw["completed"] and raw["source_after_valid"] and row["reaped"]
        assert raw["pythonhashseed"] == str(row["hashseed"])
        assert raw["native_legal_actions"] == ["Place(box='QZXU0000117', slot='0020784')"]
        active = row["treatment"] == "provided"
        assert raw["screen_initialization"]["bounds_available"] is active
        assert raw["token_sets"][""] == ([] if active else [60])

    full = read("fullcheck/summary.json")
    assert full["final"] and full["completed"] == 4 and full["all_children_reaped"]
    normalized = []
    for row in full["rows"]:
        raw = json.loads(Path(row["checker"]).read_text())
        assert not raw["passed"] and row["reaped"] and row["source_valid"]
        if row["treatment"] != "provided":
            assert any(v["rule_id"] == "checker.missing_vessel_data" for v in raw["violations"])
            continue
        assert len(raw["violations"]) == 3
        assert all("stability time budget exceeded" in v["message"] for v in raw["violations"])
        adjusted = copy.deepcopy(raw)
        assert len(row["excluded_measured_timing_fields"]) == 7
        for field in row["excluded_measured_timing_fields"]:
            assert field["path"][-1].endswith("elapsed_s")
            parent = adjusted
            for key in field["path"][:-1]:
                parent = parent[key]
            assert parent.pop(field["path"][-1]) == field["value"]
        normalized.append(adjusted)
    assert len(normalized) == 3 and normalized[0] == normalized[1] == normalized[2]

    gold = read("analysis/GOLD_IMPACT.json")
    assert len(gold["gates"]) == 3
    for gate in gold["gates"].values():
        flags = gate["flags"]
        assert flags["cases"] == flags["completed"] == flags["contract_passed"] == 100
        assert flags["historical_checker_bound"] == flags["source_valid"] == 100
        assert flags["cases_with_unavailable"] == flags["cases_with_failopen"] == 0
        assert gate["securing_numeric_totals"]["evaluations"] == 5654
        assert gate["archived_selected_origins"] == 0

    regression = read("regression/controller.json")
    assert regression["completed"] and regression["source_after_same"]
    for mode, failures in (("before", 3), ("after", 0)):
        suites = ET.parse(ROOT / "regression" / f"{mode}.xml").getroot().findall("testsuite")
        assert sum(int(s.attrib["tests"]) for s in suites) == 3
        assert sum(int(s.attrib["failures"]) for s in suites) == failures
        assert sum(int(s.attrib["errors"]) for s in suites) == 0

    cheap = read("cheap-tests.json")
    assert cheap["completed"] and cheap["reaped"] and cheap["source_after_same"]
    assert cheap["exit_code"] == 0 and cheap["counts"] == {
        "tests": 6, "errors": 0, "failures": 0, "skipped": 0
    }
    ledger = read("compute-ledger.json")
    assert not ledger["running"] and not ledger["queued"]

    patch = ROOT / "patches/archive_dependency_closure.patch"
    lines = patch.read_text().splitlines(keepends=True)
    start = lines.index("+++ b/experiments/mvp/lookahead/archive_assets.py\n") + 2
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("--- a/"))
    extracted = "".join(line[1:] for line in lines[start:end] if line.startswith("+"))
    assert extracted == (ROOT / "patches/archive_asset_closure.py").read_text()
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=ROOT.parents[2], check=True)
    print(json.dumps({"verified": True, "current_controls": 24, "causal_asset_probes": 6,
        "full_checker_probes": 4, "historical_gold_gates": "3 x same100",
        "regression_before_failures": 3, "regression_after_passes": 3,
        "additional_tests_passed": 6, "compute_ledger_closed": True,
        "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest()}, sort_keys=True))


if __name__ == "__main__":
    main()
