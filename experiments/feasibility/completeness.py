"""Completeness of the feasibility engine's completion screens vs brute force on tiny instances (plan 4.4).

    UV_CACHE_DIR=$PWD/.uv-cache nice uv run python experiments/feasibility/completeness.py \
        --n 20 --out experiments/feasibility/completeness.json

For each tiny instance (first N per generator in ``experiments/instances-v2/{random_yard,simulated_yard}/tiny``):
  * Memoised DFS over an engine U with ``completion_screens: 0, global_screen: 0`` (L1∧L2 only), skipping actions with
    ``U.forces_restow(a)`` (restow-free plans; depth = number of load boxes). Every complete leaf is a plan.
  * A plan is **valid** iff its expanded plan passes ``apply_plan`` and the final ship has no hard violation of
    ``check_static_rules`` (C2 / stability / strength can NOT be checked here: the 3.7 model is unavailable, and
    the engine runs with C2 off). Objective = ``apply_plan(...).count.weighted_nonproductive``.
  * In lock-step, a screened engine S (``completion_screens: 1, global_screen: 0`` — C2 OFF) follows the same
    path while every action so far was S-legal. Reported: (a) valid plans S rejects somewhere on the path
    (a soundness bug — repro recorded: instance, action sequence, index of the first S-rejected action), (b) share
    of DFS edges (under S-alive nodes) that S prunes, and how many of those lead to a valid plan, (c) S's own
    optimum (best valid plan fully legal in S) vs the true optimum.
  * Optionally ``stowage.solver.exact.solve_exact`` (time-limited) objective vs brute-force optimum.

Static-rule results are cached by final placement set (validity depends only on the final ship).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "1"

from stowage.actions import get_action_space  # noqa: E402
from stowage.checker.rules import check_static_rules  # noqa: E402
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine  # noqa: E402
from stowage.io import load_instance  # noqa: E402
from stowage.moves import apply_plan  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
U_CFG = {"completion_screens": 0, "global_screen": 0}
S_CFG = {"completion_screens": 1, "global_screen": 0}


def tiny_paths(gen, n):
    d = ROOT / "experiments/instances-v2" / gen / "tiny"
    return sorted(p for p in d.glob("seed-*.json") if not p.name.endswith(".audit.json"))[:n]


def _key(eng):
    ex = eng._cur.ex
    return (tuple(sorted((k, tuple(v)) for k, v in ex.yard.items())), frozenset(ex.where.items()),
            frozenset(ex.covers_off.items()))


def brute(inst, space, node_cap, wall_cap):
    """Memoised DFS over engine states (the engines' legality is a function of the physical state, so subtrees of
    equal states are equal). Counts are numbers of complete plans (action sequences), multiplied through."""
    U = make_feasibility_engine(inst, space, config=FeasibilityConfig(extra=dict(U_CFG)))
    S = make_feasibility_engine(inst, space, config=FeasibilityConfig(extra=dict(S_CFG)))
    w = inst.objective
    weight = {"relocate": w.relocate, "restow_shift": w.restow, "restow_off": w.restow, "restow_on": w.restow,
              "hatch_cover_off": w.hatch_cover, "hatch_cover_on": w.hatch_cover, "load": 0.0, "discharge": 0.0}
    st = dict(states=0, terminal_states=0, valid_terminal_states=0, s_state_edges=0, s_state_pruned=0,
              truncated=False, invalid_rules={}, s_root_dead=repr(S.dead_end()), u_root_dead=repr(U.dead_end()))
    memo: dict = {}
    terminal: dict = {}   # final key -> (valid, future cost) ; future = apply_plan objective - path edge costs
    rejected_valid = []   # soundness bug repros (<= 5)
    t_end = time.perf_counter() + wall_cap
    INF = float("inf")

    def edge_cost(a):
        return sum(weight[m.kind] for m in U._cur.ex.copy().step(a))

    def dfs(alive, path_cost):
        """Returns (n_plans, n_valid, n_s_plans, n_s_valid, best, best_s, best_tail, best_s_tail) for the subtree;
        costs are cost-to-go; n_s_* count plans fully legal in S (0 unless alive)."""
        k = (_key(U), alive)
        if k in memo:
            return memo[k]
        if st["states"] >= node_cap or time.perf_counter() > t_end:
            st["truncated"] = True
            return (0, 0, 0, 0, INF, INF, None, None)
        st["states"] += 1
        if U.is_complete():
            fk = k[0]
            if fk not in terminal:
                st["terminal_states"] += 1
                res = apply_plan(inst, U.to_plan())
                if not res.legal:
                    terminal[fk] = (False, None, ("apply_plan illegal",))
                else:
                    rep = check_static_rules(inst, res.final_state.ship, call_index=inst.call_index)
                    hard = tuple(sorted({v.rule_id for v in rep.violations if v.severity == "hard"}))
                    if not rep.ran:
                        hard += ("checker-not-run",)
                    fut = float(res.count.weighted_nonproductive) - path_cost
                    terminal[fk] = (not hard, fut, hard)
                    st["valid_terminal_states"] += int(not hard)
                for h in terminal[fk][2]:
                    st["invalid_rules"][h] = st["invalid_rules"].get(h, 0) + 1
            ok, fut, _ = terminal[fk]
            r = (1, int(ok), int(alive), int(alive and ok), fut if ok else INF, fut if (ok and alive) else INF,
                 (), () if (ok and alive) else None)
            memo[k] = r
            return r
        cands = [a for a in U.legal_actions() if not U.forces_restow(a)]
        s_set = set(S.legal_actions()) if alive else set()
        n = nv = ns = nsv = 0
        best = best_s = INF
        bt = bst = None
        for a in cands:
            ok = alive and a in s_set
            c = edge_cost(a)
            U.push(a)
            if ok:
                S.push(a)
            sub = dfs(ok, path_cost + c)
            if ok:
                S.pop()
            U.pop()
            if alive:
                st["s_state_edges"] += 1
                if not ok:
                    st["s_state_pruned"] += 1
                    if sub[1] and len(rejected_valid) < 5:
                        rejected_valid.append(dict(prefix=[repr(x) for x in U.actions()], rejected=repr(a),
                                                   rejected_index=U.depth, valid_plans_below=sub[1]))
            n += sub[0]; nv += sub[1]; ns += sub[2]; nsv += sub[3]
            if sub[4] + c < best:
                best, bt = sub[4] + c, (a, *sub[6])
            if sub[5] + c < best_s:
                best_s, bst = sub[5] + c, (a, *sub[7])
        r = (n, nv, ns, nsv, best, best_s, bt, bst)
        memo[k] = r
        return r

    t0 = time.perf_counter()
    n, nv, ns, nsv, best, best_s, bt, bst = dfs(S.dead_end() is None, 0.0)

    def replay(seq):  # recompute the objective of a found plan directly (checks the additive decomposition)
        if seq is None:
            return None
        E = U.copy()
        while E.depth:
            E.pop()
        for a in seq:
            E.push(a)
        return float(apply_plan(inst, E.to_plan()).count.weighted_nonproductive)

    st.update(wall_s=time.perf_counter() - t0, plans=n, valid=nv, s_plans=ns, s_valid=nsv,
              n_rejected_valid=nv - nsv, optimum=None if best == INF else best,
              s_optimum=None if best_s == INF else best_s,
              optimum_replayed=replay(bt), s_optimum_replayed=replay(bst),
              optimum_plan=None if bt is None else [repr(a) for a in bt], rejected_valid=rejected_valid)
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="tiny instances per generator")
    ap.add_argument("--node-cap", type=int, default=200_000)
    ap.add_argument("--wall-cap", type=float, default=30.0, help="s per instance")
    ap.add_argument("--exact-time", type=float, default=0.0, help="solve_exact time limit (0 = skip)")
    ap.add_argument("--only", nargs="*", default=None, help="instance paths (debug)")
    ap.add_argument("--out", default=str(ROOT / "experiments/feasibility/completeness.json"))
    a = ap.parse_args()
    space = get_action_space()
    paths = [Path(p) if Path(p).is_absolute() else ROOT / p for p in a.only] if a.only else \
        [p for g in ("random_yard", "simulated_yard") for p in tiny_paths(g, a.n)]
    out = dict(meta=dict(date=time.strftime("%Y-%m-%d %H:%M %Z"), u_cfg=U_CFG, s_cfg=S_CFG,
                         c2="OFF (3.7 model unavailable); validity = apply_plan legal + no hard static violation",
                         argv=sys.argv[1:]), instances=[], errors=[])
    t_all = time.perf_counter()
    for p in paths:
        rel = str(p.relative_to(ROOT))
        inst = load_instance(p)
        try:
            row = dict(path=rel, n_load=len(inst.load_list), **brute(inst, space, a.node_cap, a.wall_cap))
        except Exception as exc:
            out["errors"].append(dict(path=rel, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc()[-2000:]))
            print(rel, "ERROR", exc, flush=True)
            continue
        if a.exact_time > 0:
            try:
                from stowage.solver.exact import solve_exact
                r = solve_exact(inst, time_limit_s=a.exact_time, max_nodes=20_000)
                row["exact"] = dict(status=r.status, objective=r.objective, lower_bound=r.lower_bound,
                                    wall_s=r.wall_time_s, nodes=r.expanded_nodes,
                                    termination=r.metrics.get("termination"),
                                    scope=r.metrics.get("action_space_scope"))
            except Exception as exc:
                row["exact"] = dict(error=f"{type(exc).__name__}: {exc}")
        out["instances"].append(row)
        print(rel, {k: row[k] for k in ("states", "plans", "valid", "s_plans", "s_valid", "s_state_edges",
                                         "s_state_pruned", "optimum", "optimum_replayed", "s_optimum", "truncated",
                                         "wall_s")},
              row.get("exact"), flush=True)
        Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    out["meta"]["wall_s"] = time.perf_counter() - t_all
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
