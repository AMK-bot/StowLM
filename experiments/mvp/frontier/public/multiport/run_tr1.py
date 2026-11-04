"""Run the DP-flow MIP on TR1. Resumable: finished instances are skipped.

  .venv/bin/python -I run_tr1.py <shard> <shards> [limit_s]

Plans go to the scratchpad (they are derived from GPL data), numbers go to results/tr1_<shard>.jsonl.
Rehandle counts for the report come from check_kshift.py, not from here.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from flowmip import solve_tr1  # noqa: E402
from kshift_io import S40, expand_tr1, load_tr1, parse_name, write_plan  # noqa: E402

SCRATCH = Path(
    os.environ.get(
        "KSHIFT_SCRATCH",
        "/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/"
        "650fd584-ebd7-4e07-a64a-d5d31504125f/scratchpad",
    )
)
DATA = SCRATCH / "repos/kshift-roberti-parreno/extracted/Parreno-Torres_et_al_2021/TR1"
PLANS = SCRATCH / "plans/tr1"


def main() -> None:
    shard, shards = int(sys.argv[1]), int(sys.argv[2])
    limit = float(sys.argv[3]) if len(sys.argv) > 3 else 180.0
    files = sorted(DATA.glob("*.txt"))
    # Required last: it alone needs the full MIP, because a zero plan usually does not exist.
    files.sort(key=lambda p: (parse_name(p)["cls"] == "Required", parse_name(p)["n"], parse_name(p)["r"] * parse_name(p)["c"], p.name))
    out = HERE / "results" / f"tr1_{shard}.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(line)["file"] for line in out.read_text().splitlines()}
    for k, path in enumerate(files):
        if k % shards != shard or path.name in done:
            continue
        prob = load_tr1(path)
        rec = {"file": path.name, **parse_name(path), "limit_s": limit}
        t0 = time.perf_counter()
        try:
            res = solve_tr1(prob.t[S40], prob.stacks, prob.tiers, limit)
            rec["t_mip"] = round(time.perf_counter() - t0, 2)
            rec.update({key: res.get(key) for key in ("status", "bound", "t_zero", "zero_status", "n_vars", "n_rows")})
            if res["stacks"] is None:
                raise RuntimeError(f"no incumbent: {res['status']}")
            pats = Counter(tuple(sorted((S40, i, j, tier, -1) for i, j, tier in st)) for st in res["stacks"])
            gv = {(S40, i, j, d): v for (i, j, d), v in res["g"].items()}
            plan = expand_tr1(prob, {"patterns": pats, "g": gv})
            write_plan(PLANS / (path.name + ".json.gz"), plan)
            rec["rehandles_model"] = res["obj"]
            rec["ok"] = True
        except Exception as exc:  # keep the batch alive, the failure is recorded and reported
            rec.update({"ok": False, "error": repr(exc), "trace": traceback.format_exc()[-400:]})
        rec["wall_s"] = round(time.perf_counter() - t0, 2)
        with out.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(rec, flush=True)


if __name__ == "__main__":
    main()
