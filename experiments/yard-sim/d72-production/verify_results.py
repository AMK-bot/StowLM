"""Validate study artifacts without producing any new yards or running planners."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partial", action="store_true", help="check a strict subset; never certify grid completion")
    args = parser.parse_args()
    text = (HERE / "results.jsonl").read_text()
    if args.partial and text and not text.endswith("\n"):
        text = text[:text.rfind("\n") + 1]
    rows = [json.loads(line) for line in text.splitlines()]
    config = (json.loads((HERE / "run.json").read_text()) if (HERE / "run.json").exists()
              else {"draws_per_cell": 8, "max_attempts": 8} if args.partial else None)
    assert config is not None, "main executor has not completed its run metadata"
    from run_measurement import SHIPS, RUNG, PORTS, NOISES
    expected = [{"ship": ship, "rung": rung, "port": port, "noise": noise,
                 "seed": 720000 + draw, "max_attempts": config["max_attempts"]}
                for draw in range(config["draws_per_cell"]) for ship in SHIPS for rung in RUNG
                for port in PORTS for noise in NOISES]
    keys = [json.dumps(r["spec"], sort_keys=True) for r in rows]
    assert len(keys) == len(set(keys)), "duplicate independent-draw specification"
    expected_keys = {json.dumps(spec, sort_keys=True) for spec in expected}
    if args.partial:
        assert keys and set(keys) <= expected_keys, "unexpected or empty partial grid"
    else:
        assert set(keys) == expected_keys, "incomplete requested production grid"
    measured = [r for r in rows if r["status"] == "measured"]
    for row in measured:
        assert row["paired_input_sha256"] and isinstance(row["simulation_seed"], int)
        for name, requested in (("off", 0), ("default", .25), ("old_n_coupled", row["spec"]["noise"])):
            arm = row["arms"][name]
            assert arm["paired_input_sha256"] == row["paired_input_sha256"]
            assert arm["requested_share"] == requested
            effective = requested if row["spec"]["noise"] > 0 else 0
            assert arm["effective_share"] == arm["congestion_mixing"]["configured_share"] == effective
            assert arm["congestion_mixing"]["congestion_mix_share"] == requested
            policy = arm["congestion_mixing"]
            assert policy["mixed_boxes"] <= policy["mixing_selected_boxes"] <= policy["eligible_boxes"]
            assert policy["achieved_share"] == (policy["mixed_boxes"] / policy["eligible_boxes"]
                                               if policy["eligible_boxes"] else 0)
            metrics = arm["metrics"]
            assert metrics["foreign_blocked_exports"] <= metrics["exports_present"] <= metrics["requested_exports"]
            assert metrics["foreign_blocked_export_share"] == metrics["foreign_blocked_exports"] / metrics["requested_exports"]
            assert metrics["exports_absent"] == metrics["requested_exports"] - metrics["exports_present"]
            assert 0 <= metrics["yard_rehandle_bound"] <= metrics["foreign_boxes_present"]
            if row["spec"]["noise"] == 0 or name == "off":
                assert metrics["foreign_blocked_exports"] == 0
                assert metrics["yard_rehandle_bound"] == 0
            assert sum(b["nonempty_stacks"] for b in arm["blocks"]) == metrics["stack_count_nonempty"]
            for block in arm["blocks"]:
                assert block["over_limit_stacks"] == 0
                if row["spec"]["port"] in {"laem_chabang_like", "small_feeder"}:
                    assert block["yard_limit"] == {"laem_chabang_like": 5, "small_feeder": 3}[row["spec"]["port"]]
                hist = {int(h): n for h, n in block["height_histogram"].items()}
                assert sum(hist.values()) == block["nonempty_stacks"]
                assert max(hist, default=0) == block["max_height"] <= block["yard_limit"]
    for row in rows:
        for field in ("source_before", "source_after"):
            if field not in row:
                continue
            manifest = json.loads((HERE / "manifests" / (row[field] + ".json")).read_text())
            digest = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            assert digest == row[field] == manifest["sha256"]
    print(f"verified {'PARTIAL ' if args.partial else 'COMPLETE '}{len(rows)} records / {len(measured)} paired production-producer yard candidates")


if __name__ == "__main__":
    main()
