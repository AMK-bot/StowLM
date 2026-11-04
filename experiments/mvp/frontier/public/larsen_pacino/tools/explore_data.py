"""Exploratory stats on the Larsen-Pacino files. Reads text only; used to pin down
conventions (hatches, slots, ports) before writing the parser and evaluator."""
import collections, os, sys
ROOT = "/Users/anda/Projects/meta-hackathon-stowage/data/raw/larsen-pacino"

def read_vessel(v):
    path = f"{ROOT}/vessel_data/vessel_{v}.txt"
    mode = None; bays = []; hydro = []; tanks = []
    ship = None; bay = None; stack = None; sec = None
    for line in open(path):
        s = line.strip()
        if not s: continue
        if s.startswith('#'):
            mode = s.lstrip('# ').split(':')[0]; continue
        f = s.split()
        if mode == 'Ship': ship = f
        elif mode == 'HydroPoints': hydro.append(list(map(float, f)))
        elif mode == 'Tanks': tanks.append(list(map(float, f)))
        elif mode == 'Bay':
            bay = dict(idx=int(f[0]), lcg=float(f[1]), minS=float(f[2]), maxS=float(f[3]), maxB=float(f[4]), cw=float(f[5]), cvcg=float(f[6]), buoy=[], stacks=[])
            bays.append(bay)
        elif mode == 'BuoyancyPoints': bay['buoy'].append(float(f[0]))
        elif mode == 'Stack':
            stack = dict(idx=int(f[0]), tcg=float(f[1]), secs={})
            bay['stacks'].append(stack)
        elif mode in ('AboveDeck', 'BelowDeck'):
            sec = dict(deck=mode == 'AboveDeck', id=int(f[0]), h=float(f[1]), w20=float(f[2]), w40=float(f[3]), vcg=float(f[4]), cells=[])
            stack['secs'][mode] = sec
        elif mode == 'Cell': sec['cells'].append((int(f[0]), int(f[1])))
    return ship, hydro, tanks, bays

for v in 'SML':
    ship, hydro, tanks, bays = read_vessel(v)
    print('=== vessel', v, ship, 'hydro', len(hydro), 'tanks', len(tanks), 'bays', len(bays))
    print('lightship', sum(b['cw'] for b in bays), 'sum lcg*cw', sum(b['cw']*b['lcg'] for b in bays), 'vm', sum(b['cw']*b['cvcg'] for b in bays))
    print('bay lcg', [b['lcg'] for b in bays])
    for b in bays[:4]:
        ids = collections.defaultdict(list)
        for st in b['stacks']:
            for k, sec in st['secs'].items():
                ids[sec['id']].append((st['idx'], k[0], len(sec['cells']), sec['h'], sec['w20'], sec['w40'], sec['vcg'], sec['cells'][0][0], sec['cells'][-1][0], st['tcg']))
        print('bay', b['idx'], 'maxS', b['minS'], b['maxS'], b['maxB'], 'cw', b['cw'], b['cvcg'])
        for k in sorted(ids): print('  id', k, ids[k][:30])
    # block counts
    blocks = set()
    for b in bays:
        for st in b['stacks']:
            for k, sec in st['secs'].items():
                blocks.add((b['idx'], sec['id']))
    print('blocks', len(blocks))
    nz = [(b['idx'], st['idx'], k, len(sec['cells']), sum(1 for c in sec['cells'] if c[1] == 2), sum(1 for c in sec['cells'] if c[1] == 1)) for b in bays for st in b['stacks'] for k, sec in st['secs'].items()]
    print('plugs total (r sum)', sum(c[1] for b in bays for st in b['stacks'] for sec in st['secs'].values() for c in sec['cells']), 'cells with r=1', sum(n[5] for n in nz), 'r=2', sum(n[4] for n in nz))
    print('tiers contiguous?', all(all(sec['cells'][i+1][0] == sec['cells'][i][0]+1 for i in range(len(sec['cells'])-1)) for b in bays for st in b['stacks'] for sec in st['secs'].values()))

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

for v, fn in [('S', 'VSLow1'), ('S', 'VSHigh1'), ('L', 'VLHigh1')]:
    np_, nc, types, boxes = read_inst(f"{ROOT}/container_instances/Vessel_{v}/{fn}.txt")
    rel = [b for b in boxes if len(b) > 3]; load = [b for b in boxes if len(b) == 3]
    print(fn, 'ports', np_, nc, 'rel', len(rel), 'load', len(load))
    print(' start ports rel', collections.Counter(b[0] for b in rel), 'load', collections.Counter(b[0] for b in load))
    print(' end ports rel', sorted(collections.Counter(b[1] for b in rel).items()), 'load', sorted(collections.Counter(b[1] for b in load).items()))
    print(' slots rel by len', collections.Counter((types[b[2]][0], b[6]) for b in rel))
    print(' types dups', [t for t in types.items() if t[0] >= 21][:20])
    print(' load types', collections.Counter(types[b[2]] for b in load).most_common(12))
