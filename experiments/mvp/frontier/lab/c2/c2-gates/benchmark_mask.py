"""Cold independent-engine timings/profiles; differential test is test_mask.py."""
from __future__ import annotations
import argparse
import cProfile
import io
import json
import pstats
from time import perf_counter

from test_mask import HERE, cases
from alns import Problem, _mask_ok
from mask import PrefixMask
from e9fix import install
from stowage.conventions import cells_of_slot
from collections import defaultdict


def census(ins, actions):
    owner = {c:i for i,a in enumerate(actions) for c in cells_of_slot(a.slot)}
    support, hatch = set(), set()
    holds, decks = defaultdict(set), defaultdict(set)
    for stack in ins.vessel.stacks:
        seq = list(dict.fromkeys(owner[c] for c in stack.cell_ids if c in owner))
        support.update(zip(seq, seq[1:]))
        if stack.hatch_cover_id is not None:
            (decks if stack.on_deck else holds)[stack.hatch_cover_id].update(seq)
    for cover in holds.keys() & decks.keys():
        hatch.update((lo,hi) for lo in holds[cover] for hi in decks[cover] if lo != hi)
    return dict(vertices=len(actions), occupied_physical_cells=len(owner),
                support_edges=len(support), hatch_edges=len(hatch),
                unique_edges=len(support|hatch), used_slots=len({a.slot for a in actions}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='results/mask_profile.json')
    parser.add_argument('--maps', type=int, default=11)
    args = parser.parse_args()
    install()
    rows = []
    profile = cProfile.Profile()
    for i, (ins, actions) in enumerate(cases()):
        if i >= args.maps:
            break
        p = Problem(ins)
        profile.enable()
        native = _mask_ok(p, actions)
        profile.disable()
        native_times = []
        for _ in range(3):
            p = Problem(ins)
            t = perf_counter()
            assert _mask_ok(p, actions) == native
            native_times.append(perf_counter()-t)
        p = Problem(ins)
        fast = PrefixMask(p)
        t = perf_counter()
        assert fast.check(actions) == native
        first = perf_counter()-t
        t = perf_counter()
        assert fast.check(actions) == native
        repeat = perf_counter()-t
        row = dict(instance=ins.id, n=len(actions), graph=census(ins, actions), mask_legal=native,
                   native_cold_s=native_times, native_mean_s=sum(native_times)/3,
                   fast_cold_s=first, fast_repeat_s=repeat,
                   cold_speedup=(sum(native_times)/3)/first)
        print(json.dumps(row), flush=True)
        rows.append(row)
        stream = io.StringIO()
        pstats.Stats(profile, stream=stream).sort_stats('cumulative').print_stats(45)
        (HERE/args.out).write_text(json.dumps(dict(records=rows, profile=stream.getvalue()),indent=2))

if __name__ == '__main__':
    main()
