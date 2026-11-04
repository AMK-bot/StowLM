"""Read saved parent-owned medium retry reports; perform no physical evaluation."""
from collections import defaultdict
import argparse
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=HERE)
    args = parser.parse_args()
    directory = args.directory
    strength, visibility = defaultdict(list), defaultdict(list)
    reports = []
    zero, negative = [], []
    for path in sorted(directory.glob("retry-*.witness.report.json")):
        report = json.loads(path.read_text())
        assert report["passed"]
        assert all(part["ran"] and not part["error"] and not part["violations"]
                   for part in report["parts"].values())
        phase_parts = {}
        for name, part in report["parts"].items():
            for key, value in part["margins"].items():
                item = dict(report=path.name, part=name, margin=key, value=value)
                if value < -1e-9:
                    negative.append(item)
                elif abs(value) <= 1e-9:
                    item["classification"] = ("Passing weather applicability flag, not a physical clearance"
                        if key.endswith("weather_table_applicability") else "Equality/numerical boundary; not claimed as positive clearance")
                    zero.append(item)
            if not name.startswith("condition:"):
                continue
            phase = next((phase for phase in ("departure", "mid_voyage", "arrival")
                          if f"condition.phase: {phase}" in part["metrics"]), None)
            if name.endswith(":strength"):
                values = {key: part["metrics"][key] for key in ("max_sf_ratio", "max_bm_ratio")}
                values.update({"margin_"+key: part["margins"][key] for key in ("sf_ratio", "bm_ratio")})
                for key, value in values.items():
                    strength[key].append(value)
                phase_parts.setdefault(phase, {})["strength"] = values
            if name.endswith(":visibility"):
                values = {"margin_"+key: value for key, value in part["margins"].items()}
                for key, value in values.items():
                    visibility[key].append(value)
                phase_parts.setdefault(phase, {})["visibility"] = values
        assert set(phase_parts) == {"departure", "mid_voyage", "arrival"}
        reports.append(dict(file=path.name, states=phase_parts))
    assert len(reports) == 6
    source_summary = json.loads((directory / "retry-summary.json").read_text())
    result = dict(scope="Six accepted medium normal retry trajectories in the stated directory; saved authoritative independent full-checker reports only. No new physical checks and no claim about all raw draws.",
        directory=str(directory),
        source_epochs=sorted({cell[key] for cell in source_summary["cells"] for key in ("source_start", "source_end")}),
        files=reports, all_passed=True, d57_states=18,
        maximum_shear_force_utilisation=max(strength["max_sf_ratio"]),
        maximum_bending_moment_utilisation=max(strength["max_bm_ratio"]),
        minimum_shear_force_margin_ratio=min(strength["margin_sf_ratio"]),
        minimum_bending_moment_margin_ratio=min(strength["margin_bm_ratio"]),
        minimum_visibility_blind_distance_margin_m=min(visibility["margin_blind_distance_m"]),
        minimum_total_blind_sector_margin_deg=math.degrees(min(visibility["margin_total_blind_sector_rad"])),
        minimum_clear_sector_margin_deg=math.degrees(min(visibility["margin_min_clear_sector_rad"])),
        zero_margins=zero, negative_margins=negative)
    (directory / "retry-part-margins.json").write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print(json.dumps({key: value for key, value in result.items() if key not in {"files", "zero_margins"}}, indent=2))


if __name__ == "__main__":
    main()
