"""Where do the load boxes go? Splits of a plan by hold/deck, HO source, free cells; and VM candidates."""
import collections, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import evaluator as ev, published as pub
ROOT = "/Users/anda/Projects/meta-hackathon-stowage/data/raw/larsen-pacino"
k = int(sys.argv[1]); plan = ev.read_plan(sys.argv[2]) if len(sys.argv) > 2 else []
v = ev.read_vessel(pub.path_vessel(ROOT, k)); I = ev.read_instance(pub.path_instance(ROOT, k))
rel_h = rel_d = 0; relw_h = relw_d = 0
cells_h = sum(len(s.tiers) for s in v.sections if not s.deck); cells_d = sum(len(s.tiers) for s in v.sections if s.deck)
occ = collections.Counter()
top = {}
for b in I.release:
    bay, st, t, sl = b.pos
    s = v.by_cell[(bay, st, t)]
    occ[id(s)] = max(occ[id(s)], s.tiers.index(t) + 1)
    if s.deck: rel_d += 1; relw_d += b.weight
    else: rel_h += 1; relw_h += b.weight
freeh = sum(len(s.tiers) - occ[id(s)] for s in v.sections if not s.deck)
freed = sum(len(s.tiers) - occ[id(s)] - (1 if True else 0) for s in v.sections if s.deck)
print('cells hold', cells_h, 'deck', cells_d, 'release boxes hold', rel_h, relw_h, 'deck', rel_d, relw_d, 'free hold cells', freeh, 'free deck cells (n-1 usable)', freed)
lh = ld = 0; fbl = 0
for idx, (bay, st, t, sl) in plan:
    s = v.by_cell[(bay, st, t)]; b = I.boxes[idx]
    if s.deck: ld += 1
    else: lh += 1; fbl += b.end
print('load boxes placed hold', lh, 'deck', ld, 'FB load', fbl, 'load total', len(I.load))
# HO split
hatches = collections.defaultdict(lambda: ([], []))
for s in v.sections: hatches[s.hatch][0 if s.deck else 1].append(s)
by_sec = collections.defaultdict(list)
for b in I.release:
    bay, st, t, sl = b.pos
    by_sec[id(v.by_cell[(bay, st, t)])].append((b, t, False))
for idx, (bay, st, t, sl) in plan:
    by_sec[id(v.by_cell[(bay, st, t)])].append((I.boxes[idx], t, True))
h15r = h15l = h16 = 0
for key, (deck, hold) in hatches.items():
    hb = [x for s in hold for x in by_sec[id(s)]]
    db = [x for s in deck for x in by_sec[id(s)]]
    if hb:
        mn = min(x[0].end for x in hb)
        cells = collections.defaultdict(list)
        for s in deck:
            for x in by_sec[id(s)]:
                cells[(id(s), x[1])].append(x)
        for c, xs in cells.items():
            if any(x[0].end > mn for x in xs):
                if any(x[2] for x in xs): h15l += 1
                else: h15r += 1
        if any(not x[2] for x in db):
            h16 += len({(id(s), x[1]) for s in hold for x in by_sec[id(s)] if x[2]})
print('HO15 load-cells', h15l, 'release-cells', h15r, 'HO16', h16)
# VM candidates using a release-heavy instance
for kk in (24, 9, 18, 7):
    v2 = ev.read_vessel(pub.path_vessel(ROOT, kk)); I2 = ev.read_instance(pub.path_instance(ROOT, kk))
    a2 = pub.APPENDIX2[kk-1]; light_vm = sum(b.const_vcg*b.const_w for b in v2.bays)
    target = a2['vcg'] * a2['disp'] - light_vm
    w_rel = sum(b.weight for b in I2.release); w_tot = a2['disp'] - sum(b.const_w for b in v2.bays)
    out = {}
    for name, f in (('base', lambda s, i, b: s.vcg), ('i261', lambda s, i, b: s.vcg + i*2.61), ('i261+1.3', lambda s, i, b: s.vcg + i*2.61 + 1.3), ('i261+half', lambda s, i, b: s.vcg + i*2.61 + b.height/2)):
        vm = 0
        for b in I2.release:
            bay, st, t, sl = b.pos
            s = v2.by_cell[(bay, st, t)]
            vm += b.weight * f(s, s.tiers.index(t), b)
        out[name] = round(vm)
        out[name + '_loadz'] = round((target - vm) / max(1, w_tot - w_rel), 2)
    print(kk, 'target cargo VM', round(target), 'pub', pub.TABLE2[kk-1]['VM'], 'w_load', w_tot - w_rel, out)
