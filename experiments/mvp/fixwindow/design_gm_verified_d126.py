"""Recompute all six D56 conditions; preserve exact caps without assuming C3 wins.

Erratum to the historical extracted runbook helper: feeder_800 is governed by
C2 departure, not C3. This changes an experiment assertion, not a physical rule.
"""
import json
import math
from pathlib import Path
import sys
import time

from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_lashing_design import populate_lashing_design


def main():
    assert rule_freeze_v1_enabled() is False
    out = Path(sys.argv[1]) / "design-gm-verified.json"
    if out.exists():
        raise FileExistsError("Preserve previous GM measurements: " + str(out))
    expected = {"feeder_500": 3.2838356051426465,
                "feeder_800": 3.9584570964621637,
                "feeder_1700": 5.530108952747626}
    rows = []
    for ship, cap in expected.items():
        started, cpu = time.perf_counter(), time.process_time()
        vessel = load_vessel(ship)
        audit = populate_lashing_design(vessel, load_ship_params(ship),
                                       "D126 repaired-source six-condition recomputation")
        records = audit["conditions"]
        assert len(records) == 6 and {(r["condition"], r["phase"]) for r in records} == {
            (condition, phase) for condition in ("C1", "C2", "C3")
            for phase in ("departure", "arrival")}
        assert all(math.isfinite(r["gm_m"]) for r in records)
        maximum = max(records, key=lambda r: r["gm_m"])
        assert audit["governing"] == maximum
        assert audit["factor"] == 1.1
        actual = audit["max_gm_lashing_m"]
        assert actual == 1.1 * maximum["gm_m"] == cap, (ship, actual, cap)
        row = dict(ship=ship, prior_cap_m=cap, cap_m=actual, exactly_unchanged=True,
                   delta_m=actual-cap, wall_s=time.perf_counter()-started,
                   cpu_s=time.process_time()-cpu, audit=audit)
        rows.append(row)
        out.write_text(json.dumps(rows, indent=2, allow_nan=False) + "\n")
        print(json.dumps(dict(ship=ship, cap_m=actual, governing=maximum["condition"],
                              phase=maximum["phase"], six_conditions=True)), flush=True)


if __name__ == "__main__":
    main()
