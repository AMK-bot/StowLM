"""FB port numbering floors and VM formula candidates, against Table 2."""
import collections, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import evaluator as ev, published as pub
ROOT = "/Users/anda/Projects/meta-hackathon-stowage/data/raw/larsen-pacino"
plans = {1: sys.argv[1]} if len(sys.argv) > 1 else {}
print('inst FBpub | FBrel, nrelhold, FBrel+n | FB max with load: all load in hold (0-based) (1-based) | VM light+rel(z) ')
for k in range(1, 28):
    v = ev.read_vessel(pub.path_vessel(ROOT, k)); I = ev.read_instance(pub.path_instance(ROOT, k))
    fb = n = 0
    light = sum(b.const_vcg*b.const_w for b in v.bays)
    z = zc = 0
    for b in I.release:
        bay, st, t, sl = b.pos
        s = v.by_cell[(bay, st, t)]
        i = s.tiers.index(t)
        if not s.deck: fb += b.end; n += 1
        z += b.weight * (s.vcg + i * 2.61 + 1.3)
    p = pub.TABLE2[k-1]
    a2 = pub.APPENDIX2[k-1]
    wload = a2['disp'] - sum(b.const_w for b in v.bays) - sum(b.weight for b in I.release)
    fbload = sum(b.end for b in I.load); nload = len(I.load)
    print(f"{k:2d} FBpub {p['FB']:6d} | rel {fb} n {n} rel+n {fb+n} | load0 {fbload} n {nload} | frac0 {(p['FB']-fb)/fbload:.2f} frac1 {(p['FB']-fb-n)/(fbload+nload):.2f} | VMpub {p['VM']:.3g} light+relz {light+z:.3g} implied load z {(p['VM']-light-z)/max(wload,1):.1f}")
if plans:
    k = 1
    v = ev.read_vessel(pub.path_vessel(ROOT, k)); I = ev.read_instance(pub.path_instance(ROOT, k))
    z = 0; light = sum(b.const_vcg*b.const_w for b in v.bays)
    items = [(b, b.pos) for b in I.release] + [(I.boxes[i], p) for i, p in ev.read_plan(plans[k])]
    for b, (bay, st, t, sl) in items:
        s = v.by_cell[(bay, st, t)]
        z += b.weight * (s.vcg + s.tiers.index(t) * 2.61 + 1.3)
    print('plan VM tier model: cargo', round(z), 'light+cargo', round(z + light), 'pub', pub.TABLE2[k-1]['VM'], 'A2 total', pub.APPENDIX2[k-1]['vcg']*pub.APPENDIX2[k-1]['disp'])
