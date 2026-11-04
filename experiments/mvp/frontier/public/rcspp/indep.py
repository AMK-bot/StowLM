"""Independent RCSPP evaluator, written from RCSPP_model.pdf and RCSPPSuite.pdf at slot level (fore/aft half slots, 40s as two halves).

Shares no code with rcspp_io or check_rcspp. Reads the XML itself. Written by the reviewer, who had read check_rcspp first, so it is independent code but not a blind reading.
"""
import bisect
import collections
import json
import re
import xml.etree.ElementTree as ET

RHO = 1.025
THETA, ETA = 0.05, 10.0
A, B, G = 0.22, 1.12, 0.6
TMIN, TMAX = -2.0, 2.0
H = {'20DC': 2.591, '40DC': 2.591, '40HC': 2.896}
EPS = 1e-6


def pw(table, x, col):
    """Linear interpolation between the two rows nearest x. Reports when x is outside the table."""
    xs = [r[0] for r in table]
    if x < xs[0] or x > xs[-1]:
        OUT.append((x, xs[0], xs[-1]))
        return table[0][col] if x < xs[0] else table[-1][col]
    j = bisect.bisect_left(xs, x)
    if xs[j] == x:
        return table[j][col]
    d1, d2 = table[j-1], table[j]
    return d1[col]+(x-d1[0])*(d2[col]-d1[col])/(d2[0]-d1[0])


OUT = []


class V:
    pass


def vessel(path):
    r = ET.parse(path).getroot()
    v = V()
    a = r.find('VesselData').attrib
    v.gmin, v.dmax, v.lash = float(a['GMLimit']), float(a['MaxDispl']), float(a['LashLim'])
    v.hyd = sorted(tuple(float(q) for q in e.text.split(',')) for e in r.find('HydrostaticsTable').findall('Values'))
    v.bays, v.stack, v.tank = [], {}, {}
    for b in r.find('Bays').findall('Bay'):
        q = V()
        q.name = int(b.attrib['Name'])
        i = b.find('BayInfo').attrib
        q.la, q.lf = float(i['LpMin']), float(i['LpMax'])
        q.lm = (q.la+q.lf)/2
        l = b.find('LightShipMass').attrib
        q.wl, q.vl = float(l['Weight']), float(l['VP'])
        q.bon = sorted(tuple(float(z) for z in e.text.split(',')) for e in b.find('BonjeanData').findall('Values'))
        lim = b.find('BendingMomentLimit')
        q.lim = (float(lim.attrib['SaggingLimit']), float(lim.attrib['HoggingLimit'])) if lim is not None else None
        q.tanks = []
        for t in b.findall('Tanks/Tank'):
            ta = t.attrib
            info = dict(name=ta['Name'], vol=float(ta['MaxVolume']), v0=float(ta['VpEmpty']), v1=float(ta['VpFull']), bay=q.name)
            q.tanks.append(info)
            v.tank[ta['Name']] = info
        for s in b.findall('Stacks/Stack'):
            a2 = s.attrib
            st = V()
            st.id, st.bay, st.row, st.level = int(a2['Index']), q.name, int(a2['Row']), a2['Level']
            st.vp, st.t0, st.t1 = float(a2['VpMin']), int(a2['StartTier']), int(a2['EndTier'])
            st.wmax, st.hmax, st.block = float(a2['MaxWeight']), float(a2['MaxHeight']), int(a2['BlockId'])
            st.hatch = a2.get('HatchCoverId')
            st.plugs = set(int(x) for x in a2.get('ReeferPositions', '').replace(',', ';').split(';') if x.strip())
            assert (st.bay, st.row, st.level) not in v.stack
            v.stack[st.bay, st.row, st.level] = st
        v.bays.append(q)
    v.file_order = [q.name for q in v.bays]
    v.bays.sort(key=lambda q: -q.lf)        # fore to aft, from the geometry
    v.geo_order = [q.name for q in v.bays]
    return v


def boxes_of(section):
    out = {}
    for c in section.findall('Container'):
        b = V()
        b.id = int(c.find('ID').text)
        b.type = c.find('Type').text
        b.w = float(c.find('Weight').text)
        b.pol = int(float(c.find('POL').text))
        b.pod = int(float(c.find('POD').text))
        b.reefer = c.find('Reefer').text.strip() == '1'
        b.imdg = c.find('Imdg').text.strip() == '1'
        t = c.find('ImdgType')
        b.itype = t.text if t is not None else None
        p = c.find('Points')
        b.points = int(p.text) if p is not None else 0
        pos = c.find('Position')
        b.pos = None
        if pos is not None:
            b.pos = (int(pos.attrib['Bay']), int(pos.attrib['Row']), int(pos.attrib['Tier']), pos.attrib.get('Side'))
        out[b.id] = b
    return out


def instance(path):
    r = ET.parse(path).getroot()
    i = V()
    i.mix = int(r.find('MaxMixedBays').text)
    i.load = boxes_of(r.find('LoadList'))
    i.ac = boxes_of(r.find('ACList'))
    i.ports = [(int(p.attrib['Code']), int(p.attrib['MaxLC'])) for p in r.find('Voyage').findall('Port')]
    return i


def renumber(i):
    """s_14-style idle ports: published results number ports with the idle one removed."""
    used = {b.pol for b in i.load.values()} | {b.pod for b in i.load.values()} | {b.pol for b in i.ac.values()} | {b.pod for b in i.ac.values()}
    kept = [(c, m) for c, m in i.ports if c in used or c == i.ports[0][0]]
    if len(kept) == len(i.ports):
        return i
    mp = {c: k for k, (c, _) in enumerate(kept)}
    for b in list(i.load.values())+list(i.ac.values()):
        b.pol, b.pod = mp[b.pol], mp[b.pod]
    i.ports = [(mp[c], m) for c, m in kept]
    return i


def published(path):
    text = re.sub(r'<\?xml[^>]*\?>', '', open(path, encoding='utf-8-sig').read())
    r = ET.fromstring('<R>'+text+'</R>')
    pos = {}
    for c in r.find('PositionedContainers'):
        pos[int(c.attrib['ID'])] = {int(p.attrib['Port']): (int(p.attrib['Bay']), int(p.attrib['Row']), int(p.attrib['Tier']), p.attrib.get('Side'))
                                    for p in c.find('Positions')}
    tanks = {}
    for p in r.find('TankStatus'):
        tanks[int(p.attrib['Name'])] = {t.attrib['Name']: float(t.attrib['PercentageFilled']) for t in p.find('Tanks')}
    o = r.find('Objectives')
    return pos, tanks, {k: float(o.find(k).text) for k in ('BallastWater', 'ContainerPoints', 'Restows', 'Objective')}


def plan_json(path):
    d = json.load(open(path))
    pos = {int(b): {int(p): tuple(w) for p, w in where.items()} for b, where in d['positions'].items()}
    tanks = {int(p): dict(t) for p, t in d['tanks'].items()}
    return pos, tanks


def find_stack(v, bay, row, tier):
    for lvl in ('OnDeck', 'BelowDeck'):
        st = v.stack.get((bay, row, lvl))
        if st is not None and st.t0 <= tier <= st.t1:
            return st
    return None


def check(v, inst, pos, tanks):
    """Returns (breaches Counter, per-port dict, totals)."""
    br = collections.Counter()
    notes = collections.Counter()
    ports = [c for c, _ in inst.ports]
    lc = dict(inst.ports)
    P = ports[1:]
    every = dict(inst.load)
    every.update(inst.ac)

    # Each slot is (stack id, tier, 'F' or 'A')
    X = {p: {} for p in ports}
    stacks = {s.id: s for s in v.stack.values()}
    for bid, where in pos.items():
        b = every[bid]
        for p, w in where.items():
            if p not in X:
                br['position at unknown port'] += 1
                continue
            st = find_stack(v, w[0], w[1], w[2])
            if st is None or (w[2]-st.t0) % 2:
                br['off-ship position'] += 1
                continue
            halves = [(st.id, w[2], 'F', 1), (st.id, w[2], 'A', 2)] if b.type != '20DC' else [(st.id, w[2], {'Fore': 'F', 'Aft': 'A'}[w[3]], 0)]
            for sid, t, side, half in halves:
                s = sid, t, side
                if s in X[p]:
                    br['(15)'] += 1
                X[p][s] = (bid, half)

    # (13) and (20)-(21), and boxes aboard per port
    aboard = {}
    for p in ports:
        here = {c for c, _ in X[p].values()}
        must = {c for c in pos if every[c].pol <= p < every[c].pod}
        if here != must:
            br['(20)(21) aboard set'] += len(here ^ must)
        aboard[p] = len(here)
    for c, b in inst.ac.items():
        if c not in pos:
            br['AC box missing'] += 1
        elif pos[c].get(ports[0], (None,)*3)[:3] != b.pos[:3]:
            br['(13) cell differs'] += 1
    for c, w in pos.items():
        b = every[c]
        if set(w) != {p for p in ports if b.pol <= p < b.pod}:
            br['(20)(21) port list'] += 1

    res = {}
    w_total = 0.0
    ballast_sum = 0.0
    z_halves = collections.Counter()
    z_hatch_halves = collections.Counter()
    stack_cells = collections.defaultdict(list)
    prev = X[ports[0]]
    for p in P:
        cur = X[p]
        h, w = {}, {}
        for s, (c, half) in cur.items():
            b = every[c]
            h[s] = H[b.type]
            w[s] = b.w/2 if half else b.w

        by_stack = collections.defaultdict(dict)
        for s in cur:
            by_stack[s[0]][s[1], s[2]] = s

        for sid, cells in by_stack.items():
            st = stacks[sid]
            tiers = sorted({t for t, _ in cells})
            # (18)(19) fore and aft of each cell, written with the sums as printed
            for t in tiers:
                f, a = cells.get((t, 'F')), cells.get((t, 'A'))
                sumf = (every[cur[f][0]].pol if f else 0, every[cur[f][0]].pod if f else 0)
                suma = (every[cur[a][0]].pol if a else 0, every[cur[a][0]].pod if a else 0)
                if sumf[0] != suma[0]:
                    br['(18)'] += 1
                if sumf[1] != suma[1]:
                    br['(19)'] += 1
                if (f is None) != (a is None):
                    br['lone half cell'] += 1
            # (22) support on each side, with no gap down to the stack's first tier
            for side in 'FA':
                ts = sorted(t for t, q in cells if q == side)
                if ts != [st.t0+2*k for k in range(len(ts))]:
                    br['(22)'] += 1
            # (23) on fore slots as printed, and on either side
            below40 = False
            for t in tiers:
                f, a = cells.get((t, 'F')), cells.get((t, 'A'))
                here = [every[cur[q][0]] for q in (f, a) if q]
                if below40 and any(b.type == '20DC' for b in here):
                    br['(23)'] += 1
                if any(b.type != '20DC' for b in here):
                    below40 = True
            # (24) over fore slots, (25) over all
            hsum = sum(h[q] for (t, sd), q in cells.items() if sd == 'F')
            wsum = sum(w[q] for q in cells.values())
            ac_only = all(cur[q][0] in inst.ac for q in cells.values())
            if hsum > st.hmax+EPS:
                br['(24) ac-only' if ac_only else '(24)'] += 1
            if wsum > st.wmax+EPS:
                br['(25) ac-only' if ac_only else '(25)'] += 1
            # (26)
            for (t, sd), q in cells.items():
                b = every[cur[q][0]]
                if b.reefer and t not in st.plugs:
                    if cur[q][1] != 2:       # a 40 is counted once, on its first half
                        br['(26) ac' if b.id in inst.ac else '(26)'] += 1

        # Weights by bay
        wb = {q.name: q.wl for q in v.bays}
        vert = sum(q.wl*q.vl for q in v.bays)
        y_port = 0.0
        tk = tanks.get(p, {})
        for name in tk:
            t = v.tank[name]
            ybar = t['vol']*RHO
            y = tk[name]/100*ybar
            if y > ybar*(1+1e-9):
                br['(2) over'] += 1
            if y < 0:
                br['(2) negative'] += 1
            y_port += y
            wb[t['bay']] += y
            vert += y*(t['v0']+y/ybar*(t['v1']-t['v0']))
        ballast_sum += y_port

        # h base, (9), and (10) and (28)
        hbase = {}
        worst_lash = worst_lash_ac = 0.0
        lash_rows = []
        for sid, cells in by_stack.items():
            st = stacks[sid]
            run = {}
            cum = 0.0
            for t in sorted({t for t, _ in cells}):
                run[t] = cum
                a = cells.get((t, 'A'))
                if a:
                    cum += h[a]
            sw = sh = 0.0
            for (t, sd), q in cells.items():
                hb = 0.5*h[q]+run[t]
                hbase[q] = hb
                wb[st.bay] += w[q]
                vert += w[q]*(hb+st.vp)
                sw += w[q]
                sh += (hb+G*st.vp)*w[q]
            if st.level == 'OnDeck' and sw > 0:
                lash_rows.append((sid, sh/sw, sw))

        d = sum(wb.values())
        if d > v.dmax+EPS:
            br['(6)'] += 1
        tf, lcb, kmt = pw(v.hyd, d, 3), pw(v.hyd, d, 1), pw(v.hyd, d, 2)
        trim = tf*(lcb-sum(q.lm*wb[q.name] for q in v.bays)/d)
        if trim < TMIN-EPS or trim > TMAX+EPS:
            br['(7)'] += 1
        gm = kmt-vert/d
        if gm < v.gmin-EPS:
            br['(11)'] += 1
        worst_bm = 0.0
        bm_all = {}
        for i, q in enumerate(v.bays):
            if q.lim is None:
                continue
            bm = sum((f.lm-q.lf)*(wb[f.name]-pw(f.bon, d, 1)*RHO) for f in v.bays[:i])
            bm_all[q.name] = bm
            lo, hi = q.lim
            if bm < lo-EPS or bm > hi+EPS:
                br['(12)'] += 1
            if hi and bm > 0:
                worst_bm = max(worst_bm, bm/hi)
            if lo and bm < 0:
                worst_bm = max(worst_bm, bm/lo)
        for sid, hm, sw in lash_rows:
            val = max(gm, 1e-9)**A*B**hm*sw
            ac_only = all(cur[q][0] in inst.ac for q in by_stack[sid].values())
            if val > v.lash+EPS:
                br['(29) ac-only' if ac_only else '(29)'] += 1
            if ac_only:
                worst_lash_ac = max(worst_lash_ac, val)
            else:
                worst_lash = max(worst_lash, val)

        # (30)-(34), slot level, halves, literal
        ml, md = set(), set()
        for s, cb in cur.items():
            if prev.get(s) != cb:
                ml.add((cb[0], cb[1], s))
        for s, cb in prev.items():
            if cur.get(s) != cb:
                md.add((cb[0], cb[1], s))
        low = {}
        for _, _, s in ml | md:
            low[s[0]] = min(low.get(s[0], 10**6), s[1])
        induced = set()
        for s, cb in cur.items():
            if prev.get(s) == cb and s[0] in low and s[1] > low[s[0]]:
                induced.add((cb[0], cb[1], s))
        # hatch reading: a deck slot stays while the hold under its cover is worked
        worked = set()
        for _, _, s in ml | md:
            st = stacks[s[0]]
            if st.level != 'OnDeck' and st.hatch is not None:
                worked.add(st.hatch)
        hatch_ind = set()
        for s, cb in cur.items():
            st = stacks[s[0]]
            if prev.get(s) == cb and st.level == 'OnDeck' and st.hatch in worked:
                hatch_ind.add((cb[0], cb[1], s))
        # restow count per half: each c,half with a slot change at a port strictly between pol and pod
        for c, half, s in ml:
            b = every[c]
            if b.pol < p < b.pod:
                z_halves[c, half] += 1
        for c, half, s in induced:
            z_halves[c, half] += 1
        for c, half, s in hatch_ind:
            z_hatch_halves[c, half] += 1

        # (35), literal halves and per box. Induced boxes are 2 lifts each (off and on).
        lc_half = collections.Counter()
        lc_box = collections.Counter()
        for c, half, s in ml | md:
            bay = stacks[s[0]].bay
            lc_half[bay] += 1
            lc_box[bay] += 0.5 if half else 1
        for c, half, s in induced:
            bay = stacks[s[0]].bay
            lc_half[bay] += 2
            lc_box[bay] += 1 if half else 2
        lc_hatch = collections.Counter(lc_box)
        for c, half, s in hatch_ind:
            lc_hatch[stacks[s[0]].bay] += 1 if half else 2
        names = [q.name for q in v.bays]
        pair = lambda cnt: max((cnt[a]+cnt[b] for a, b in zip(names, names[1:])), default=0)
        if pair(lc_box) > lc[p]:
            br['(35) per box'] += 1
        if pair(lc_hatch) > lc[p]:
            br['(35) per box, hatch lifts added'] += 1
        if pair(lc_half) > lc[p]:
            notes['(35) literal halves'] += 1

        # (36)(37)
        pods = collections.defaultdict(set)
        for s, (c, _) in cur.items():
            st = stacks[s[0]]
            pods[st.bay, st.block].add(every[c].pod)
        mixed = {k[0] for k, q in pods.items() if len(q) > 1}
        if len(mixed) > inst.mix:
            br['(37)'] += 1

        # (27) stand-in figures: different IMDG types on a level of a bay
        lvl = collections.defaultdict(set)
        lvl_with_ac = collections.defaultdict(set)
        for s, (c, _) in cur.items():
            b = every[c]
            if b.imdg:
                st = stacks[s[0]]
                lvl_with_ac[st.bay, st.level].add(b.itype)
                if c not in inst.ac:
                    lvl[st.bay, st.level].add(b.itype)
        notes['(27) levels mixed, load only'] += sum(len(q) > 1 for q in lvl.values())
        notes['(27) levels mixed, arrival included'] += sum(len(q) > 1 for q in lvl_with_ac.values())

        res[p] = dict(displacement=d, trim=trim, gm=gm, bending=worst_bm, lashing=worst_lash, lashing_ac=worst_lash_ac, ballast=y_port, mixed_bays=len(mixed),
                      long_crane=pair(lc_box), long_crane_half=pair(lc_half), long_crane_hatch=pair(lc_hatch), limit=lc[p], aboard=aboard[p],
                      restow_stack=len({c for c, _, _ in induced}), hatch_lifted=len({c for c, _, _ in hatch_ind}))
        prev = cur

    loaded = sum(c in inst.load for c in pos)
    flat = 50*loaded
    points = sum(inst.load[c].points for c in pos if c in inst.load)
    z_box = len({c for c, _ in z_halves})      # distinct, used for a sanity print only
    z_h = sum(z_halves.values())
    z_b = sum(1 if not hf else .5 for (c, hf), n in z_halves.items() for _ in range(n))
    zh_h = sum(z_hatch_halves.values())
    zh_b = sum(1 if not hf else .5 for (c, hf), n in z_hatch_halves.items() for _ in range(n))
    tot = dict(loaded=loaded, ballast=ballast_sum, restow_box=z_b, restow_halves=z_h, hatch_box=zh_b, hatch_halves=zh_h, points_flat=flat, points_file=points,
               objective_flat=flat-THETA*ballast_sum-ETA*(z_b+zh_b), objective_file=points-THETA*ballast_sum-ETA*(z_b+zh_b),
               objective_halves=flat-THETA*ballast_sum-ETA*(z_h+zh_h), aboard0=aboard[ports[0]])
    return br, notes, res, tot
