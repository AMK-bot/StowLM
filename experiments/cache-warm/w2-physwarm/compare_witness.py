"""Compare unchanged public verdicts and measured interpolation error."""
import json
from pathlib import Path

import numpy as np
from stowage.checker import stability_liquid
from stowage.vessel import load_ship_params

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
direct = json.loads((OUT / "witness-direct.json").read_text())
fast = json.loads((OUT / "witness-fast.json").read_text())
historical = json.loads((ROOT / "experiments/checker/followup-1028/d71-refinement/small/report-0.json").read_text())
manifest_path = stability_liquid.manifest_path(load_ship_params("feeder_1700"))
limits = json.loads(manifest_path.read_text())["validation"]


def verdicts(report):
    return dict(passed=report["passed"], violations=report["violations"],
                parts={key: {k:v for k,v in row.items() if k not in ("metrics", "margins")}
                       for key,row in report["parts"].items()})


maxima = dict(gz=0., area=0., gm=0.)
comparisons = []
for reference, label in ((direct["report"], "fresh direct"), (historical, "saved direct")):
    row = dict(reference=label, verdicts_identical=verdicts(reference) == verdicts(fast["report"]),
               maxima=dict(gz=0., area=0., gm=0.), other_numeric_deltas=[])
    for part, old in reference["parts"].items():
        new = fast["report"]["parts"][part]
        for key, value in old["metrics"].items():
            if key not in new["metrics"]:
                continue
            actual = new["metrics"][key]
            if not isinstance(value, (float,int)) or not isinstance(actual, (float,int)):
                continue
            delta = abs(actual-value)
            category = ("gm" if key == "gm_m" else "area" if "area_" in key and key.endswith("mrad")
                        else "gz" if key in ("gz_max_m", "gz_at_or_above_30_m") else None)
            if category:
                floor, relative = {"gm":(.05,.01), "area":(.0012,.05), "gz":(.05,.05)}[category]
                fraction = delta/max(floor, relative*abs(value))
                row["maxima"][category] = max(row["maxima"][category], fraction)
                maxima[category] = max(maxima[category], fraction)
            elif delta:
                row["other_numeric_deltas"].append(dict(part=part, metric=key, delta=actual-value))
    comparisons.append(row)

assert len(direct["curves"]) == len(fast["curves"])
curve_comparisons = []
for old,new in zip(direct["curves"],fast["curves"]):
    aa,bb = np.array(old["angles_rad"]),np.array(new["angles_rad"])
    end = min(aa[-1],bb[-1])
    nodes = np.unique(np.r_[aa[aa<=end],bb[bb<=end],end])
    row = dict(context=old["context"], max_absolute_gz_difference_m=0., max_gz_l5_fraction=0.)
    for key in ("gz_m", "negative_gz_m"):
        a,b = np.interp(nodes,aa,old[key]),np.interp(nodes,bb,new[key])
        row["max_absolute_gz_difference_m"] = max(row["max_absolute_gz_difference_m"],float(max(abs(a-b))))
        row["max_gz_l5_fraction"] = max(row["max_gz_l5_fraction"],float(max(abs(a-b)/np.maximum(.05,.05*abs(a)))))
    maxima["gz"] = max(maxima["gz"],row["max_gz_l5_fraction"])
    curve_comparisons.append(row)

stability_parts = {key:row["metrics"] for key,row in fast["report"]["parts"].items()
                   if "stability" in key and "liquid_table_queries" in row["metrics"]}
result = dict(manifest=str(manifest_path), empirical_l5_limits={k:limits[k] for k in maxima},
              comparisons=comparisons, curves=curve_comparisons, observed_max_l5_fractions=maxima,
              all_verdicts_identical=all(row["verdicts_identical"] for row in comparisons),
              within_qualified_bounds=all(maxima[k] <= limits[k] for k in maxima),
              fast_envelope_calls=fast["envelope_calls"],
              liquid_table_queries=sum(row.get("liquid_table_queries",0) for row in stability_parts.values()),
              liquid_direct_queries=sum(row.get("liquid_direct_queries",0) for row in stability_parts.values()),
              historical_cold_seconds=33.751862, fresh_direct_seconds=direct["seconds"],
              fast_cold_seconds=fast["seconds"], fixture_hashes_identical=direct["fixture_hashes"]==fast["fixture_hashes"])
result["passed"] = (result["all_verdicts_identical"] and result["within_qualified_bounds"]
                    and result["fixture_hashes_identical"] and result["fast_envelope_calls"]==0
                    and result["liquid_table_queries"]>0 and result["liquid_direct_queries"]==0)
(OUT / "witness-comparison.json").write_text(json.dumps(result, indent=2)+"\n")
print(json.dumps({k:v for k,v in result.items() if k not in ("comparisons","curves")}),flush=True)
assert result["passed"]
