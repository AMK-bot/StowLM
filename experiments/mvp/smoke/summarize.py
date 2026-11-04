"""Render measured smoke facts from persisted per-case records."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def cell(stage):
    if not stage:
        return "—"
    elapsed = stage.get("wall_s")
    if elapsed is None:
        return "RUNNING"
    return ("PASS" if stage.get("passed") else "FAIL") + f" {elapsed:.3f}s"


def render(directory):
    rows = [json.loads(p.read_text()) for p in sorted(directory.glob("*/result.json"))]
    campaign_path = directory / "results.json"
    campaign = json.loads(campaign_path.read_text()) if campaign_path.exists() else {}
    rows.sort(key=lambda r: (r["replicate"], ("tiny", "small", "medium").index(r["rung"]), r["ship"]))
    lines = ["Measured OFF smoke (seconds; — means stage not reached).", "",
        "| Case | Generation | Constructor | Ballast | Full checker | Token mask C2 OFF | First failure |",
        "|---|---:|---:|---:|---:|---:|---|"]
    for row in rows:
        stages = row["stages"]
        first = row.get("first_failure", {})
        failure = first.get("rule") or first.get("error") or stages.get("checker", {}).get("first_failing_rule") or row.get("error") or "—"
        if failure == "—" and not stages.get("mask_c2_off", {}).get("passed"):
            failure = stages.get("mask_c2_off", {}).get("error", "—")
        failure = str(failure).replace("|", "\\|").replace("\n", " ")
        lines.append("| " + " | ".join([row["case"], *[cell(stages.get(s))
            for s in ("generation", "constructor", "ballast", "checker", "mask_c2_off")], failure]) + " |")
    lines += ["", "C2 latency includes actual production token-mask generation and advance calls; "
        "action latency covers all tokens of one chosen action. Nearest-rank p95.", "",
        "| Medium case | C2 | Replay | Tokens completed | Token median/p95 ms | Action median/p95 ms | Engine setup s | Failure |",
        "|---|---|---|---:|---:|---:|---:|---|"]
    for row in rows:
        if row["rung"] != "medium" or row["replicate"] != 0:
            continue
        for setting in ("off", "on"):
            data = row["stages"].get("mask_c2_" + setting, {})
            if not data:
                continue
            def latency(name):
                med, p95 = data.get(name+"_median"), data.get(name+"_p95")
                return f"{med:.3f}/{p95:.3f}" if med is not None and p95 is not None else "unavailable"
            setup = data.get("engine_setup_s")
            lines.append("| " + " | ".join([row["case"], setting,
                "PASS" if data.get("passed") else "FAIL/not reached", str(len(data.get("token_ms", []))),
                latency("token_ms"), latency("action_ms"), f"{setup:.3f}" if setup is not None else "—",
                data.get("error", row.get("error", "—"))]) + " |")
    drift = [r["case"] for r in rows if r.get("source_sha256_end") is not None
             and r["source_sha256_end"] != r.get("source_sha256_start")]
    complete = sum(bool(r.get("completed", "wall_s" in r and "source_sha256_end" in r)) for r in rows)
    expected = campaign.get("expected_cases")
    lines += ["", f"Complete OFF passes: {sum(bool(r.get('passed')) for r in rows)}/{len(rows)} recorded cases.",
        f"Finished records: {complete}/{len(rows)}; expected campaign cases: {expected if expected is not None else 'not recorded (historical run)'}. "
        f"Campaign complete: {campaign.get('campaign_complete', False)}.",
        campaign.get("c2_reason", "Historical configuration; see per-case records."),
        f"Source fingerprint changed during these cases: {', '.join(drift) or 'none'}."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    rendered = render(args.directory)
    (args.directory / "table.md").write_text(rendered)
    print(rendered)


if __name__ == "__main__":
    main()
