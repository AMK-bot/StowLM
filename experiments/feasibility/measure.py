"""Feasibility engine v2 (plan 4.4) measurements on instances-v2: latency, legal-set size, diagnostics, dead ends.

Usage (single core, reproducible; all randomness from the seeds below):

    UV_CACHE_DIR=$PWD/.uv-cache nice uv run python experiments/feasibility/measure.py \
        --per-rung tiny=20 small=10 medium=4 full=3 --random tiny=10 small=10 medium=3 full=2 \
        --out experiments/feasibility/results.json

What it does, per rung and generator (``random_yard``, ``simulated_yard``):
  1. Instances: the first N canonical files ``experiments/instances-v2/<gen>/<rung>/seed-*.json`` in seed order
     (``*.audit.json`` and ``manifest.json`` skipped), loaded with ``stowage.io.load_instance`` unchanged: KCS hulls,
     DG boxes kept, catalogue rule defaults (D25 weight classes; no overrides).
  2. Engine config: ``global_screen: 0`` — **C2 OFF (3.7 model unavailable)**; every number from this script is
     C2 OFF. ``c2_probe.py`` records the C2-on construction attempts separately.
  3. Construction time (``make_feasibility_engine``).
  4. Policies: ``random`` (uniform over ``legal_actions``; K rollouts per instance) and ``greedy`` (forces_restow
     False first, then heaviest box, then lowest tier, then ``space.sort_key``; 1 per instance). Along every
     ``random`` rollout each state is timed: legal_actions cold (first call on a new state) and cached (second
     call), is_legal (on the chosen action), copy, push and pop (push/pop/push of the chosen action).
  5. Dead ends: ``dead_end().rule_ids`` and reason; ``completion_check()`` of the dead-end state; and the
     "rejected-candidate profile": a shadow engine with ``completion_screens: 0`` (L1∧L2 only) replays the same
     actions; each of its candidates (≤ ``--profile-cap``) at the dead end is pushed and the post-state's
     ``completion_check()`` False keys are counted (the per-candidate screens are built to equal that check).
     No L1∧L2 candidate at all means L1/L2 closed every option.
  6. ``--census RUNG...``: depth-0 status of every canonical instance (alive, dead-end rules, completion_check
     False keys, load boxes with no statically allowed slot). ``--select alive``: measure the first N instances
     that are alive at depth 0 instead of the first N (a labelled secondary sample, not the canonical prefix).
  7. Completed plans: ``apply_plan(instance, engine.to_plan()).legal`` and hard violations of
     ``check_static_rules`` on the final ship (E6 counted separately).

Exceptions are caught and recorded with (instance path, policy, rollout seed, depth, action) and counted as "error".
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import resource
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "1"

import numpy as np  # noqa: E402

from stowage.actions import get_action_space  # noqa: E402
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine  # noqa: E402
from stowage.io import load_instance  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GENERATORS = ("random_yard", "simulated_yard")
E6 = "expert.e6.heavy_under_deck"
C2_OFF = {"global_screen": 0}
LAT_KEYS = ("legal_cold", "legal_cached", "is_legal", "push", "pop", "copy")


def instance_paths(gen: str, rung: str, n: int | None = None) -> list[Path]:
    d = ROOT / "experiments/instances-v2" / gen / rung
    files = sorted(p for p in d.glob("seed-*.json") if not p.name.endswith(".audit.json"))
    return files[:n]


def root_status(inst, eng) -> dict:
    """Is the engine alive at depth 0? If not: rule ids, completion_check False keys, and the load boxes with no
    statically allowed slot at all (L2 rejects them everywhere), with their DG/special attributes."""
    de = eng.dead_end()
    if de is None:
        return dict(alive=True)
    T = eng.T
    zero = [T.box_ids[i] for i in np.flatnonzero(T.static.sum(axis=1) == 0)]
    box = {b.id: b for b in inst.containers}
    return dict(alive=False, rules=list(de.rule_ids), reason=de.reason,
                completion_false=[k for k, v in eng.completion_check().items() if not v],
                zero_slot_boxes=[dict(id=b, dg=None if box[b].dg is None else
                                      f"{box[b].dg.imdg_class}/{box[b].dg.stowage_category}/"
                                      f"{','.join(box[b].dg.stowage_codes)}",
                                      special=list(box[b].special_stowage), oog=box[b].oog is not None,
                                      reefer=box[b].reefer) for b in zero])


def drop_dg(inst):
    """LATENCY-ONLY DIAGNOSTIC (``--drop-dg``): remove every DG box (load list, containers, yard, quay buffer).
    Used only because no DG box is placeable on instances-v2 (missing checker facts), so every instance with DG is
    dead at depth 0 and no state exists to time. Never used for dead-end or completeness numbers."""
    from stowage.model import Instance

    d = inst.model_dump(mode="json")
    dg = {c["id"] for c in d["containers"] if c["dg"] is not None and c["id"] in set(d["load_list"])}
    d["containers"] = [c for c in d["containers"] if c["id"] not in dg]
    d["load_list"] = [b for b in d["load_list"] if b not in dg]
    for st in d["yard"]["stacks"]:
        st["boxes"] = [b for b in st["boxes"] if b not in dg]
    d["yard"]["stacks"] = [st for st in d["yard"]["stacks"] if st["boxes"]]
    d["yard"]["quay_buffer"] = [b for b in d["yard"]["quay_buffer"] if b not in dg]
    d["id"] = d["id"] + ".no-dg-latency-diagnostic"
    return Instance.model_validate(d), len(dg)


def census(rungs, gens, space) -> dict:
    """Root (depth-0) status of every canonical instance of ``rungs`` (C2 OFF)."""
    out = {}
    for rung in rungs:
        for gen in gens:
            rows = []
            for p in instance_paths(gen, rung):
                inst = load_instance(p)
                eng = make_feasibility_engine(inst, space, config=FeasibilityConfig(extra=dict(C2_OFF)))
                n_dg = sum(1 for b in inst.containers if b.id in set(inst.load_list) and b.dg is not None)
                rows.append(dict(path=str(p.relative_to(ROOT)), n_dg=n_dg, **root_status(inst, eng)))
            alive = sum(r["alive"] for r in rows)
            zero_dg = sum(1 for r in rows if not r["alive"] and any(z["dg"] for z in r["zero_slot_boxes"]))
            zero_any = sum(1 for r in rows if not r["alive"] and r["zero_slot_boxes"])
            with_dg = sum(1 for r in rows if r["n_dg"])
            out[f"{gen}/{rung}"] = dict(n=len(rows), alive=alive, with_dg=with_dg,
                                        alive_without_dg=sum(1 for r in rows if r["alive"] and not r["n_dg"]),
                                        dead_with_zero_slot_dg_box=zero_dg, dead_with_zero_slot_box=zero_any,
                                        rows=rows)
            print(f"census {gen}/{rung}: {alive}/{len(rows)} alive at root; {with_dg} with DG; dead with an "
                  f"unplaceable DG box {zero_dg}, any unplaceable box {zero_any}", flush=True)
    return out


# ----------------------------------------------------------------------------------------------- policies
def _tier(a):
    slot = getattr(a, "slot", None)
    return int(slot[-2:]) if slot else 99


def greedy_order(eng, legal, mass):
    """no-restow first, then heaviest box, then lowest tier, then canonical order."""
    return sorted(legal, key=lambda a: (eng.forces_restow(a), -mass.get(getattr(a, "box", None), 0.0), _tier(a),
                                        eng.space.sort_key(a)))


def pct(xs, q):
    return float(np.percentile(np.asarray(xs) * 1e3, q)) if xs else None


def rollout(eng0, policy, rng, mass, lat=None):
    """One rollout from a copy of ``eng0``. Returns dict(status, depth, engine, dead)."""
    eng = eng0.copy()
    while True:
        t0 = time.perf_counter()
        legal = eng.legal_actions()
        t1 = time.perf_counter()
        if lat is not None:
            lat["legal_cold"].append(t1 - t0)
            t0 = time.perf_counter()
            eng.legal_actions()
            lat["legal_cached"].append(time.perf_counter() - t0)
        if not legal:
            return dict(status="complete" if eng.is_complete() else "dead_end", depth=eng.depth, engine=eng,
                        dead=eng.dead_end())
        if lat is not None:
            lat["n_legal"].append(len(legal))
        a = legal[rng.randrange(len(legal))] if policy == "random" else greedy_order(eng, legal, mass)[0]
        if lat is not None:
            t0 = time.perf_counter()
            ok = eng.is_legal(a)
            lat["is_legal"].append(time.perf_counter() - t0)
            if not ok:
                raise AssertionError(f"listed action not is_legal at depth {eng.depth}: {a!r}")
            t0 = time.perf_counter()
            c = eng.copy()
            lat["copy"].append(time.perf_counter() - t0)
            del c
            t0 = time.perf_counter()
            eng.push(a)
            lat["push"].append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            eng.pop()
            lat["pop"].append(time.perf_counter() - t0)
        try:
            eng.push(a)
        except Exception as exc:
            raise RuntimeError(f"push of listed action failed at depth {eng.depth} (action #{eng.depth}): {a!r}: "
                               f"{type(exc).__name__}: {exc}") from exc


def candidate_profile(shadow0, actions, cap):
    """Replay ``actions`` on the L1∧L2-only shadow; for its candidates at the end, count completion_check False
    keys on each post-state."""
    sh = shadow0.copy()
    for a in actions:
        sh.push(a)
    cands = list(sh.legal_actions())
    prof: Counter = Counter()
    for a in cands[:cap]:
        sh.push(a)
        cc = sh.completion_check()
        sh.pop()
        bad = tuple(k for k, v in cc.items() if not v)
        prof["+".join(bad) if bad else "all_true"] += 1
    return dict(n_l1l2_candidates=len(cands), profiled=min(len(cands), cap), false_keys=dict(prof))


def verify(inst, eng):
    from stowage.checker.rules import check_static_rules
    from stowage.moves.engine import apply_plan

    plan = eng.to_plan()
    res = apply_plan(inst, plan)
    hard = []
    if res.legal:
        rep = check_static_rules(inst, res.final_state.ship, call_index=inst.call_index)
        hard = sorted({v.rule_id for v in rep.violations if v.severity == "hard"})
        if not rep.ran:
            hard.append(f"checker-not-run:{rep.error}")
    return dict(apply_legal=bool(res.legal), apply_violations=[v.rule_id for v in res.violations][:5],
                static_hard=[h for h in hard if h != E6], e6_hard=E6 in hard, n_moves=len(plan.moves),
                weighted_nonproductive=float(res.count.weighted_nonproductive),
                restow_lifts=int(getattr(res.count, "restow_lifts", 0)))


def run_policy_block(inst, eng, label, n_random, *, greedy=True, timing=True, shadow=None, profile_cap=60,
                     seed_base=0, errors=None, deadline=None):
    """Run ``n_random`` random rollouts (+1 greedy) on one engine. Returns dict(rows, lat, diagnostics)."""
    mass = {b.id: float(b.gross_mass_kg) for b in inst.containers}
    lat = {k: [] for k in (*LAT_KEYS, "n_legal")}
    rows, diags = [], []
    errors = [] if errors is None else errors
    plan = [("random", r) for r in range(n_random)] + ([("greedy", 0)] if greedy else [])
    for policy, r in plan:
        if deadline is not None and time.perf_counter() > deadline:
            break
        seed = seed_base * 1009 + r
        rng = random.Random(seed)
        t0 = time.perf_counter()
        try:
            res = rollout(eng, policy, rng, mass, lat if (timing and policy == "random") else None)
        except Exception as exc:
            errors.append(dict(instance=label, policy=policy, rollout_seed=seed, error=f"{type(exc).__name__}: {exc}",
                               tb=traceback.format_exc()[-2000:]))
            rows.append(dict(policy=policy, seed=seed, status="error"))
            continue
        e = res["engine"]
        row = dict(policy=policy, seed=seed, status=res["status"], depth=res["depth"],
                   wall_s=time.perf_counter() - t0)
        if res["status"] == "dead_end":
            d = res["dead"]
            row.update(dead_reason=d.reason if d else None, dead_rules=list(d.rule_ids) if d else [],
                       remaining=int(len(inst.load_list) - sum(1 for a in e.actions() if hasattr(a, "slot"))),
                       completion_at_dead={k: bool(v) for k, v in e.completion_check().items()})
            if shadow is not None:
                try:
                    row["candidate_profile"] = candidate_profile(shadow, e.actions(), profile_cap)
                except Exception as exc:
                    row["candidate_profile"] = dict(error=f"{type(exc).__name__}: {exc}")
                    errors.append(dict(instance=label, policy=policy, rollout_seed=seed,
                                       error=f"shadow replay: {type(exc).__name__}: {exc}",
                                       tb=traceback.format_exc()[-2000:]))
        elif res["status"] == "complete":
            try:
                row.update(n_actions=e.depth, **verify(inst, e))
            except Exception as exc:
                row.update(verify_error=f"{type(exc).__name__}: {exc}")
                errors.append(dict(instance=label, policy=policy, rollout_seed=seed,
                                   error=f"verify: {type(exc).__name__}: {exc}", tb=traceback.format_exc()[-2000:]))
            if not row.get("apply_legal", False) or row.get("static_hard"):
                errors.append(dict(instance=label, policy=policy, rollout_seed=seed,
                                   error="completed plan failed verification",
                                   detail={k: row.get(k) for k in ("apply_violations", "static_hard")}))
        diags.append({k: e.diagnostics.get(k) for k in ("lp_calls", "c1a_flows", "exact_calls", "c2_unresolved",
                                                         "c2_slot_lps", "dig_trials", "recenters")})
        rows.append(row)
        print(f"  {label} {policy} s{seed}: {row['status']} depth={row.get('depth')} {row.get('wall_s', 0):.2f}s"
              + (f" rules={row.get('dead_rules')}" if row["status"] == "dead_end" else ""), flush=True)
    return dict(rows=rows, lat=lat, diagnostics=diags)


def summarize_lat(lat):
    out = {k: dict(p50=pct(lat[k], 50), p95=pct(lat[k], 95), p99=pct(lat[k], 99), n=len(lat[k])) for k in LAT_KEYS}
    nl = lat["n_legal"]
    out["n_legal"] = dict(n=len(nl), min=min(nl, default=None), p50=float(np.median(nl)) if nl else None,
                          mean=float(np.mean(nl)) if nl else None, max=max(nl, default=None))
    return out


def _kv(items, cast=int):
    return {k: cast(v) for k, v in (s.split("=") for s in items)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--per-rung", nargs="+", default=["tiny=20", "small=10", "medium=4", "full=3"],
                    help="instances per generator per rung, rung=N")
    ap.add_argument("--random", nargs="+", default=["tiny=10", "small=10", "medium=3", "full=2"],
                    help="random rollouts per instance, rung=K")
    ap.add_argument("--generators", nargs="+", default=list(GENERATORS))
    ap.add_argument("--profile-cap", type=int, default=60, help="candidates profiled per dead end")
    ap.add_argument("--max-wall", type=float, default=600.0, help="per-rung-and-generator wall cap in s")
    ap.add_argument("--select", choices=("first", "alive"), default="first",
                    help="first N instances in seed order, or the first N alive at depth 0 (labelled in output)")
    ap.add_argument("--drop-dg", action="store_true", help="LATENCY-ONLY diagnostic: remove DG load boxes")
    ap.add_argument("--census", nargs="*", default=None, help="rungs for a root-status census of every instance")
    ap.add_argument("--out", default=str(ROOT / "experiments/feasibility/results.json"))
    args = ap.parse_args()
    per, nrand = _kv(args.per_rung), _kv(args.random)
    space = get_action_space()
    out = dict(meta=dict(date=time.strftime("%Y-%m-%d %H:%M %Z"), python=sys.version.split()[0],
                         machine=platform.machine(), engine_config=C2_OFF, c2="OFF (3.7 model unavailable)",
                         select=args.select, drop_dg=args.drop_dg, argv=sys.argv[1:]), rungs={}, errors=[])
    t_all = time.perf_counter()
    if args.census:
        out["census"] = census(args.census, args.generators, space)
        out["meta"]["census_wall_s"] = time.perf_counter() - t_all
    for rung, n in per.items():
        R = dict(instances=[], construct_s=[], rows=[], diagnostics=[], by_generator={})
        lat_all = {k: [] for k in (*LAT_KEYS, "n_legal")}
        for gen in args.generators:
            t_g = time.perf_counter()
            lat_g = {k: [] for k in (*LAT_KEYS, "n_legal")}
            picked = 0
            for i, p in enumerate(instance_paths(gen, rung, None if args.select == "alive" else n)):
                if picked >= n:
                    break
                rel = str(p.relative_to(ROOT))
                inst = load_instance(p)
                dropped = 0
                if args.drop_dg:
                    inst, dropped = drop_dg(inst)
                t0 = time.perf_counter()
                eng = make_feasibility_engine(inst, space, config=FeasibilityConfig(extra=dict(C2_OFF)))
                ct = time.perf_counter() - t0
                if args.select == "alive" and eng.dead_end() is not None:
                    continue
                picked += 1
                shadow = make_feasibility_engine(inst, space, config=FeasibilityConfig(
                    extra={"global_screen": 0, "completion_screens": 0}))
                n_dg = sum(1 for b in inst.containers if b.id in set(inst.load_list) and b.dg is not None)
                R["instances"].append(dict(path=rel, generator=gen, n_load=len(inst.load_list), n_dg=n_dg,
                                           dg_dropped_for_latency_diagnostic=dropped,
                                           construct_s=ct, global_screen=eng.diagnostics["global_screen"],
                                           dead_at_root=repr(eng.dead_end())))
                R["construct_s"].append(ct)
                blk = run_policy_block(inst, eng, rel, nrand[rung], shadow=shadow, profile_cap=args.profile_cap,
                                       seed_base=i, errors=out["errors"], deadline=t_g + args.max_wall)
                for row in blk["rows"]:
                    row.update(instance=rel, generator=gen)
                R["rows"] += blk["rows"]
                R["diagnostics"] += blk["diagnostics"]
                for k in lat_g:
                    lat_g[k] += blk["lat"][k]
            R["by_generator"][gen] = dict(latency_ms=summarize_lat(lat_g), wall_s=time.perf_counter() - t_g)
            for k in lat_all:
                lat_all[k] += lat_g[k]
        R["latency_ms"] = summarize_lat(lat_all)
        out["rungs"][rung] = R
        print(f"{rung}: done {time.perf_counter() - t_all:.0f}s", flush=True)
        Path(args.out).write_text(json.dumps(out, indent=1, default=str))
    out["meta"]["wall_s"] = time.perf_counter() - t_all
    out["meta"]["peak_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20  # macOS: bytes
    Path(args.out).write_text(json.dumps(out, indent=1, default=str))
    print("wrote", args.out, f"{out['meta']['wall_s']:.0f}s", f"rss {out['meta']['peak_rss_mb']:.0f} MB")


if __name__ == "__main__":
    main()
