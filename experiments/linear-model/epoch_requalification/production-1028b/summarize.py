"""Summarize qualified observations against their own directional reserves."""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

RUN = Path(__file__).resolve().parent
SHIPS = ("feeder_500", "feeder_1000", "feeder_1700")


def read(path):
    return json.loads(path.read_text())


def summarize():
    bank = read(RUN / "promotion/candidate-bank.json")
    result = {}
    lines = ["# Fresh production requalification", "",
        "Necessary errors include independent ordinary and edge observations and explicit reference transfer.",
        "Strength comparisons preserve every station; a maximum error is paired with its own station reserve.", "",
        "| Ship | Ordinary | Edge | Quantity | Max error | Reserve at max error | Max excess |",
        "|---|---:|---:|---|---:|---:|---:|"]
    for ship in SHIPS:
        raw = read(RUN / "ordinary" / ship / "raw.json")
        identity = raw["diagnostics"]["response_calibration_identity"]
        entry = bank["entries"][identity]
        outer = entry["necessary"]
        qualifications = [outer["holdout_qualification"], outer["edge_holdout_qualification"]]
        counts = [q["holdout_samples"] for q in qualifications]
        metrics = {}
        assert all(q["independent_holdout_passed"] is True for q in qualifications)
        for side, quantities in qualifications[0]["holdout_unsafe"].items():
            for metric in quantities:
                observed = np.asarray(np.maximum.reduce([np.asarray(q["holdout_unsafe"][side][metric])
                    for q in qualifications]))
                reserves = [np.asarray(q["holdout_unsafe"][side][metric])
                    - np.asarray(q["max_excess"][side][metric]) for q in qualifications]
                np.testing.assert_allclose(reserves[0], reserves[1], rtol=1e-12, atol=1e-12)
                reserve = reserves[0]
                excess = observed-reserve
                index = int(np.argmax(observed))
                obs, bound = float(observed.flat[index]), float(reserve.flat[index])
                key = f"{side}/{metric}"
                metrics[key] = dict(max_error=obs, reserve_at_max_error=bound,
                    max_excess=float(np.max(excess)), reserve_min=float(np.min(reserve)),
                    reserve_max=float(np.max(reserve)),
                    station_at_max_error_m=(entry["station_x_m"][index] if observed.ndim else None))
                assert np.all(excess <= 0), (ship, key, metrics[key])
                lines.append(f"| {ship} | {counts[0]} | {counts[1]} | {key} | {obs:.9g} | {bound:.9g} | {float(np.max(excess)):.9g} |")
        boundary = outer["boundary_list"]
        for side in ("lower_unsafe", "upper_unsafe"):
            reserve = 1.1*(boundary["measured_optimistic_kgm"][side]
                + boundary["reference_transfer_kgm"][side])
            boundary_excess = boundary["qualification"]["max_holdout_excess_kgm"][side]
            interior_excess = boundary["interior_qualification"]["max_holdout_excess_kgm"][side]
            maximum_excess = max(boundary_excess, interior_excess)
            assert maximum_excess <= 0
            metrics[f"{side}/list_boundary_kgm"] = dict(
                max_error=reserve+maximum_excess, reserve=reserve, max_excess=maximum_excess,
                boundary_max_excess=boundary_excess, interior_max_excess=interior_excess,
                interior_holdout_samples=boundary["interior_qualification"]["holdout_samples"])
            lines.append(f"| {ship} | {counts[0]} | {counts[1]} | {side}/list_boundary_kgm | {reserve+maximum_excess:.9g} | {reserve:.9g} | {maximum_excess:.9g} |")
        own_paths = [path for directory in ("ordinary-logs", "ordinary-logs-time-denied")
            for path in (RUN/directory).glob(f"*{ship}*.execution.json")]
        if ship == "feeder_1000":
            own_paths += list((RUN/"ordinary-logs").glob("targeted-*.execution.json"))
        edge_paths = list((RUN/"edge-logs").glob(f"{ship}*.execution.json"))
        own_logs = [read(path) for path in own_paths]
        edge_logs = [read(path) for path in edge_paths]
        spans = []
        for path in own_paths+edge_paths:
            log = read(path)
            if "seconds" not in log:
                raise ValueError(f"qualification worker still active: {path}")
            end = path.stat().st_mtime
            start = (datetime.fromisoformat(log["start"]).timestamp()
                if "start" in log else end-log["seconds"])
            spans.append((start, end))
        wall_start, wall_end = min(start for start, _ in spans), max(end for _, end in spans)
        result[ship] = dict(ordinary_holdouts=counts[0],edge_holdouts=counts[1],
            boundary_holdouts_per_direction=boundary["qualification"]["holdout_samples"],
            response_identity=identity,metrics=metrics,
            qualification_wall_s=wall_end-wall_start,
            qualification_start_utc=datetime.fromtimestamp(wall_start, timezone.utc).isoformat(),
            qualification_end_utc=datetime.fromtimestamp(wall_end, timezone.utc).isoformat(),
            summed_ordinary_worker_wall_s=sum(log["seconds"] for log in own_logs),
            summed_edge_worker_wall_s=sum(log["seconds"] for log in edge_logs))
    lines += ["", "Wall time spans first fresh raw worker start through last edge worker completion; ships overlap.", "",
        "| Ship | Qualification wall s |", "|---|---:|"]
    lines += [f"| {ship} | {record['qualification_wall_s']:.3f} |" for ship, record in result.items()]
    (RUN/"results.json").write_text(json.dumps(result, indent=2)+"\n")
    (RUN/"results.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({ship: {k: r[k] for k in ("ordinary_holdouts", "edge_holdouts",
        "summed_ordinary_worker_wall_s", "summed_edge_worker_wall_s")} for ship,r in result.items()}))


if __name__ == "__main__":
    summarize()
