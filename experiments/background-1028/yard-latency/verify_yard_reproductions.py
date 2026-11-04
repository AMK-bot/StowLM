"""Read-only audits of original failed pilot requests after the yard fix."""
from __future__ import annotations

import json
from pathlib import Path

from stowage.model import Instance
from stowage.ports.profiles import effective_yard_tiers, load_port


ROOT = Path(__file__).resolve().parent
EXPECTED = {40: 58, 41: 58, 43: 58, 50: 229, 52: 428}


def audit(index: int, suffix: str = "after") -> dict:
    directory = ROOT / f"request{index:03d}-{suffix}"
    result = json.loads((directory / "result.json").read_text())
    row = {"index": index, "pid": result["pid"],
           "accepted": result.get("accepted", False),
           "elapsed_s": result["elapsed_s"],
           "peak_rss_bytes": result["peak_rss_bytes"], "spec": result["spec"],
           "source_changes": sorted(name for name in result["source_start"].keys()
                                    | result["source_end"].keys()
                                    if result["source_start"].get(name)
                                    != result["source_end"].get(name))}
    if not row["accepted"]:
        return row | {"error": result.get("error"),
                      "error_type": result.get("error_type")}
    instance = Instance.model_validate_json((directory / "emitted.instance.json").read_text())
    source = load_port(instance.family["port"])
    assert instance.port.model_dump(exclude={"yard"}) == source.model_dump(exclude={"yard"})
    originals = {block.id: block for block in source.yard.blocks}
    for block in instance.port.yard.blocks:
        original = originals.get(block.id, source.yard.blocks[-1])
        assert block.n_bays <= original.n_bays and block.n_rows <= original.n_rows
        assert block.equipment_id == original.equipment_id
        assert block.allowed_lengths_ft == original.allowed_lengths_ft
        assert block.dg_block == original.dg_block
        assert block.reefer_rows == [row for row in original.reefer_rows if row <= block.n_rows]
        assert block.max_tiers == effective_yard_tiers(source, original)
    evidence = result["audit"]
    count = len(instance.load_list)
    ground = sum((block.n_bays // 2) * block.n_rows for block in instance.port.yard.blocks)
    assert count == evidence["requested_targets"] == EXPECTED[index]
    assert ground >= count + 2
    reports = [json.loads(path.read_text()) for path in sorted(directory.glob("check-*.report.json"))]
    assert reports and all(report["passed"] for report in reports)
    assert all(part["ran"] and part["error"] is None
               for report in reports for part in report["parts"].values())
    assert evidence["accepted"] and evidence["background_last"]["accepted"]
    assert set(evidence["background_last"]["states"]) == {"departure", "mid_voyage", "arrival"}
    assert evidence["c2_screened"] is True
    assert instance.onboard.tank_masses_kg == evidence["background_last"]["booking_tank_masses_kg"]
    return row | {"demand": count, "ground_positions": ground,
                  "blocks": len(instance.port.yard.blocks),
                  "attempts": evidence["attempts"],
                  "background_checks": evidence["background_checks"],
                  "background_seconds": evidence["background_seconds"],
                  "background_cache_hits": evidence["background_cache_hits"],
                  "full_reports": len(reports),
                  "full_report_parts": [len(report["parts"]) for report in reports],
                  "completion_checks": evidence["completion_checks"],
                  "c2_screened": evidence["c2_screened"],
                  "rejections": evidence["rejections"],
                  "arrival_b23_ratio": evidence["background_last"]["states"]["arrival"]["b23_ratio"],
                  "source_capabilities_preserved": True,
                  "versions": instance.provenance.versions}


if __name__ == "__main__":
    rows = [audit(index) for index in EXPECTED]
    output = {"rows": rows, "total_elapsed_s": sum(row["elapsed_s"] for row in rows),
              "max_peak_rss_bytes": max(row["peak_rss_bytes"] for row in rows),
              "estimated_campaign_s": 720, "cores": 1, "gpu": False}
    repeat_directory = ROOT / "request050-after-stable"
    if (repeat_directory / "result.json").exists():
        repeat = audit(50, "after-stable")
        output["source_change_repeat"] = repeat
        output["total_elapsed_including_repeat_s"] = output["total_elapsed_s"] + repeat["elapsed_s"]
    text = json.dumps(output, indent=2, sort_keys=True) + "\n"
    (ROOT / "yard-reproductions-audit.json").write_text(text)
    print(text, end="")
