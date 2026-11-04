"""Print KPIs and the shear/bending profile of a plan against limits, under the candidate bending readings."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import evaluator as ev, published as pub
ROOT = "/Users/anda/Projects/meta-hackathon-stowage/data/raw/larsen-pacino"
k = int(sys.argv[1]); plan = sys.argv[2]
v = ev.read_vessel(pub.path_vessel(ROOT, k)); I = ev.read_instance(pub.path_instance(ROOT, k))
r = ev.evaluate(v, I, ev.read_plan(plan))
print({a: r[a] for a in ('obj','UL','HO','OV','MK','BP','NR','ES','FB','VM','feasible','violations','notes','strict_eq11_40on20','disp','lcg','tcg','lm','tm','lm_bounds')})
print('pub', pub.TABLE2[k-1])
print('bay  lcg  shear [min,max] | printed-cum  lever*shear physical | maxB')
for b, p in zip(v.bays, r['profile']):
    lever = abs(v.bays[0].lcg - b.lcg)
    print(f"{b.idx:2d} {b.lcg:7.1f} S={p[0]:8.0f} [{b.min_shear:6.0f},{b.max_shear:6.0f}] cum={p[1]:10.0f} lev*S={lever*p[0]:10.0f} phys={p[2]:10.0f} maxB={b.max_bend:8.0f}")
