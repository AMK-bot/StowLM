# Provenance: copied from experiments/mvp/frontier/lab/c1/fx-scout/lash_test.py.
def main():
    from pathlib import Path
    from collections import defaultdict
    import json, sys, time
    ROOT = Path(__file__).resolve().parents[5]
    sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier'), str(Path(__file__).parent)]
    from alns import Problem, Sequencer
    from stowage.checker.lashing import check_lashing
    from stowage.conventions import cells_of_slot
    from stowage.io import load_instance
    from stowage.moves import apply_plan
    from .lash_oracle import Oracle
    i = load_instance(ROOT/sys.argv[1]); p = Problem(i)
    a = json.loads((Path(__file__).parent/sys.argv[2]).read_text())
    s = p.score(Sequencer(p).order(a))
    ship = apply_plan(i, s.plan).final_state.ship
    r = check_lashing(i, ship, call_index=i.call_index)
    print('full metrics', {k: round(v, 3) for k, v in r.metrics.items() if 'ratio' in k})
    o = Oracle(i, ship)
    groups = defaultdict(dict)
    for b, sl in a.items():
        groups[sl[:5] if sl[3:5] != '' else sl][b] = sl
    full = defaultdict(float)
    for v in r.violations:
        for sl in v.slots:
            if v.measured: full[sl[:5]] = max(full[sl[:5]], v.measured)
    t = time.time(); rows = []
    for k, g in groups.items():
        rows.append((k, round(o.ratio(g), 3), round(full.get(k, 0), 3)))
    print('per pattern s', (time.time()-t)/len(groups))
    print(sorted(rows, key=lambda x: -x[1])[:16])
    
    from .lash_oracle import Fast
    f = Fast(i, ship)
    t = time.time(); rows = []
    for k, g in groups.items():
        if k[3:5] == k[3:5] and all(sl[5] in '789' for sl in g.values()):
            order = sorted(g, key=lambda b: g[b])
            rows.append((k, round(o.ratio(g), 3), round(f.ratio(order, [g[b] for b in order]), 3)))
    print(sorted(rows, key=lambda x: -x[1])[:20])
    print('fast s', (time.time()-t))
    import numpy as np
    e = np.array([r[2]-r[1] for r in rows]); print('fast-exact mean', e.mean(), 'max', e.max(), 'min', e.min())


if __name__ == "__main__":
    main()
