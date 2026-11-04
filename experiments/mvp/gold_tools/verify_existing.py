"""Re-verify published gold split isolation; never rerun or weaken physics gates."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from driver import atomic_json
from split_admission import verify_candidate


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def authority_pins(corpus):
    paths = set()
    for cohort in ("test", "showcase"):
        paths.update((corpus / cohort).rglob("*.json"))
    for name in ("manifest.json", "releases.json", "test.requested.json",
                 "train.requested.json", "val.requested.json"):
        if (corpus / name).exists():
            paths.add(corpus / name)
    return {str(p.relative_to(corpus)): digest(p) for p in sorted(paths)}


def verify_existing(corpus, output, rung="tiny"):
    corpus, output = Path(corpus).resolve(), Path(output).resolve()
    pins = authority_pins(corpus)
    heldout_ids, heldout_hashes, heldout_seeds = set(), set(), set()
    for cohort in ("test", "showcase"):
        for path in (corpus / cohort).rglob("*.json"):
            value = json.loads(path.read_text())
            if isinstance(value, dict) and "containers" in value and "vessel" in value:
                heldout_ids.add(value["id"])
                heldout_hashes.add(digest(path))
                heldout_seeds.add(value["seed"])
    manifest = json.loads((corpus / "manifest.json").read_text())
    mismatches = []
    for row in manifest.get("files", []):
        if row.get("split") in {"test", "showcase"}:
            path = corpus / row["path"]
            if not path.exists() or digest(path) != row["sha256"]:
                mismatches.append(row["path"])
    records = []
    for result_path in sorted(output.glob(f"*/{rung}/**/result.json")):
        row = json.loads(result_path.read_text())
        if not (row.get("completed") and row.get("passed")):
            continue
        entry = dict(result_path=str(result_path.relative_to(output)), passed=False)
        try:
            path = corpus / result_path.parent.relative_to(output).with_suffix(".json")
            identity = verify_candidate(path, corpus, output)
            assert identity["input_sha256"] == row["input_sha256"], "gold input digest changed"
            assert identity["instance_id"] == row["instance_id"], "gold instance identity changed"
            assert identity["seed"] == row["seed"], "gold seed changed"
            assert identity["instance_id"] not in heldout_ids, "heldout instance ID"
            assert identity["input_sha256"] not in heldout_hashes, "heldout instance bytes"
            assert identity["seed"] not in heldout_seeds, "heldout root seed"
            assert digest(result_path.with_name("plan.json")) == row["plan_sha256"], "published plan digest changed"
            assert json.loads(result_path.with_name("checker.json").read_text())["passed"], "checker evidence missing"
            assert all(row["stages"][gate]["passed"] for gate in ("checker", "mask")), "gate evidence missing"
            entry.update(passed=True, **identity, plan_sha256=row["plan_sha256"])
        except (ValueError, AssertionError, KeyError, OSError) as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
        records.append(entry)
    changed = authority_pins(corpus) != pins
    return dict(checked_utc=datetime.now(timezone.utc).isoformat(), rung=rung,
                checked=len(records), affected=sum(not r["passed"] for r in records),
                passed=not mismatches and not changed and all(r["passed"] for r in records),
                authority_changed_during_audit=changed, frozen_manifest_mismatches=mismatches,
                heldout_instances=len(heldout_hashes), authority_sha256=pins, records=records,
                scope="membership, heldout/duplicate identities, input/plan hashes and stored gate evidence; no physics rerun")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=Path("experiments/mvp/corpus"))
    parser.add_argument("--output", type=Path, default=Path("experiments/mvp/gold"))
    args = parser.parse_args()
    report = verify_existing(args.corpus_root, args.output)
    atomic_json(args.output / "REVERIFY.json", report)
    print(json.dumps({k: report[k] for k in ("checked", "affected", "passed", "heldout_instances",
                                            "authority_changed_during_audit", "frozen_manifest_mismatches")}))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
