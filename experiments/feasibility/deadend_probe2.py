"""Finer dead-end attribution (4.4): for each remaining box at a dead state, after the fast L2 filters, which
of DG segregation / heat / E9 envelope / C1 sub-check / last-box E6 removes its last candidate."""
import random, sys
from collections import Counter
from pathlib import Path
import numpy as np
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.io import load_instance

def attribute(e):
    st, T = e._cur, e.T
    O = e._open_slots(st); lift = e._liftable(st)
    room, hroom, caprank, nh, bad = e._slot_features(st, O)
    out = Counter()
    for b in np.flatnonzero(st.rem):
        if not lift[b]: out["not liftable"] += 1; continue
        m = T.static[b, O] & ~bad & (T.share[b] <= room + 1e-6) & (T.height[b] <= hroom + 1e-9) & (T.rank[b] <= caprank)
        if T.e1_threshold is not None: m &= ~((T.mass[b] >= T.e1_threshold) & nh)
        if not m.any(): out["fast L2"] += 1; continue
        slots = O[m]
        if T.is_dg[b] and st.dg_aboard:
            okd = np.array([e._dg_ok(int(T.dg_class[b]), int(s), st.dg_aboard) for s in slots])
            if not okd.any(): out["DG segregation (L2)"] += 1; continue
            slots = slots[okd]
        bb = np.full(len(slots), b)
        if T.heat[b]:
            okh = np.array([e._heat_ok(st, b, int(s)) for s in slots])
            if not okh.any(): out["heat"] += 1; continue
            slots = slots[okh]; bb = bb[:len(slots)]
        e._c1(st, bb, slots)
        f = {k: v for k, v in e._c1_fails.items() if v}
        out["C1: " + "+".join(sorted(f)) if f else "C1/E9/other"] += 1
    return out

if __name__ == "__main__":
    gen, rung, n, k = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    sp = get_action_space(); tot = Counter()
    for path in sorted(Path(f"experiments/feasibility/fresh/{gen}/{rung}").glob("seed-*.json"))[:n]:
        inst = load_instance(path)
        for r in range(k):
            e = make_feasibility_engine(inst, sp, config=FeasibilityConfig(extra={"global_screen": 0})); rng = random.Random(r)
            while not e.is_complete():
                a = e.legal_actions()
                if not a: break
                e.push(rng.choice(a))
            if not e.is_complete(): tot.update(attribute(e))
    print(gen, rung, dict(tot.most_common()))
