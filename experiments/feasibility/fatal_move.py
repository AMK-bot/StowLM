"""Find the fatal move of a dead-end rollout (4.4 diagnostic): the last ancestor state from which a completion still
exists (exhaustive DFS through the screened engine, restow-free, node-capped), and the action that left it."""
import random, sys, time
from pathlib import Path
import numpy as np
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.io import load_instance

def completable(e, budget):
    """DFS (restow-free first, then all); returns True/False/None (budget)."""
    n = [0]
    def rec():
        if e.is_complete():
            return True
        n[0] += 1
        if n[0] > budget:
            return None
        acts = e.legal_actions()
        acts = sorted(acts, key=lambda a: isinstance(a, Place) and e.forces_restow(a))
        unknown = False
        for a in acts:
            if isinstance(a, Place) and e.forces_restow(a):
                continue
            e.push(a)
            r = rec()
            e.pop()
            if r:
                return True
            if r is None:
                unknown = True
                if n[0] > budget:
                    return None
        return None if unknown else False
    return rec()

if __name__ == "__main__":
    gen, rung, n_inst = sys.argv[1], sys.argv[2], int(sys.argv[3])
    sp = get_action_space()
    for path in sorted(Path(f"experiments/feasibility/fresh/{gen}/{rung}").glob("seed-*.json"))[:n_inst]:
        inst = load_instance(path)
        boxes = {b.id: b for b in inst.containers}
        for r in range(3):
            e = make_feasibility_engine(inst, sp, config=FeasibilityConfig(extra={"global_screen": 0}))
            rng = random.Random(r); hist = []
            while not e.is_complete():
                a = e.legal_actions()
                if not a: break
                cheap = [x for x in a if not (isinstance(x, Place) and e.forces_restow(x))] or a
                x = rng.choice(cheap); hist.append(x); e.push(x)
            if e.is_complete():
                continue
            # walk back
            t = time.time(); verdict = None
            while e.depth > 0:
                last = e.pop()
                c = completable(e.copy(), 20000)
                if c is not False:
                    verdict = (c, last)
                    break
            if verdict:
                c, last = verdict
                b = boxes[last.box]
                print(path.name, r, "fatal at depth", e.depth, "of", len(hist), "completable" if c else "unknown",
                      last, dict(m=round(b.gross_mass_kg), L=b.length_ft, dg=b.dg.imdg_class if b.dg else None,
                                 reefer=b.reefer, sp=[str(x) for x in b.special_stowage]),
                      "%.1fs" % (time.time() - t), flush=True)
