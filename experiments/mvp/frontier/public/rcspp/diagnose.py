"""Why do published plans break documented rules? Splits each breach by whether the plan could have avoided it and by how much it misses.

Usage: diagnose.py <suite_dir> <out.json>. Written to settle readings before any planner is held to them.
"""
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_rcspp import evaluate, layout, stack_columns
from rcspp_io import ALPHA, BETA, GAMMA, read_instance, read_result, read_vessel, vessel_path, without_idle_ports


def stats(values):
    values = sorted(values)
    if not values:
        return None
    pick = lambda q: round(values[min(len(values)-1, int(q*len(values)))], 3)
    return {'n': len(values), 'p50': pick(.5), 'p90': pick(.9), 'p99': pick(.99), 'max': round(values[-1], 3)}


def main():
    root = Path(sys.argv[1])
    names = sorted(p.stem for p in (root/'Instances').glob('*.xml'))
    vessels = {}
    bins = defaultdict(list)
    counts = Counter()
    for name in names:
        size = name.split('_')[0]
        instance = without_idle_ports(read_instance(root/'Instances'/f'{name}.xml'))
        result = read_result(root/'Results'/f'{name}_results.xml')
        vessel = vessels.setdefault(size, read_vessel(vessel_path(root, name)))
        faults, m = evaluate(vessel, instance, result.positions, result.tanks)
        boxes = {**instance.load, **instance.arrival}
        index = {s.index: s for bay in vessel.bays for s in bay.stacks}
        ports = [c for c, _ in instance.ports]
        before = None
        for previous, port in zip(ports, ports[1:]):
            if before is None:
                before, _ = layout(vessel, boxes, result.positions, previous)
            now, _ = layout(vessel, boxes, result.positions, port)
            gm = m['ports'][port]['gm']
            for key, cs in now.items():
                stack = index[key[0]]
                members = {b.id: b for b in cs.values()}
                only_arrival = all(i in instance.arrival for i in members)
                any_arrival = any(i in instance.arrival for i in members)
                changed = before.get(key, {}) != cs
                height = 0.
                moment = weight = 0.
                for tier, aft, fore in stack_columns(cs):
                    for box in ([aft, fore] if aft is not fore else [aft]):
                        if box is not None:
                            moment += box.weight*(height+box.height/2+GAMMA*stack.vp)
                            weight += box.weight
                            if box.reefer:
                                plug = tier in stack.plugs
                                counts[size, 'reefer', 'plug' if plug else 'no plug', 'arrival' if box.id in instance.arrival else 'loaded here'] += 1
                    height += (aft or fore).height
                state = 'arrival only' if only_arrival else 'some arrival' if any_arrival else 'plan only'
                if height > stack.max_height+1e-6:
                    bins[size, 'height over', state].append(height/stack.max_height)
                if stack.level == 'OnDeck' and weight:
                    ratio = gm**ALPHA*BETA**(moment/weight)*weight/vessel.lash_limit
                    bins[size, 'lashing', state, 'changed' if changed else 'unchanged'].append(ratio)
            before = now
        for i, port in enumerate(sorted(m['ports'])):
            x = m['ports'][port]
            bins[size, 'trim', 'tail' if i >= len(m['ports'])-2 else 'body'].append(abs(x['trim']))
    out = {' | '.join(map(str, k)): stats(v) for k, v in sorted(bins.items())}
    for k, v in sorted(counts.items()):
        print(k, v)
    for k, v in out.items():
        print(k, v)
    Path(sys.argv[2]).write_text(json.dumps({'bins': out, 'reefers': {' | '.join(k): v for k, v in counts.items()}}, indent=1))


if __name__ == '__main__':
    main()
