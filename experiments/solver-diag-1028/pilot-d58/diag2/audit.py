"""Check diagnostic evidence without rerunning search or physical checking."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()
    pin = json.loads((HERE / "frozen-source.json").read_text())
    problems = []
    for relative, expected in pin["files"].items():
        if sha(HERE / "frozen" / relative) != expected:
            problems.append(f"source pin changed: {relative}")
    selection = json.loads((HERE / "notes/selection.json").read_text())["selected"]
    expected = {(solver, case["index"]) for case in selection for solver in ("scalable", "twostep")}
    observed = set()
    for path in (HERE / "runs").glob("*/*/done.json"):
        row = json.loads(path.read_text())
        key = row["solver"], row["index"]
        observed.add(key)
        if row.get("error") or row.get("cap_expired") or row.get("returncode") != 0:
            problems.append(f"incomplete measurement: {key}")
        if sha(Path(row["input"])) != row["input_sha256"]:
            problems.append(f"input changed: {key}")
        if row.get("budget_s") != 1200 or row.get("source_sha256") != pin["source_sha256"] or row.get("source_end_sha256") != pin["source_sha256"]:
            problems.append(f"wrong budget/source: {key}")
        identity = json.loads((path.parent / "qualification-path-adapter.json").read_text())
        if any(r["pinned_sha256"] != r["original_sha256"] for r in identity["checks"]):
            problems.append(f"qualification identity mismatch: {key}")
        if row.get("clean_complete_pass") and not (row.get("completion") == "complete" and row.get("checker_passed") and row.get("shippable") and row.get("mask_replay_passed")):
            problems.append(f"passing gates inconsistent: {key}")
        if row.get("mask_replay") and row["mask_replay"].get("global_screen") != "on":
            problems.append(f"C2 not admitted for replay: {key}")
    if args.final and observed != expected:
        problems.append(f"missing long trials: {sorted(expected-observed)}")
    profile = json.loads((HERE / "profile/measurement-qualifiedpaths/result.json").read_text())
    if profile["dimensions"]["global_screen"] != "on" or profile.get("status") != "timeout" or profile.get("infeasibility_proved"):
        problems.append("profile status/qualification mismatch")
    stalls = json.loads((HERE / "stalls/summary.json").read_text())
    if stalls["prefix_checks"] != 145 or stalls["concrete_candidates"] != 42 or stalls["frozen_source_changes"]:
        problems.append("stalls evidence inconsistent")
    if args.final:
        mass = json.loads((HERE / "mass-contract.json").read_text())
        if not mass["guard_ast_verified"] or mass["contradiction_cases"] != 2 or mass["checker_lashing_sha256"] != sha(HERE / "frozen/src/stowage/checker/lashing.py"):
            problems.append("mass-contract evidence mismatch")
    scripts = sorted(HERE.glob("*.py")) + sorted((HERE / "profile").glob("*.py")) + sorted((HERE / "stalls").glob("*.py"))
    for script in scripts:
        ast.parse(script.read_text(), filename=str(script))
    result = dict(final=args.final, expected=12, completed=len(observed), missing=sorted(expected-observed),
                  source_files_verified=len(pin["files"]), scripts_parsed=len(scripts), problems=problems)
    (HERE / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
