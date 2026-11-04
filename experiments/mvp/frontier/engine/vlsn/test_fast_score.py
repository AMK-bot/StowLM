# Provenance: copied from experiments/mvp/frontier/lab/c1/fx-vlsn/test_fast_score.py.
"""Differential and timing check on allowed validation rows only.

Run with PYTHONPATH=src:experiments/mvp/frontier and numerical threads=1.
"""
import argparse
import cProfile
import io
import json
from pathlib import Path
import pstats
import random
import statistics
import sys
from time import perf_counter

from alns import Problem, Sequencer, Moves
from .fast_score import FastScorer
from .search import CachedSequencer
from stowage.actions.space import Place
from stowage.io import load_instance
from stowage.rules.catalogue import rule_freeze_v1

ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]/'lab'))
from catalog import load_set, ensure_safe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--maps', type=int, default=10)
    parser.add_argument('--set', default='quick_small')
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--candidates', type=int, default=150)
    parser.add_argument('--out', help='Optional JSON evidence file inside this experiment folder')
    args = parser.parse_args()
    rng = random.Random(612)
    allowed = set(load_set(args.set))
    records = []
    profile = cProfile.Profile()
    for path in sorted((ROOT/'experiments/mvp/frontier/runs/val-0.9').glob('*.json')):
        if path.name == 'AUDIT.json':
            continue
        row = json.loads(path.read_text())
        if not row.get('passed') or row.get('input') not in allowed:
            continue
        ins = load_instance(ROOT/ensure_safe(row['input']))
        p = Problem(ins)
        fast = FastScorer(p)
        assert fast.supported, ins.id
        assignment = {m['container_id']: m['dst'] for m in row['plan']['moves'] if m['kind'] == 'load'}
        original = tuple(Place(m['container_id'], m['dst']) for m in row['plan']['moves'] if m['kind'] == 'load')
        seq, moves = Sequencer(p), Moves(p, rng)
        cached_seq = CachedSequencer(p)
        candidates = [original, seq.order(assignment)]
        for i in range(args.candidates):
            name = Moves.OPERATORS[i % len(Moves.OPERATORS)]
            changed = getattr(moves, name)(assignment)
            if changed:
                candidates.append(seq.order(changed))
        # Deliberate unsupported/illegal: reverse order, duplicate slots, gaps,
        # wrong length classes, repeated actions, omissions and unknown slots.
        candidates.append(tuple(reversed(original)))
        for _ in range(20):
            changed = dict(assignment)
            b = rng.choice(list(changed))
            changed[b] = rng.choice(list(p.cells))
            candidates.append(seq.order(changed))
        candidates += [original[:-1], original+(original[0],), (Place(original[0].box, '9999999'),)+original[1:]]
        mismatches = []
        supported = legal = 0
        fast_times, real_times = [], []
        for index, actions in enumerate(candidates):
            if actions is None:
                continue
            start = perf_counter()
            value = fast.score(actions)
            fast_times.append(perf_counter()-start)
            start = perf_counter()
            if args.profile:
                profile.enable()
            real = p.score(actions)
            if args.profile:
                profile.disable()
            real_times.append(perf_counter()-start)
            legal += real is not None
            supported += value is not None
            if value is not None:
                current = {a.box: a.slot for a in actions}
                assert cached_seq.order(current) == seq.order(current), ins.id
            if value is not None and (real is None or value != real.objective):
                mismatches.append(dict(index=index, fast=value, real=real.objective if real else None))
            # On complete pure loads the fast domain should accept every replay
            # legal action sequence, including permutations of valid assignment.
            if len(actions) == len(ins.load_list) and real is not None and value is None:
                mismatches.append(dict(index=index, fast=None, real=real.objective))
        with rule_freeze_v1(enabled=True):
            assert fast.score(original) is None
        assert fast.score(original[:-1]) is None
        record = dict(instance=ins.id, n=len(ins.load_list), candidates=len(fast_times),
                      replay_legal=legal, fast_scored=supported, mismatches=mismatches,
                      mean_real_ms=1000*statistics.mean(real_times), mean_fast_ms=1000*statistics.mean(fast_times),
                      yard_cache=str(fast._yard_cost.cache_info()))
        records.append(record)
        print(json.dumps(record), flush=True)
        if len(records) >= args.maps:
            break
    stream = io.StringIO()
    if args.profile:
        pstats.Stats(profile, stream=stream).sort_stats('cumulative').print_stats(25)
    output = dict(records=records, profile=stream.getvalue())
    if args.out:
        (HERE/args.out).write_text(json.dumps(output, indent=2))
    print(stream.getvalue())
    assert not any(r['mismatches'] for r in records)


if __name__ == '__main__':
    main()
