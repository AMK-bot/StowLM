"""Why do L1/L2 dead ends happen? For dead states of random rollouts: per remaining box, which check removes each open
slot (first failing check in the L2 fast path). Diagnostic for the 4.4 completion-strength study."""
import json, random, sys
from collections import Counter
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.io import load_instance

def why(e):
    st, T = e._cur, e.T
    O = e._open_slots(st)
    lift = e._liftable(st)
    rem = np.flatnonzero(st.rem)
    room, hroom, caprank, nh, bad = e._slot_features(st, O)
    out = Counter()
    per_box = {}
    for b in rem:
        if not lift[b]:
            out["not liftable"] += 1; per_box[T.box_ids[b]] = "not liftable"; continue
        checks = [("static", T.static[b, O]), ("bad(closed/20on40/poison)", ~bad),
                  ("stack mass", T.share[b] <= room + 1e-6), ("height", T.height[b] <= hroom + 1e-9),
                  ("E2 class", T.rank[b] <= caprank),
                  ("E1", ~((T.mass[b] >= (T.e1_threshold or np.inf)) & nh))]
        alive = np.ones(len(O), bool); last = None
        for name, m in checks:
            if (alive & m).sum() == 0 and alive.sum() > 0:
                last = name
            alive &= m
        if alive.any():
            last = "DG/special/heat or C1/C2 (passes fast L2)"
        out[last] += 1
        b_ = e._boxes[T.box_ids[b]]
        per_box[T.box_ids[b]] = (last, round(b_.gross_mass_kg), b_.length_ft, b_.dg is not None, b_.reefer,
                                 list(map(str, b_.special_stowage)))
    return out, per_box

if __name__ == "__main__":
    gen, rung, n, k = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    sp = get_action_space(); tot = Counter(); examples = []
    for path in sorted((ROOT / f"experiments/feasibility/fresh/{gen}/{rung}").glob("seed-*.json"))[:n]:
        inst = load_instance(path)
        for r in range(k):
            e = make_feasibility_engine(inst, sp, config=FeasibilityConfig(extra={"global_screen": 0}))
            rng = random.Random(r)
            while not e.is_complete():
                a = e.legal_actions()
                if not a: break
                e.push(rng.choice(a))
            if e.is_complete(): continue
            o, pb = why(e); tot.update(o)
            if len(examples) < 4: examples.append((path.name, e.depth, e.dead_end().reason[-60:], dict(o), list(pb.items())[:6]))
    print(dict(tot))
    for x in examples: print(x)
