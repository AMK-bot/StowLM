"""Do candidate box heights reproduce the (tiers, high-cubes) patterns seen in release stacks?"""
import collections, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import evaluator as ev
ROOT = "/Users/anda/Projects/meta-hackathon-stowage/data/raw/larsen-pacino"
obs = collections.defaultdict(set); ncell = {}
sum40on20 = collections.Counter()
for v in 'SML':
    V = ev.read_vessel(f"{ROOT}/vessel_data/vessel_{v}.txt")
    d = f"{ROOT}/container_instances/Vessel_{v}"
    for fn in sorted(os.listdir(d)):
        I = ev.read_instance(f"{d}/{fn}")
        secs = collections.defaultdict(lambda: collections.defaultdict(list))
        for b in I.release:
            bay, st, t, sl = b.pos
            secs[id(V.by_cell[(bay, st, t)])][t].append(b)
        for sec in V.sections:
            ncell[sec.hmax] = max(ncell.get(sec.hmax, 0), len(sec.tiers))
            m = secs.get(id(sec))
            if not m: continue
            nd = nh = 0
            for t in sorted(m):
                bs = m[t]
                if bs[0].length == 40 and bs[0].kind in ('HC', 'HR'): nh += 1
                else: nd += 1
            obs[sec.hmax].add((nd, nh))
            ts = sorted(m)
            for i in range(1, len(ts)):
                up = m[ts[i]]; dn = m[ts[i-1]]
                if up[0].length == 40 and dn[0].length == 20:
                    sum40on20['n'] += 1
                    sum40on20['gt_sum'] += up[0].weight > sum(x.weight for x in dn)
                    sum40on20['gt_max'] += up[0].weight > max(x.weight for x in dn)
                    sum40on20['half_gt_min'] += up[0].weight/2 > min(x.weight for x in dn)
                    sum40on20['pairs2'] += len(dn)==2
print('40 on 20 pairs', sum40on20)
for hd, hh in [(2.59, 2.90), (2.61, 2.92), (2.62, 2.92), (2.62, 2.93), (2.6213, 2.9261), (2.63, 2.93), (2.615, 2.92)]:
    miss = extra = 0; bad = []
    for L, s in sorted(obs.items()):
        n = ncell[L]
        allowed = {(a, b) for a in range(n + 1) for b in range(n + 1 - a) if a * hd + b * hh <= L + 1e-9}
        # observed patterns must be allowed; allowed maximal patterns should be observed
        miss_here = [x for x in s if x not in allowed]
        miss += len(miss_here)
        maxallowed = {x for x in allowed if (x[0] + 1, x[1]) not in allowed and (x[0], x[1] + 1) not in allowed and (x[0]-1, x[1] + 1) not in allowed}
        never = [x for x in maxallowed if x not in s]
        extra += len(never)
        if miss_here or never: bad.append((L, miss_here, never))
    print((hd, hh), 'observed-but-forbidden', miss, 'allowed-maximal-never-seen', extra, bad[:4])
