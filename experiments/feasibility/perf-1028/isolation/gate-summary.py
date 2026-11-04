"""Count only exact terminal cases with intact source/runtime audit closures."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=HERE / "gate-all48-manifest.json")
    parser.add_argument("--output", type=Path, default=HERE / "gate-coverage.json")
    args = parser.parse_args()
    corpus = json.loads(args.manifest.read_text())
    expected_rows = {row["id"]: row for row in corpus["instances"]}
    seeds = corpus["seeds"]
    accepted, prefixes, evidence, live, failures = {}, {}, [], [], []
    source_closure = runtime_closure = None
    for path in sorted(HERE.glob("gate-*.json")):
        data = json.loads(path.read_text())
        if "runs" not in data:
            continue
        if data.get("first_mismatch"):
            failures.append(dict(path=str(path), failure=data["failure"]))
        if not data.get("sources_after") or not data.get("runtime_after"):
            for key, row in data["runs"].items():
                live.append(dict(path=str(path), key=key, status=row["status"],
                                 actions=len(row["actions"]), compared_states=len(row["states"]),
                                 wall_s=row["wall_s"]))
            continue
        valid = (not data.get("changed_sources") and data["sources"] == data["sources_after"]
                 and data["runtime"] == data["runtime_after"])
        valid = valid and all((ROOT / relative).is_file() and sha(ROOT / relative) == value
                              for relative, value in data["sources"].items())
        if not valid:
            failures.append(dict(path=str(path), failure="source/runtime closure invalid"))
            continue
        if source_closure is None:
            source_closure, runtime_closure = data["sources"], data["runtime"]
        if data["sources"] != source_closure or data["runtime"] != runtime_closure:
            failures.append(dict(path=str(path), failure="cross-shard source/runtime mismatch"))
            continue
        snapshot = Path(data["manifest_path"])
        assert sha(snapshot) == data["manifest_sha256"]
        input_rows = {row["id"]: row for row in json.loads(snapshot.read_text())["instances"]}
        count = 0
        for key, row in data["runs"].items():
            expected = expected_rows[row["instance"]]
            assert input_rows[row["instance"]]["sha256"] == expected["sha256"]
            assert sha(ROOT / expected["path"]) == expected["sha256"]
            if len(row["states"]) != len(row["actions"]) + 1:
                continue
            identity = (row["c2"], row["instance"], row["seed"])
            if key.endswith("/measurement") and row["status"] in ("complete", "dead", "measurement-prefixes"):
                prefixes[(row["c2"], row["instance"])] = dict(path=str(path), states=len(row["states"]))
            elif row["status"] in ("complete", "dead"):
                accepted[identity] = dict(path=str(path), states=len(row["states"]), status=row["status"])
                count += 1
        evidence.append(dict(path=str(path), terminal_cases=count, wall_s=data.get("wall_s"), usage=data.get("usage")))
    modes = {}
    for c2 in (True, False):
        missing = [dict(instance=identity, seed=seed) for identity in expected_rows for seed in seeds
                   if (c2, identity, seed) not in accepted]
        missing_prefixes = [identity for identity, row in expected_rows.items()
                            if row.get("measurement") and (c2, identity) not in prefixes]
        selected = {key: value for key, value in accepted.items() if key[0] == c2}
        modes[f"c2-{int(c2)}"] = dict(expected_terminal_cases=len(expected_rows) * len(seeds),
            verified_terminal_cases=len(selected), verified_states=sum(value["states"] for value in selected.values()),
            saved_cases=sum(not identity.startswith("fresh-") for _, identity, _ in selected),
            fresh_cases=sum(identity.startswith("fresh-") for _, identity, _ in selected),
            measurement_replays=sum(mode == c2 for mode, _ in prefixes), missing=missing,
            missing_measurement_replays=missing_prefixes,
            complete=not missing and not missing_prefixes and not failures)
    output = dict(created=datetime.now(timezone.utc).isoformat(), modes=modes, evidence=evidence,
                  live_or_unclosed=live, failures=failures, source_pin_count=len(source_closure or {}),
                  all_required_modes_complete=all(mode["complete"] for mode in modes.values()))
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({key: {name: value for name, value in mode.items()
                          if name not in ("missing", "missing_measurement_replays")}
                      for key, mode in modes.items()}, indent=2))


if __name__ == "__main__":
    main()
