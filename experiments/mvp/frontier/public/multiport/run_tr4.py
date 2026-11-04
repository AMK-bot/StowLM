"""Run the DP-flow MIP on TR4. Resumable. Numbers go to results/tr4_<worker>.jsonl.

  .venv/bin/python -I run_tr4.py <worker> <limit_s> <list file in results/>
  .venv/bin/python -I run_tr4.py retry <limit_s> <list file in results/>

Workers share the list through claim directories, so one can start after another.
A crash leaves a claim behind: delete plans/tr4_claims/<name> to retry that instance.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from flowmip import solve_tr4  # noqa: E402
from kshift_io import expand_tr4, load_tr4, parse_name, write_plan  # noqa: E402

SCRATCH = Path(
    os.environ.get(
        "KSHIFT_SCRATCH",
        "/private/tmp/claude-501/-Users-anda-Projects-meta-hackathon-stowage/"
        "650fd584-ebd7-4e07-a64a-d5d31504125f/scratchpad",
    )
)
DATA = SCRATCH / "repos/kshift-roberti-parreno/extracted/Parreno-Torres_et_al_2021/TR4"
PLANS = SCRATCH / "plans/tr4"


def run_one(path: Path, limit: float, plans: Path = PLANS, seed: int = 1) -> dict:
    prob, by_class, layout = load_tr4(path)
    rec = {"file": path.name, **parse_name(path), "limit_s": limit}
    rec["t20"] = int(re.search(r"_T(\d+)_F(\d+)", path.name)[1])
    t0 = time.perf_counter()
    res = solve_tr4(prob.t, prob.empty, prob.stacks, prob.tiers, limit, seed=seed)
    rec["t_mip"] = round(time.perf_counter() - t0, 2)
    rec.update({key: res.get(key) for key in ("status", "fleets", "fails")})
    if res["stacks"] is None:
        raise RuntimeError(f"no incumbent: {res['fails']}")
    sol = {"patterns": Counter(res["stacks"]), "g": res["g"]}
    plan = expand_tr4(prob, sol, by_class, layout, random.Random(7))
    write_plan(plans / (path.name + ".json.gz"), plan)
    rec["rehandles_model"] = res["obj"]
    rec["ok"] = True
    rec["wall_s"] = round(time.perf_counter() - t0, 2)
    return rec


def load_all() -> dict:
    out = {}
    for path in sorted((HERE / "results").glob("tr4_*.jsonl")):
        for line in path.read_text().splitlines():
            rec = json.loads(line)
            out[rec["file"]] = rec
    return out


def retry(names: list, limit: float) -> None:
    """Second try on the listed instances. A plan replaces the old one only if it is better."""
    tmp = SCRATCH / "plans" / "tr4_retry"
    out = HERE / "results" / "tr4_zretry.jsonl"
    for name in names:
        old = load_all().get(name, {})
        try:
            rec = run_one(DATA / name, limit, plans=tmp, seed=2)
        except Exception as exc:  # keep the batch alive
            print({"file": name, "error": repr(exc)[:200]}, flush=True)
            continue
        better = not old.get("ok") or rec["rehandles_model"] < old["rehandles_model"]
        print({"file": name[:30], "old": old.get("rehandles_model"), "new": rec["rehandles_model"], "s": rec["wall_s"]}, flush=True)
        if better:
            rec["retry"] = True
            shutil.copy(tmp / (name + ".json.gz"), PLANS / (name + ".json.gz"))
            with out.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")


def main() -> None:
    worker, limit = sys.argv[1], float(sys.argv[2])
    names = (HERE / "results" / sys.argv[3]).read_text().split()
    if worker == "retry":
        retry(names, limit)
        return
    out = HERE / "results" / f"tr4_{worker}.jsonl"
    claims = SCRATCH / "plans" / "tr4_claims"
    claims.mkdir(parents=True, exist_ok=True)
    for name in names:
        done = set()
        for path in (HERE / "results").glob("tr4_*.jsonl"):
            done |= {json.loads(line)["file"] for line in path.read_text().splitlines()}
        if name in done:
            continue
        try:
            os.mkdir(claims / name)  # atomic: two workers never take the same instance
        except FileExistsError:
            continue
        t0 = time.perf_counter()
        try:
            rec = run_one(DATA / name, limit)
        except Exception as exc:  # keep the batch alive, the failure is recorded and reported
            rec = {"file": name, **parse_name(DATA / name), "ok": False, "error": repr(exc), "trace": traceback.format_exc()[-500:], "wall_s": round(time.perf_counter() - t0, 2)}
        with out.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print({k2: v for k2, v in rec.items() if k2 != "trace"}, flush=True)


if __name__ == "__main__":
    main()
