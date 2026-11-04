"""Run HR1 and HR2 on the public Junqueira instances and compare with Tables 7 and 8.

  .venv/bin/python -I run_junqueira.py readings   # try the readings of HR1 on 1A-12B
  .venv/bin/python -I run_junqueira.py hr2 [secs] # HR2 on the chosen reading, resumable

Results go to results/junqueira_*.json. No instance data is copied.
"""

from __future__ import annotations

import itertools
import json
import os
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from junqueira import Reading, load_instance, simulate  # noqa: E402

DATA = Path(
    os.environ.get(
        "JUNQ_DATA",
        "/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/"
        "650fd584-ebd7-4e07-a64a-d5d31504125f/scratchpad/repos/junqueira-instances/src",
    )
)
OUT = Path(__file__).resolve().parent / "results"

# (HR1 O.F., HR2 O.F.) from arXiv 2006.06795 Table 7 and Table 8.
TABLE = {
    "1A": (1, 1), "2A": (4, 4), "3A": (14, 8), "4A": (20, 16), "5A": (0, 0), "6A": (5, 5),
    "7A": (66, 59), "8A": (63, 48), "9A": (0, 0), "10A": (12, 11), "11A": (33, 26), "12A": (82, 66),
    "1B": (0, 0), "2B": (0, 0), "3B": (7, 7), "4B": (8, 8), "5B": (0, 0), "6B": (0, 0),
    "7B": (44, 40), "8B": (32, 32), "9B": (0, 0), "10B": (0, 0), "11B": (0, 0), "12B": (34, 34),
    "13A": (164, 164), "14A": (872, 815), "15A": (2110, 2033), "16A": (4512, 4512),
    "13B": (45, 45), "14B": (234, 221), "15B": (749, 749), "16B": (1525, 1499),
}
ORDER = [f"{i}{s}" for s in "AB" for i in range(1, 17)]


def instances():
    return {n: load_instance(DATA / f"Instance_{n}.mat") for n in ORDER}


def readings() -> None:
    insts = instances()
    small = [n for n in ORDER if int(n[:-1]) <= 12]
    grid = itertools.product(
        ["both", "branch1", "phys"],
        ["stack", "id"],
        ["first", "last"],
        ["far", "lifo", "fifo"],
        [False, True],
        [True, False],
        ["stack", "id"],
        ["after", "before"],
        ["stack", "stack_rev", "tier"],
    )
    rows = []
    for cap, ytie, stie, temp, csp, tf, unl, rw, ro in grid:
        if csp and unl == "id":
            continue
        r = Reading(cap, ytie, stie, temp, csp, tf, unl, rw, ro)
        diffs = {}
        for n in small:
            diffs[n] = simulate(insts[n], r)["of"] - TABLE[n][0]
        rows.append(
            {
                "reading": asdict(r),
                "exact_small": sum(1 for n in small if diffs[n] == 0),
                "abs_small": sum(abs(diffs[n]) for n in small),
                "diffs": diffs,
            }
        )
    rows.sort(key=lambda x: (-x["exact_small"], x["abs_small"]))
    OUT.mkdir(exist_ok=True)
    (OUT / "junqueira_readings.json").write_text(json.dumps(rows, indent=1))
    for cap in ("both", "branch1", "phys"):
        best = [r for r in rows if r["reading"]["cap"] == cap][0]
        print(cap, "exact", best["exact_small"], "of 24, abs diff", best["abs_small"], best["reading"])
    lit = next(r for r in rows if r["reading"] == asdict(Reading()))
    print("chosen literal reading: exact", lit["exact_small"], "of 24, abs diff", lit["abs_small"])
    print({k: v for k, v in rows[0]["diffs"].items() if v})


def hr2(cap_s: float, reading: Reading, rounds: int = 10_000, tag: str = "best") -> None:
    insts = instances()
    path = OUT / f"junqueira_hr2_{tag}.json"
    done = json.loads(path.read_text()) if path.exists() else {}
    for n in ORDER:
        if n in done:
            continue
        inst = insts[n]
        t0 = time.perf_counter()
        base = simulate(inst, reading)["of"]
        t_hr1 = time.perf_counter() - t0
        best, ran, t1 = base, 0, time.perf_counter()
        rng = random.Random(20261030)
        while ran < rounds and time.perf_counter() - t1 < cap_s:
            of = simulate(inst, reading, rng)["of"]
            ran += 1
            if of < best:
                best = of
                if best == 0:
                    break
        done[n] = {
            "hr1": base,
            "hr1_s": round(t_hr1, 3),
            "hr2": best,
            "rounds": ran,
            "hr2_s": round(time.perf_counter() - t1, 1),
            "boxes": inst.n,
        }
        print(n, done[n], flush=True)
        path.write_text(json.dumps(done, indent=1))


if __name__ == "__main__":
    if sys.argv[1] == "readings":
        readings()
    else:
        cap_s = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
        tag = sys.argv[3] if len(sys.argv) > 3 else "best"
        kw = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
        hr2(cap_s, Reading(**kw), tag=tag)
