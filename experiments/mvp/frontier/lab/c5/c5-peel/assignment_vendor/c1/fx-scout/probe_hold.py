"""Probe: rebuild a map with the hold restricted, finish, check. Shows what really stops fewer hatch lifts."""
from pathlib import Path; import sys, random, time, json
ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier')]
from stowage.io import load_instance
from stowage.conventions import parse_slot, cells_of_slot
from stowage.actions.space import Place
from stowage.actions.expander import Expander
from alns import Problem, Rebuilder, Sequencer, _mask_ok
from planner import _finish

class R(Rebuilder):
    def __init__(self, p, rng, allowed): super().__init__(p, rng); self.allowed = allowed
    def build(self, *, noise, deadline):
        p, engine = self.p, self.p.engine
        pushed = 0
        try:
            ex = Expander(p.instance); placed = []; out = []; pending = set(p.instance.load_list)
            while pending:
                if time.monotonic() >= deadline: return None, 'time'
                best = None
                occ = {c for _, s in placed for c in cells_of_slot(s)}
                for box in engine.legal_box_ids():
                    if box not in pending: continue
                    for slot in engine.legal_slots_for_box(box):
                        if occ.intersection(cells_of_slot(slot)) or not self.allowed(box, slot): continue
                        v = self._estimate(ex, placed, box, slot, noise)
                        if best is None or v < best[0]: best = (v, box, slot)
                if best is None: return None, f'stuck {len(pending)} left of {len(p.instance.load_list)}'
                a = Place(best[1], best[2]); engine.push(a); pushed += 1; ex.step(a); placed.append((a.box, a.slot)); out.append(a); pending.discard(a.box)
            return tuple(out), 'ok'
        finally:
            for _ in range(pushed): engine.pop()

path = sys.argv[1]
i = load_instance(ROOT/path); p = Problem(i)
covers = sorted(c.id for c in i.vessel.hatch_covers)
def cover(slot): return p.stacks[p.cells[cells_of_slot(slot)[0]].stack_id].hatch_cover_id
lastport = max(p.pod(b) for b in i.load_list)
tests = {'deck_only': lambda b, s: parse_slot(s).on_deck}
for c in covers:
    tests[f'hold_only_{c}'] = (lambda c: lambda b, s: parse_slot(s).on_deck or cover(s) == c)(c)
    tests[f'hold_only_{c}_late'] = (lambda c: lambda b, s: parse_slot(s).on_deck or (cover(s) == c and p.pod(b) == lastport))(c)
for name, allowed in tests.items():
    best = None; why = {}
    t0 = time.time()
    for k in range(int(sys.argv[2]) if len(sys.argv) > 2 else 12):
        r = R(p, random.Random(k), allowed)
        out, status = r.build(noise=0. if k == 0 else 1.5, deadline=time.monotonic()+20)
        why[status] = why.get(status, 0)+1
        if out is None: continue
        a = {x.box: x.slot for x in out}
        if p.mixes_hold_stack(a): why['mix'] = why.get('mix', 0)+1; continue
        o = Sequencer(p).order(a)
        if o is None or not _mask_ok(p, o): why['seq'] = why.get('seq', 0)+1; continue
        sc = p.score(o)
        if sc is None: why['score'] = why.get('score', 0)+1; continue
        if best is None or sc.objective < best.objective: best = sc
    print(name, why, 'best', best and best.objective, round(time.time()-t0, 1), flush=True)
    if best is not None:
        r, row = _finish(i, best.plan, seed=0, finalize_s=60, label=name)
        print('   finish', {k: row.get(k) for k in ('finalize_status', 'finalize_s', 'passed', 'weighted', 'violations', 'message')}, flush=True)
