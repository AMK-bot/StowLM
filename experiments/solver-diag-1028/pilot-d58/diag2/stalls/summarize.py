"""Pure JSON verification and compact summary of the three pinned stall replays."""
from collections import Counter
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
rows = []
for case in ("025", "033", "045"):
    path = HERE / case / "result.json"
    result = json.loads(path.read_text())
    assert result["stage"] == "finished" and not result.get("error")
    assert result["source_matches_raw"] and result["source_drift"] == []
    assert result["static_inventory_matches_actual_missing"]
    assert result["production_diagnostics"]["global_screen"] == "on"
    assert all(prefix["next_action_legal"] is not False for prefix in result["prefixes"])
    assert all(all(prefix["completion"].values()) for prefix in result["prefixes"])
    final = result["prefixes"][-1]
    assert final["legal_places"] == final["legal_discharges"] == 0
    assert result["first_engine_dead_end_depth"] == final["depth"]
    assert all(item["production_legal"] == item["local_d55_legal"] == 0 for item in result["boxes"])
    probe_count = len(result["candidate_probes"])
    focused_rules = Counter()
    other_rules = Counter()
    d55_rejects = 0
    for probe in result["candidate_probes"]:
        assert not probe["production_mask_legal"] and not probe["local_d55_mask_legal"]
        assert "post_candidate_completion" not in probe  # rejected before C1/C2 admission
        if any(item["clearance_m"] < -1e-9 for item in probe.get("d55_reconstructed_clearances", {}).values()):
            d55_rejects += 1
        for violation in probe.get("placement_violations", []):
            (focused_rules if probe["box"] in violation["containers"] else other_rules)[violation["rule_id"]] += 1
    first_zero = {}
    for cid in result["missing_loads"]:
        zeros = [prefix["depth"] for prefix in result["prefixes"]
                 if prefix["watched_boxes"][cid]["where"][0] != "ship"
                 and prefix["watched_boxes"][cid]["legal_slots"] == 0]
        first_zero[cid] = min(zeros) if zeros else None
    initial = HERE / case / "initial-result.json"
    initial_wall = json.loads(initial.read_text())["elapsed_s"] if initial.exists() else 0.
    rows.append(dict(case=case, final_depth=final["depth"], loaded=final["loaded"],
        n_loads=result["n_loads"], missing_loads=result["missing_loads"],
        first_engine_dead_end_depth=result["first_engine_dead_end_depth"],
        first_missing_box_zero_legal_choices=first_zero,
        last_action=result["prefixes"][-2]["next_action"],
        prefix_checks=len(result["prefixes"]), completion_screens_all_true=True,
        concrete_candidates=probe_count, d55_rejects=d55_rejects,
        focused_placement_rules=dict(focused_rules), other_placement_diagnostics=dict(other_rules),
        exhaustive_focused_box_probes=all(item.get("candidate_probe_exhaustive", True) for item in result["boxes"]),
        elapsed_s=result["elapsed_s"], initial_telemetry_attempt_s=initial_wall,
        cumulative_case_s=result["elapsed_s"] + initial_wall,
        peak_rss_gb=max(result["peak_rss_gb"], json.loads(initial.read_text())["peak_rss_gb"] if initial.exists() else 0.),
        source_drift=[], source_matches_raw=True,
        result_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        global_instance_infeasibility_proved=False))
summary = dict(cases=rows, cumulative_probe_wall_s=sum(row["cumulative_case_s"] for row in rows),
               prefix_checks=sum(row["prefix_checks"] for row in rows),
               concrete_candidates=sum(row["concrete_candidates"] for row in rows),
               maximum_peak_rss_gb=max(row["peak_rss_gb"] for row in rows),
               production_source_edits_by_this_agent=False, frozen_source_changes=False,
               interpretation="Final prefixes have no legal engine continuation; no global physical instance infeasibility claim.")
(HERE / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
print(json.dumps({key: value for key, value in summary.items() if key != "cases"}, sort_keys=True))
