"""Audit measurement integrity without rerunning physics or search."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()
    errors = []
    generation = {int(p.stem): json.loads(p.read_text()) for p in (HERE / "generation/outcomes").glob("*.json")}
    expected = {(solver, index) for index, r in generation.items()
                if r.get("status") in ("generated", "resumed")
                for solver in (("exact", "scalable", "twostep") if r["spec"]["size"] == "tiny" else ("scalable", "twostep"))}
    observed = set()
    nonpasses = set()
    for path in (HERE / "runs").glob("*/*/done.json"):
        row = json.loads(path.read_text())
        key = row["solver"], row["index"]
        observed.add(key)
        if key not in expected or not (path.parent / "result.json").exists():
            errors.append(f"unexpected or missing measurement {key}")
            continue
        actual_hash = hashlib.sha256(Path(row["input"]).read_bytes()).hexdigest()
        if actual_hash != row.get("input_sha256"):
            errors.append(f"instance changed {key}")
        for phase, field in (("start", "source_sha256"), ("end", "source_end_sha256")):
            source = path.parent / f"source-{phase}.json"
            actual = hashlib.sha256(json.dumps(json.loads(source.read_text()), sort_keys=True).encode()).hexdigest() if source.exists() else None
            if not actual or actual != row.get(field):
                errors.append(f"missing or inconsistent source-{phase} {key}")
        full_chain = bool(row.get("completion") == "complete" and row.get("checker_passed")
                          and row.get("shippable") and row.get("mask_replay_passed"))
        if bool(row.get("clean_complete_pass")) != full_chain:
            errors.append(f"gate aggregation mismatch {key}")
        if not full_chain:
            nonpasses.add(key)
        if full_chain:
            report = json.loads((path.parent / "report.json").read_text())
            mask = json.loads((path.parent / "mask-replay.json").read_text())
            if not report["passed"] or not mask["passed"] or mask.get("global_screen") != "on" or mask.get("decoder_status") != "complete":
                errors.append(f"passing gate artifact mismatch {key}")
            if not (path.parent / "final.plan.json").exists():
                errors.append(f"passing plan missing {key}")
    if args.final and observed != expected:
        errors.append(f"unmeasured pairs {sorted(expected-observed)}")
    continued = set()
    for path in (HERE / "production-continuation").glob("*/*/done.json"):
        row = json.loads(path.read_text())
        key = row["solver"], row["index"]
        continued.add(key)
        if key not in nonpasses or not (path.parent / "result.json").exists():
            errors.append(f"unexpected or missing continuation {key}")
        if not row.get("source_sha256") or row.get("source_sha256") != row.get("source_end_sha256") or row.get("source_changes_since_raw") != []:
            errors.append(f"continuation source consistency unavailable {key}")
        if row.get("error") or row.get("dispatch_error") or row.get("stage") != "finished":
            errors.append(f"continuation not fully measured {key}")
    if args.final and nonpasses - continued:
        errors.append(f"missing production continuations {sorted(nonpasses-continued)}")
    scripts = sorted(HERE.glob("*.py")) + sorted((HERE / "triage").glob("*.py"))
    for script in scripts:
        ast.parse(script.read_text(), filename=str(script))
    summary = dict(final=args.final, expected=len(expected), observed=len(observed),
                   missing=sorted(expected-observed), scripts_parsed=len(scripts), errors=errors,
                   nonpasses=len(nonpasses), continuations=len(continued), missing_continuations=sorted(nonpasses-continued),
                   source_drift_generation=sum(bool(r.get("source_changed")) for r in generation.values()))
    (HERE / "audit.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
