"""Release-only KPI floors per instance, against Table 2 and Appendix II. Used to decide
which KPI definitions (release included or not, per cell or per slot) fit the published
numbers, and which limit forms the release stacks satisfy."""
import collections, os, sys
sys.path.insert(0, os.path.dirname(__file__))
ROOT = "/Users/anda/Projects/meta-hackathon-stowage/data/raw/larsen-pacino"
exec(open(os.path.join(os.path.dirname(__file__), "explore_data.py")).read().split("for v in 'SML':")[0])

def read_inst(path):
    mode = None; types = {}; boxes = []; np_ = None
    for line in open(path):
        s = line.strip()
        if not s: continue
        if s.startswith('#'):
            mode = s; continue
        f = s.split()
        if mode.startswith('# Parameters'): np_, nc = int(f[0]), int(f[1])
        elif mode.startswith('# Transport'): types[int(f[0])] = (int(f[1]), float(f[2]), f[3])
        else: boxes.append(list(map(int, f)))
    return np_, nc, types, boxes

T2 = """1 40609.61 1 81 291 97 167 381 389 13009 1.52E+06
2 46749.47 1 65 371 125 149 332 357 10639 1.45E+06
3 54414.43 0 95 419 65 203 350 448 11863 1.53E+06
4 29306.02 1 97 162 128 246 344 396 11126 1.43E+06
5 72428.63 0 93 573 25 191 398 536 9838 1.67E+06
6 113179.96 7 165 838 12 167 434 602 12073 1.79E+06
7 81919.94 0 207 557 25 258 422 529 11485 1.69E+06
8 149884.30 40 194 841 3 289 438 592 11537 1.74E+06
9 273579.36 156 139 966 12 162 416 605 8977 1.81E+06
10 17011.68 1 11 144 190 170 314 479 13212 1.73E+06
11 20369.48 0 92 115 215 210 354 407 15347 1.68E+06
12 40246.87 0 63 318 112 198 384 587 15497 2.02E+06
13 138880.51 1 60 1265 55 348 477 619 14584 2.40E+06
14 181337.19 1 295 1447 8 268 510 739 16389 2.49E+06
15 75904.72 1 80 641 89 416 431 532 16475 2.36E+06
16 497520.59 280 93 2014 22 267 540 754 16153 2.80E+06
17 488933.06 300 52 1772 27 300 493 702 14287 2.77E+06
18 667474.49 438 444 1767 0 259 569 738 14467 2.79E+06
19 39330.37 0 105 283 218 217 517 680 23094 3.00E+06
20 435385.76 386 90 416 316 255 459 718 22702 2.72E+06
21 246178.57 140 207 816 263 370 481 892 16524 3.21E+06
22 126875.23 0 304 912 59 329 586 872 21807 3.60E+06
23 112520.97 1 150 934 91 269 571 800 24061 3.72E+06
24 213497.54 130 164 653 182 71 492 821 21450 3.27E+06
25 701912.65 476 13 2224 159 671 585 879 26784 4.29E+06
26 336175.82 80 68 2437 74 437 665 857 24070 4.29E+06
27 215336.12 1 101 1992 75 422 657 810 24494 4.21E+06"""
T1 = {1:(1953,433,760),2:(1447,231,608),3:(1528,204,662),4:(2399,401,999),5:(2060,310,875),6:(1791,197,797),7:(1634,290,672),8:(2403,479,962),9:(1098,310,394),
10:(1519,279,620),11:(2206,346,930),12:(2120,374,873),13:(3399,607,1396),14:(3613,657,1478),15:(5273,805,2234),16:(3590,380,1605),17:(3350,354,1498),18:(3136,286,1425),
19:(2486,434,1026),20:(1931,335,798),21:(3039,599,1220),22:(3920,570,1675),23:(3673,651,1511),24:(971,273,349),25:(8365,1277,3544),26:(4896,974,1961),27:(4745,917,1914)}
A2 = {1:86853,2:81976,3:85368,4:84701,5:90352,6:95110,7:89281,8:91352,9:92749,10:95330,11:97569,12:106073,13:119359,14:116936,15:119410,16:131316,17:130592,18:129072,19:144018,20:132076,21:148691,22:156645,23:164713,24:148672,25:183135,26:183017,27:181691}
pub = {}
for l in T2.splitlines():
    f = l.split(); pub[int(f[0])] = dict(obj=float(f[1]), UL=int(f[2]), OV=int(f[3]), HO=int(f[4]), ES=int(f[5]), MK=int(f[6]), BP=int(f[7]), NR=int(f[8]), FB=int(f[9]), VM=float(f[10]))

files = {}
for v in 'SML':
    d = f"{ROOT}/container_instances/Vessel_{v}"
    for fn in sorted(os.listdir(d)):
        np_, nc, types, boxes = read_inst(f"{d}/{fn}")
        load = [b for b in boxes if len(b) == 3]
        n20 = sum(1 for b in load if types[b[2]][0] == 20); n40 = len(load) - n20
        for k, (teu, a, b_) in T1.items():
            if (a, b_) == (n20, n40) and k not in files and ((k-1)//9) == 'SML'.index(v): files[k] = (v, fn)
print('mapping', {k: files[k] for k in sorted(files)})

ves = {v: read_vessel(v) for v in 'SML'}
print("inst file load | disp_est A2 | ES_rel/pub | BPrel/pub | NRrel/pub | FBrel/pub FBall | MKrel | VMrel_base VM_pub | VM0 VMt/pub | rel lcg")
for k in sorted(files):
    v, fn = files[k]
    ship, hydro, tanks, bays = ves[v]
    np_, nc, types, boxes = read_inst(f"{ROOT}/container_instances/Vessel_{v}/{fn}")
    rel = [b for b in boxes if len(b) > 3]; load = [b for b in boxes if len(b) == 3]
    light = sum(b['cw'] for b in bays)
    wr = sum(types[b[2]][1] for b in rel); wl = sum(types[b[2]][1] for b in load)
    # index
    stk = {}
    for b in bays:
        for st in b['stacks']:
            for key, sec in st['secs'].items():
                for (t, r) in sec['cells']:
                    stk[(b['idx'], st['idx'], t)] = (sec, st, key, r)
    used = set(); blk = collections.defaultdict(set); nr = 0; fb_rel = 0; vm_base = 0; vm_tier = 0; lm = 0; tm = 0
    baycnt = collections.Counter()
    cells = collections.defaultdict(list)
    missing = 0
    for b in rel:
        s, e, t, by, si, ti, sl = b
        if (by, si, ti) not in stk:
            missing += 1; continue
        sec, st, key, r = stk[(by, si, ti)]
        cells[(by, si, ti)].append(b)
        used.add((by, si, key))
        blk[(by, sec['id'])].add(e)
        w = types[t][1]; ty = types[t][2]
        if key == 'BelowDeck': fb_rel += e
        vm_base += sec['vcg'] * w
        tiers = sorted(c[0] for c in sec['cells'])
        vm_tier += (sec['vcg'] + tiers.index(ti) * 2.61 + 1.3) * w
        lm += bays[by]['lcg'] * w; tm += st['tcg'] * w
        baycnt[by] += 1
    # NR per cell
    nrcell = 0; nrslot = 0
    for c, bx in cells.items():
        sec, st, key, r = stk[c]
        if r > 0:
            nonr = [b for b in bx if types[b[2]][2] not in ('RC', 'HR')]
            nrslot += len(nonr); nrcell += 1 if nonr else 0
    rf20 = sum(1 for b in load if types[b[2]][2] in ('RC', 'HR'))
    allfb = sum(b[1] for b in rel) + sum(b[1] for b in load)
    p = pub[k]
    print(f"{k:2d} {fn[:-4]:8s} d_est={light+wr+wl-0:8.0f} A2={A2[k]} ULw? | ES {sum(1 for b in bays for st in b['stacks'] for sec in st['secs'])-len(used)}/{p['ES']} | BP {sum(len(x) for x in blk.values())}/{p['BP']} | NR c{nrcell} s{nrslot}/{p['NR']} | FB {fb_rel}/{p['FB']} all {allfb} load {sum(b[1] for b in load)}| rmk {max(baycnt[b]+baycnt[b+1] for b in range(len(bays)-1))}/{p['MK']} | VM base {sum(b['cw']*b['cvcg'] for b in bays)+vm_base:.3g} rel-only/ {p['VM']:.3g} missing={missing}")

print("\ninst | OV floors (release in same section) slot/cell, across deck | HO15 rel-only cells | HO15 slots | pub OV HO | violations of stack limits")
for k in sorted(files):
    v, fn = files[k]
    ship, hydro, tanks, bays = ves[v]
    np_, nc, types, boxes = read_inst(f"{ROOT}/container_instances/Vessel_{v}/{fn}")
    rel = [b for b in boxes if len(b) > 3]
    cells = collections.defaultdict(list)
    sec_of = {}
    secs = {}
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
    ov_slot = ov_cell = 0
    for kk, lst in bysec.items():
        lst.sort()
        ovc = set()
        for i, (ti, sl, e, t) in enumerate(lst):
            if any(x[0] < ti and x[2] < e for x in lst):
                ov_slot += 1; ovc.add(ti)
        ov_cell += len(ovc)
    # hatch blocks
    groups = collections.defaultdict(lambda: {'A': [], 'B': []})
    for b in bays:
        for st in b['stacks']:
            for key, sec in st['secs'].items():
                gid = (sec['id'] + 1) // 2
                groups[(b['idx'], gid)][key[0]].append((st['idx'], key))
    ho_c = ho_s = 0
    rel_above_under = 0
    for g, d in groups.items():
        below = [x for sidx, key in d['B'] for x in bysec.get((g[0], sidx, key), [])]
        above = [x for sidx, key in d['A'] for x in bysec.get((g[0], sidx, key), [])]
        if below:
            mn = min(x[2] for x in below)
            c = set()
            for x in above:
                if x[2] > mn:
                    ho_s += 1
            for sidx, key in d['A']:
                for x in bysec.get((g[0], sidx, key), []):
                    if x[2] > mn: c.add((sidx, x[0]))
            ho_c += len(c)
    # stack limits
    viol = collections.Counter(); ratio = collections.Counter()
    mx = {}
    for kk, lst in bysec.items():
        sec = secs[kk]
        w20f = sum(types[x[3]][1] for x in lst if types[x[3]][0] == 20 and x[1] == 1)
        w20a = sum(types[x[3]][1] for x in lst if types[x[3]][0] == 20 and x[1] == 2)
        w40 = sum(types[x[3]][1] for x in lst if types[x[3]][0] == 40)
        h = sum((2.9 if types[x[3]][2] in ('HC', 'HR') else 2.59) * (0.5 if types[x[3]][0] == 20 else 1) for x in lst)
        e7 = 0.5 * (w20f + w20a) + w40
        e8 = w20f + w20a + 0.5 * w40
        e8s = max(w20f, w20a) + 0.5 * w40
        for name, val, lim in (('e7', e7, sec['w40']), ('e8', e8, sec['w20']), ('e8slot', e8s, sec['w20']), ('h', h, sec['h'])):
            mx[name] = max(mx.get(name, 0), val / lim)
            viol[name] += val > lim + 1e-9
    p = pub[k]
    print(f"{k:2d} OVslot {ov_slot} OVcell {ov_cell} / pub OV {p['OV']} | HOcell {ho_c} HOslot {ho_s} / pub HO {p['HO']} | viol {dict(viol)} maxratio { {a: round(b, 3) for a, b in mx.items()} }")
