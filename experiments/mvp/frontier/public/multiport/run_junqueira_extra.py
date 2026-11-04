"""Two extras: HR1 under the runner-up reading, and 16B with the C the paper prints.

The repository's Instance_16B.mat has C = 133, the same as 16A, but Table 8 prints C = 266.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from junqueira import Reading, load_instance, simulate  # noqa: E402
from run_junqueira import DATA, ORDER, OUT  # noqa: E402

alt = Reading(ship_tie="last", temp="fifo")
res = {n: simulate(load_instance(DATA / f"Instance_{n}.mat"), alt)["of"] for n in ORDER}
(OUT / "junqueira_hr1_alt.json").write_text(json.dumps(res, indent=1))
print(res)

base = load_instance(DATA / "Instance_16B.mat")
fixed = dataclasses.replace(base, stacks=266, cap=[max(1, min(base.tiers, math.ceil(t / 266))) for t in base.theta])
r1 = simulate(fixed, Reading())
best, ran, t0 = r1["of"], 0, time.perf_counter()
rng = random.Random(20261030)
while ran < 10000 and time.perf_counter() - t0 < 150:
    best = min(best, simulate(fixed, Reading(), rng)["of"])
    ran += 1
out = {"hr1": r1["of"], "hr2": best, "rounds": ran, "hr2_s": round(time.perf_counter() - t0, 1), "file_C": base.stacks}
(OUT / "junqueira_16B_c266.json").write_text(json.dumps(out, indent=1))
print(out)
