"""Third pass: weight-order violations by pair type in release stacks; OV variants; height fits."""
import collections, os, sys
src = open(os.path.join(os.path.dirname(__file__), "calibrate_release.py")).read().split('print("inst file load')[0]
exec(src.replace("__file__", repr(os.path.join(os.path.dirname(__file__), "calibrate_release.py"))))
tot = collections.Counter(); totpairs = collections.Counter()
rows = []
for k in sorted(files):
    v, fn = files[k]
    ship, hydro, tanks, bays = ves[v]
    np_, nc, types, boxes = read_inst(f"{ROOT}/container_instances/Vessel_{v}/{fn}")
    rel = [b for b in boxes if len(b) > 3]
    sec_of = {}; secs = {}
    for b in bays:
        for st in b['stacks']:
            for key, sec in st['secs'].items():
                secs[(b['idx'], st['idx'], key)] = sec
                for (t, r) in sec['cells']:
                    sec_of[(b['idx'], st['idx'], t)] = (key, sec)
    bysec = collections.defaultdict(list)
    for r in rel:
        s, e, t, by, si, ti, sl = r
        key, sec = sec_of[(by, si, ti)]
        bysec[(by, si, key)].append((ti, sl, e, t))
    var1 = 0
    for kk, lst in bysec.items():
        lst.sort()
        ts = sorted(set(x[0] for x in lst))
        for i, t in enumerate(ts):
            here = [x for x in lst if x[0] == t]
            for x in here:
                L = types[x[3]][0]; W = types[x[3]][1]; d = x[2]
                # variant: 40' covers both slots when compared; 20' compares to same slot or to 40' below
                lower = [y for y in lst if y[0] < t]
                bad = False
                for y in lower:
                    if y[2] >= d: continue
                    Ly = types[y[3]][0]
                    if L == 40 or Ly == 40 or y[1] == x[1]:
                        bad = True
                var1 += bad
            if i:
                prev = [x for x in lst if x[0] == ts[i-1]]
                for x in here:
                    for y in prev:
                        lx = types[x[3]][0]; ly = types[y[3]][0]
                        key2 = f"{lx}on{ly}"
                        same = (x[1] == y[1])
                        if lx == 20 and ly == 20 and not same: continue
                        if lx == 40 and ly == 20 and not same:
                            key2 += "_fore"
                        totpairs[key2] += 1
                        if types[x[3]][1] > types[y[3]][1]:
                            tot[key2] += 1
    # sum rule check for 40 on 20 pair
    rows.append((k, var1, pub[k]['OV']))
print('weight violations by pair', dict(tot), 'of', dict(totpairs))
print('OV variant (40 covers both):', rows)
print('fails', [r for r in rows if r[1] > r[2]])
