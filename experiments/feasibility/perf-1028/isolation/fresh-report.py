"""Audit accepted corpus bytes and summarize the current generation campaign."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def main():
    import hashlib
    manifest = json.loads((HERE / "fresh-manifest.json").read_text())
    compute = json.loads((HERE / "fresh-compute.json").read_text())
    rows = []
    for row in manifest["instances"]:
        for path_key, hash_key in (("path", "sha256"), ("audit_path", "audit_sha256")):
            assert hashlib.sha256((ROOT / row[path_key]).read_bytes()).hexdigest() == row[hash_key]
        audit = json.loads((ROOT / row["audit_path"]).read_text())
        assert audit["accepted"] and audit["c2_screened"] and audit["background_last"]["accepted"]
        assert audit["fresh_c2_verification"]["global_screen"] == "on"
        assert audit["fresh_c2_verification"]["completion"]["c2"] is True
        rows.append(dict(id=row["id"], generation_s=row["generation_s"], attempts=audit["attempts"],
                         background_checks=audit["background_checks"],
                         background_cache_hits=audit["background_cache_hits"],
                         states=sorted(audit["background_last"]["states"])))
    summary = dict(accepted=len(rows), requested=24, complete=manifest["complete"],
                   rejected_requests=len(manifest["rejected"]), rows=rows,
                   total_recorded_generation_s=sum(r["generation_s"] for r in rows),
                   maxrss_bytes_macos=manifest["maxrss_bytes_macos"],
                   initial_physics_sha256=manifest["physics"]["sha256"],
                   source_pins_unchanged=manifest["sources_before"] == manifest["sources_after"])
    (HERE / "fresh-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if manifest["complete"]:
        compute["remaining_campaign"].update(status="complete", exit_code=0,
            recorded_loop_seconds=manifest["last_process_wall_s"], maxrss_bytes_macos=manifest["maxrss_bytes_macos"])
        (HERE / "fresh-compute.json").write_text(json.dumps(compute, indent=2) + "\n")
    lines = ["# Fresh current D58 corpus", "",
             f"Accepted {len(rows)}/24 requests; rejected requests {len(manifest['rejected'])}.",
             "Matrix: feeder_500/800/1700 × tiny/small × random_yard/simulated_yard × two seeds.",
             "Scenario assumption: explicit n_boxes=4 tiny and 8 small, normal decision regions and full background physics.",
             "Each accepted audit records the normal D58 background gate, current clean provenance, C2-screened request,",
             "and an independent current production C2-on completion check. All instance/audit SHA256 values verified.", "",
             f"Recorded per-request generation total: {summary['total_recorded_generation_s']:.3f}s; peak {manifest['maxrss_bytes_macos']/2**30:.3f} GiB.",
             f"Source pins unchanged: {summary['source_pins_unchanged']}. Physics SHA256: {manifest['physics']['sha256']}.",
             "Pilot estimate ≤60s versus 20.061s actual; remaining 23 estimate ≤720s based on measured pilot plus 50% allowance.",
             f"Last campaign loop actual: {manifest['last_process_wall_s']:.3f}s. One CPU, <2.5GB allocation, no GPU; all three thread limits 1.",
             "nice -n10 was attempted; sandbox refused setpriority and executed the command.", "",
             "| Request | Attempts | Background checks | Generation s |", "|---|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['id']} | {row['attempts']} | {row['background_checks']} | {row['generation_s']:.3f} |")
    (HERE / "fresh-report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
