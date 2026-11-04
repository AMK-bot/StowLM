"""Second calibration pass: OV hypotheses, Eq (16) floor, height pairs, buoyancy totals, hydro brackets."""
import collections, os, sys
src = open(os.path.join(os.path.dirname(__file__), "calibrate_release.py")).read().split('print("inst file load')[0]
exec(src.replace("__file__", repr(os.path.join(os.path.dirname(__file__), "calibrate_release.py"))))
ALAP = """1 23.74 -0.03 -0.10 86853
2 24.29 -0.02 -0.35 81976
3 24.25 0.10 -0.20 85368
4 23.27 0.06 -0.18 84701
5 24.43 0.06 -0.50 90352
6 24.56 0.10 -0.30 95110
7 25.04 -0.09 -0.12 89281
8 24.95 0.09 -0.46 91352
9 25.33 -0.06 -0.40 92749
10 24.73 0.09 -0.23 95330
11 23.69 0.00 -0.41 97569
12 25.03 -0.05 -0.30 106073
13 25.35 -0.01 -0.27 119359
14 26.66 -0.07 -0.41 116936
15 25.07 0.02 -0.27 119410
16 26.14 -0.03 -0.15 131316
17 26.01 0.10 -0.13 130592
18 26.51 -0.02 -0.33 129072
19 28.45 -0.05 0.27 144018
20 28.86 0.09 0.42 132076
21 28.92 0.04 0.38 148691
22 29.95 -0.07 0.05 156645
23 29.26 -0.06 0.32 164713
24 29.32 0.00 0.38 148672
25 29.38 0.00 0.30 183135
26 29.41 0.10 0.30 183017
27 29.20 0.05 0.26 181691"""
apx = {int(l.split()[0]): list(map(float, l.split()[1:])) for l in ALAP.splitlines()}
hpairs = collections.defaultdict(set)
for k in sorted(files):
    v, fn = files[k]
    ship, hydro, tanks, bays = ves[v]
    np_, nc, types, boxes = read_inst(f"{ROOT}/container_instances/Vessel_{v}/{fn}")
    rel = [b for b in boxes if len(b) > 3]; load = [b for b in boxes if len(b) == 3]
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
    h_any = h_same = h_direct = h_direct_slot = 0
    two_small = 0; tier_gap = 0; sw40_20 = 0; wviol = 0
    for kk, lst in bysec.items():
        lst.sort()
        ts = sorted(set(x[0] for x in lst))
        sec = secs[kk]
        tiers = sorted(c[0] for c in sec['cells'])
        # hole check
        pos = [tiers.index(t) for t in ts]
        if pos != list(range(len(pos))): tier_gap += 1
        nDC = 0; nHC = 0
        for t in ts:
            here = [x for x in lst if x[0] == t]
            if len(here) == 1 and types[here[0][3]][0] == 20: two_small += 1
            if len(here) == 1 and types[here[0][3]][0] == 40:
                if types[here[0][3]][2] in ('HC', 'HR'): nHC += 1
                else: nDC += 1
            else: nDC += 1
        hpairs[sec['h']].add((nDC, nHC))
        for i, (ti, sl, e, t) in enumerate(lst):
            lower = [x for x in lst if x[0] < ti]
            if any(x[2] < e for x in lower): h_any += 1
            if any(x[2] < e and x[1] == sl for x in lower): h_same += 1
            prev = [x for x in lower if x[0] == ti - 1]
            if any(x[2] < e for x in prev): h_direct += 1
            if any(x[2] < e and x[1] == sl for x in prev): h_direct_slot += 1
            # weight order per slot
            for x in prev:
                if x[1] == sl and types[x[3]][1] < types[t][1]: wviol += 1
            if types[t][0] == 20 and any(types[x[3]][0] == 40 for x in prev): sw40_20 += 1
    # Eq 16 floor counting release below
    groups = collections.defaultdict(lambda: {'A': [], 'B': []})
    for b in bays:
        for st in b['stacks']:
            for key, sec in st['secs'].items():
                groups[(b['idx'], (sec['id'] + 1) // 2)][key[0]].append((st['idx'], key))
    h16 = 0
    for g, d in groups.items():
        below = [(sidx, x[0]) for sidx, key in d['B'] for x in bysec.get((g[0], sidx, key), [])]
        above = [x for sidx, key in d['A'] for x in bysec.get((g[0], sidx, key), [])]
        if above: h16 += len(set(below))
    p = pub[k]
    print(f"{k:2d} OV pub {p['OV']:4d}: any {h_any} sameslot {h_same} direct {h_direct} directslot {h_direct_slot} | HO pub {p['HO']} h16rel {h16} | lone20 {two_small} gaps {tier_gap} 20on40 {sw40_20} wviol {wviol}")
for L in sorted(hpairs):
    s = hpairs[L]
    # pareto: max nHC for each nDC
    par = {}
    for a, b in s:
        par[a] = max(par.get(a, -1), b)
    print('height', L, sorted(par.items()))
for v in 'SML':
    ship, hydro, tanks, bays = ves[v]
    print(v, 'buoy sum vs disp', [(round(sum(b['buoy'][i] for b in bays)), hydro[i][0]) for i in (0, 5, len(hydro)-1)])
    print(v, 'hydro', [(h[0], h[1], h[2], h[3]) for h in hydro])
