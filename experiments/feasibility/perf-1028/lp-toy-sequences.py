"""Supplemental synthetic engine sequences; never production qualification.

Synthetic tight linear rows and tank windows exercise actual engine C2 dispatch,
child-reference construction, copies and pop. Boundary probes show whether the
legacy tolerances make two primal-reference choices observationally different.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import random
import resource
import time

import numpy as np

from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.feasibility import FeasibilityConfig

import candidate


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def factory(seed, tightness, screen_class):
    def build(cls, instance, tables, *, margin_scale=1.):
        rng = np.random.default_rng(seed)
        C = len(tables.cell_ids)
        cells = {c.id: c for c in instance.vessel.cells}
        # SYNTHETIC diagnostic rows, not qualified physical stability limits.
        A = np.array([[cells[c].tcg_m for c in tables.cell_ids],
                      [cells[c].lcg_m for c in tables.cell_ids],
                      [cells[c].vcg_base_m for c in tables.cell_ids],
                      np.ones(C), (np.arange(C) % 3 == 0).astype(float)])
        At = np.array([[.8, -.8], [2., -3.], [.3, -.4], [0., 0.], [0., 0.]])
        total = float(tables.mass.sum())
        u0 = float(tables.share.max())
        tank_lo = rng.uniform(0., 300., 2)
        tank_hi = tank_lo + rng.uniform(100., 3000., 2)
        target = A @ np.full(C, total / C) + At @ ((tank_lo + tank_hi) / 2.)
        width = np.maximum(np.abs(A).max(axis=1) * total, 1.) * rng.uniform(.025, .18, 5) * tightness
        lo, hi = target - width, target + width
        lo[3] = hi[3] = total
        lo[4] = -np.inf
        return screen_class(A, At, np.zeros(5), lo, hi, tank_lo, tank_hi, u0)
    return classmethod(build)


def boundary_decisions(eng, slot, masses, classes):
    g, ref, T = eng._global, eng._cur.ref, eng.T
    if ref is None:
        return np.zeros(len(masses), dtype=bool)
    if isinstance(ref, classes.UnknownReference):
        return np.ones(len(masses), dtype=bool)
    sc, sn = T.slot_cells[[slot]], T.slot_n[[slot]]
    lo, hi = g.intervals(ref, sc, sn)
    ok = (masses >= lo[0] - 1e-6) & (masses <= hi[0] + 1e-6)
    (lu, uu), (ld, ud) = g.local_intervals(ref, sc, sn, eng._donors[[slot]])
    ok |= ((masses >= lu[0] - 1e-6) & (masses <= uu[0] + 1e-6))
    ok |= ((masses >= ld[0] - 1e-6) & (masses <= ud[0] + 1e-6))
    lb, ub = g.basis_intervals(ref, sc, sn)
    ok |= (masses >= lb[0]) & (masses <= ub[0])
    cells = [int(c) for c in sc[0] if c >= 0]
    sol = g.slot_interval(ref, cells, 0., min(ref.R, g.u0 * len(cells)))
    if sol is classes.UNKNOWN_INTERVAL:
        ok[:] = True
    elif sol is not None:
        ok |= (masses >= sol[0][0] - 1e-6) & (masses <= sol[1][0] + 1e-6)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--rollouts", type=int, default=2)
    ap.add_argument("--depth", type=int, default=6)
    ap.add_argument("--tightness", type=float, default=1.)
    ap.add_argument("--vary-tightness", action="store_true")
    ap.add_argument("--out", default="lp-toy-pilot.json")
    args = ap.parse_args()
    paths = [ROOT / "src/stowage/feasibility" / p for p in ("_engine.py", "global_bounds.py", "_lp_highs.py", "_certificates.py")]
    paths.append(Path(__file__).resolve())
    paths.extend([HERE / "candidate.py", HERE / "candidate" / "manifest.json",
                  HERE / "candidate" / "_engine.py", HERE / "candidate" / "global_bounds.py"])
    sources = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    start = time.perf_counter()
    report = dict(kind="supplemental synthetic actual-engine sequences; not production qualification",
                  config=vars(args), sources=sources, states=0, roots=0, roots_nonempty=0,
                  prefix_masks_equal=0, boundary_decisions=0, boundary_differences=[],
                  mismatches=[], slot_infeasible=0, unknown_intervals=0, witness_hits=0,
                  parent_witness_hits=0, singleton_pushes=0, pair_pushes=0, copy_pop_checks=0,
                  forced_mass_comparisons=0, forced_mass_infeasible=0, forced_mass_mismatches=[])
    classes = {False: candidate.get_reference_classes(), True: candidate.get_candidate_classes()}
    old_factories = {mode: api.GlobalScreen.__dict__["from_instance"] for mode, api in classes.items()}
    space = get_action_space("boxslot")
    try:
        for seed in range(args.seeds):
            instance = tiny_instance(seed + 1704, n_load=8, n_other=0)
            tightness = args.tightness * ((.01, .05, .2, 1.)[seed % 4] if args.vary_tightness else 1.)
            for mode, api in classes.items():
                api.GlobalScreen.from_instance = factory(seed + 1704, tightness, api.GlobalScreen)
            for rollout in range(args.rollouts):
                rng = random.Random((seed + 1704) * 100 + rollout)
                config = FeasibilityConfig(extra={"global_screen": 1})
                old = candidate.make_engine(instance, config, False, space=space)
                new = candidate.make_engine(instance, config, True, space=space)
                report["roots"] += 1
                for depth in range(args.depth):
                    a, b = old.legal_index(), new.legal_index()
                    report["states"] += 1
                    if depth == 0 and len(a):
                        report["roots_nonempty"] += 1
                    if not np.array_equal(a, b):
                        report["mismatches"].append(dict(seed=seed, rollout=rollout, depth=depth,
                                                       only_old=np.setdiff1d(a, b).tolist(), only_new=np.setdiff1d(b, a).tolist()))
                        break
                    report["prefix_masks_equal"] += 1
                    for engine, api in ((old, classes[False]), (new, classes[True])):
                        report["slot_infeasible"] += sum(s is None for s in engine._cur.c2_sols.values())
                        report["unknown_intervals"] += sum(s is api.UNKNOWN_INTERVAL for s in engine._cur.c2_sols.values())
                    # Use two slot endpoint neighborhoods as diagnostic masses;
                    # these are not inserted into the physical cargo manifests.
                    if old._cur.ref is not None and not isinstance(old._cur.ref, classes[False].UnknownReference):
                        chosen_slots = list(dict.fromkeys((a % old.n_slots).tolist()))[:2]
                        for slot in chosen_slots:
                            cells = [int(c) for c in old.T.slot_cells[slot] if c >= 0]
                            sol = old._global.slot_interval(old._cur.ref, cells, 0., min(old._cur.ref.R, old._global.u0 * len(cells)))
                            if sol is None or sol is classes[False].UNKNOWN_INTERVAL:
                                continue
                            masses = np.array([m + delta for m, delta in itertools.product((sol[0][0], sol[1][0]), (-2e-6, -5e-7, -1e-8, 0., 1e-8, 5e-7, 2e-6))])
                            masses = masses[(masses >= 0.) & (masses <= min(old._cur.ref.R, old._global.u0 * len(cells)))]
                            x, y = boundary_decisions(old, slot, masses, classes[False]), boundary_decisions(new, slot, masses, classes[True])
                            report["boundary_decisions"] += len(masses)
                            if np.any(x != y):
                                report["boundary_differences"].append(dict(seed=seed, rollout=rollout, depth=depth, slot=int(slot),
                                                                         masses=masses[x != y].tolist(), old=x[x != y].tolist(), new=y[x != y].tolist()))
                            # Point-mass LPs expose genuinely infeasible child
                            # relaxations, including physical masses that the
                            # broad interval query can avoid by choosing zero.
                            cap = min(old._cur.ref.R, old._global.u0 * len(cells))
                            for mass in (.5 * cap, cap):
                                answers = [engine._global.slot_interval(engine._cur.ref, cells, mass, mass) for engine in (old, new)]
                                statuses = ["infeasible" if s is None else "unknown" if s is api.UNKNOWN_INTERVAL else "feasible"
                                            for s, api in zip(answers, (classes[False], classes[True]))]
                                report["forced_mass_comparisons"] += 1
                                report["forced_mass_infeasible"] += int(statuses[0] == "infeasible")
                                if statuses[0] != statuses[1]:
                                    report["forced_mass_mismatches"].append(dict(seed=seed, rollout=rollout, depth=depth, slot=int(slot), mass=mass, statuses=statuses))
                    if not len(a):
                        break
                    action = old.decode_index(int(rng.choice(a)))
                    count = int(old.T.slot_n[old.T.slot_index[action.slot]])
                    report["singleton_pushes" if count == 1 else "pair_pushes"] += 1
                    # Mutate a sibling's shared workspace, then verify pop.
                    sibling = new.copy()
                    sibling.push(action)
                    sibling.legal_index()
                    old.push(action)
                    new.push(action)
                    old.pop()
                    new.pop()
                    if not np.array_equal(old.legal_index(), new.legal_index()):
                        report["mismatches"].append(dict(seed=seed, rollout=rollout, depth=depth, phase="pop"))
                        break
                    report["copy_pop_checks"] += 1
                    old.push(action)
                    new.push(action)
                report["witness_hits"] += new._global.witness_hits
                report["parent_witness_hits"] += new._global.parent_witness_hits
    finally:
        for mode, api in classes.items():
            api.GlobalScreen.from_instance = old_factories[mode]
    report["wall_s"] = time.perf_counter() - start
    report["maxrss_bytes_macos"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report["sources_after"] = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    report["changed_sources"] = {p: v for p, v in report["sources_after"].items() if sources[p] != v}
    (HERE / args.out).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k not in ("sources", "sources_after")}, indent=2))
    return bool(report["mismatches"] or report["forced_mass_mismatches"] or report["changed_sources"])


if __name__ == "__main__":
    raise SystemExit(main())
