"""Independently bind every new report and the private feasibility witness."""
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def main():
    spec = importlib.util.spec_from_file_location("d130_reference_verification", HERE / "reference_audit.py")
    audit = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = audit
    spec.loader.exec_module(audit)
    summary = json.loads((HERE / "SUMMARY.json").read_text())
    epoch = audit.source_epoch()
    assert epoch["sha256"] == summary["source_epoch"]
    assert summary["complete"] and summary["reference_count"] == 32
    assert summary["distinct_group_count"] == 9 and summary["passing_references"] == 22
    from stowage.model import CheckReport, MovePlan
    from stowage.io import load_instance, load_plan
    from stowage.checker.full import report_matches_inputs
    instances = {}
    checked = []
    by_ship = {}
    for reference in summary["references"]:
        path = ROOT / reference["path"]
        assert sha256(path.read_bytes()).hexdigest() == reference["file_sha256"]
        source = ROOT / reference["input"]
        assert sha256(source.read_bytes()).hexdigest() == reference["input_sha256"]
        if reference["input"] not in instances:
            instances[reference["input"]] = load_instance(source)
        instance = instances[reference["input"]]
        data = audit.extract(path, reference["pointer"], reference["line"])
        plan = MovePlan.model_validate(data)
        report_path = ROOT / reference["report"]
        report = CheckReport.model_validate_json(report_path.read_text())
        assert report_matches_inputs(report, instance, plan)
        assert report.passed is reference["passed"]
        binding = ROOT / reference["binding"]
        assert binding.is_file()
        checked.append(dict(reference=reference["path"], pointer=reference["pointer"],
                            report=reference["report"], passed=report.passed,
                            report_file_sha256=sha256(report_path.read_bytes()).hexdigest()))
        row = by_ship.setdefault(reference["ship"], dict(passed=0, failed=0))
        row["passed" if report.passed else "failed"] += 1
    witness = HERE / "original-private-heat"
    instance, plan = load_instance(witness / "instance.json"), load_plan(witness / "plan.json")
    report = CheckReport.model_validate_json((witness / "report.json").read_text())
    assert report.passed and report_matches_inputs(report, instance, plan)
    pipeline = json.loads((witness / "result.json").read_text())
    assert pipeline["completed"] and not pipeline["passed"] and pipeline["blocked_at"] == "mask"
    assert pipeline["stages"]["checker"]["passed"]
    replay = json.loads((witness / "private-securing-mask.json").read_text())
    assert replay["passed"] and not replay["production_mask_approval"]
    assert replay["counters"]["unavailable"] == replay["counters"]["fail_open"] == 0
    assert audit.source_epoch() == epoch
    record = dict(verified_utc=datetime.now(timezone.utc).isoformat(), passed=True,
                  source_epoch=epoch["sha256"], references=checked, by_ship=by_ship,
                  frozen_originals_preserved=True, current_full_checker_witness_passed=True,
                  production_mask_witness_passed=False, private_corrected_secured_replay_passed=True,
                  witness_sha256={name:sha256((witness/name).read_bytes()).hexdigest()
                                  for name in ("instance.json", "plan.json", "report.json", "private-securing-mask.json")})
    path = HERE / "VERIFIED.json"
    if path.exists():
        raise FileExistsError("Preserve prior verification")
    path.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(dict(passed=True, references=len(checked), by_ship=by_ship,
                          witness_checker=True, production_mask=False, private_secured_replay=True)))


if __name__ == "__main__":
    main()
