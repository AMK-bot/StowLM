"""Current production necessary-C2 acceptance; twenty fixed one-attempt draws.

Pilot/resume checkpoints never repeat a requested seed. No monkeypatch, source
adapter, candidate redraw beyond max_attempts=1, ballast finisher or full-plan
claim. Both the current generator's explicit C2 screen and the canonical root
engine's global_screen=1 must pass.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import resource
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEST = HERE / "acceptance.json"


def pins():
    from stowage.frozen import current_provenance
    paths = list((ROOT / "src/stowage").rglob("*.py"))
    paths += [ROOT / "src/stowage/linear/margin_policy_data.json"]
    paths += list((ROOT / "params").rglob("*.toml"))
    paths += list((ROOT / "params").rglob("*.yaml"))
    paths += list((ROOT / "params").rglob("*.yml"))
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(paths)}
    return {"sources": sources,
            "provenance": current_provenance().model_dump(mode="json")}


def save(out):
    temp = DEST.with_suffix(".partial.json")
    temp.write_text(json.dumps(out, indent=2, default=str) + "\n")
    temp.replace(DEST)


def summary(rows):
    out = {}
    for rung in ("tiny", "small"):
        rr = [r for r in rows if r["rung"] == rung]
        n = len(rr)
        c1 = sum(r["generator_audit"]["c2_checks"] > 0 for r in rr)
        gp = sum(r["generated"] for r in rr)
        reached = sum(r["engine_c2_reached"] for r in rr)
        c2pass = sum(r["engine_c2_pass"] for r in rr)
        accepted = sum(r["accepted"] for r in rr)
        out[rung] = dict(requests=n, c1_surviving_c2_reached=c1,
            generator_c2_passes=gp, engine_c2_reached=reached,
            engine_c2_passes=c2pass, root_all_completion_passes=accepted,
            root_acceptance_unconditional=[accepted, n],
            generator_c2_acceptance_conditional=[gp, c1],
            engine_c2_acceptance_conditional=[c2pass, reached],
            root_acceptance_given_generated=[accepted, gp],
            changed_draws=sum(bool(r["changed_sources"]) for r in rr))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.generate.random_yard import generate_random_instance, RandomGenerationReport
    if DEST.exists():
        out = json.loads(DEST.read_text())
    else:
        out = dict(started=time.strftime("%Y-%m-%dT%H:%M:%S%z"), draws=[],
            configuration=dict(vessel="feeder_500", port="laem_chabang_like",
                max_attempts=1, generator_c2_screen=True, global_screen=1,
                direction="necessary", stability_margin_scale=1.0,
                source_adapter=False, completion_or_ballast_search=False),
            environment={key: os.environ.get(key) for key in
                ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "UV_NO_SYNC")})
    completed = {(r["rung"], r["seed"]) for r in out["draws"]}
    requests = [(rung, 70_900_000 + int(rung == "small") * 100_000 + i)
                for rung in ("tiny", "small") for i in range(10)]
    for rung, seed in requests[:args.limit]:
        if (rung, seed) in completed:
            continue
        row = dict(rung=rung, seed=seed, max_attempts=1, generated=False,
            engine_c2_reached=False, engine_c2_pass=False, accepted=False,
            pins_before=pins())
        audit = RandomGenerationReport()
        started = time.perf_counter()
        try:
            inst = generate_random_instance(rung, seed=seed, max_attempts=1,
                report=audit, c2_screen=True)
            row["generated"] = True
            row["instance_sha256"] = hashlib.sha256(inst.model_dump_json().encode()).hexdigest()
            generation_done = time.perf_counter()
            row["generation_s"] = generation_done - started
            eng = make_feasibility_engine(inst, get_action_space("boxslot"),
                config=FeasibilityConfig(extra={"global_screen": 1}))
            row["engine_construct_s"] = time.perf_counter() - generation_done
            row["engine_c2_reached"] = eng.diagnostics.get("global_screen") == "on"
            row["completion_checks"] = eng.completion_check()
            row["engine_c2_pass"] = row["engine_c2_reached"] and bool(row["completion_checks"]["c2"])
            row["accepted"] = row["engine_c2_reached"] and all(row["completion_checks"].values())
            row["engine_diagnostics"] = dict(eng.diagnostics)
        except Exception as exc:
            row["exception"] = dict(type=type(exc).__name__, message=str(exc),
                traceback=traceback.format_exc())
        row["wall_s"] = time.perf_counter() - started
        row["generator_audit"] = asdict(audit)
        row["pins_after"] = pins()
        before = row["pins_before"]["sources"]
        after = row["pins_after"]["sources"]
        row["changed_sources"] = {p: [before.get(p), after.get(p)]
            for p in sorted(set(before) | set(after)) if before.get(p) != after.get(p)}
        out["draws"].append(row)
        out["summary"] = summary(out["draws"])
        out["sum_draw_wall_s"] = sum(r["wall_s"] for r in out["draws"])
        out["peak_self_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20
        save(out)
        print(rung, seed, "generator", row["generated"], "engineC2", row["engine_c2_pass"],
            "accepted", row["accepted"], "wall", round(row["wall_s"], 3), flush=True)
    print(json.dumps(out["summary"], indent=2), flush=True)


if __name__ == "__main__":
    main()
