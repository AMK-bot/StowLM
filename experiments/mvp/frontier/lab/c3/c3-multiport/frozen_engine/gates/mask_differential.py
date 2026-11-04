# Copied from experiments/mvp/frontier/lab/c2/c2-gates/test_mask.py; engine integration changes are local.
"""Differential exact mask validation on manifest-listed validation/TRAIN maps."""
from __future__ import annotations
import argparse
import cProfile
import hashlib
import io
import json
from pathlib import Path
import pstats
import random
import sys
from time import perf_counter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
from catalog import load_baseline, load_set
from ..e9fix import install
from alns import Problem, Moves, Sequencer, _mask_ok
from .mask import PrefixMask
from stowage.actions.space import Place
from stowage.io import load_instance
from stowage.rules.catalogue import rule_freeze_v1


def cases():
    rows = load_baseline()
    wanted = set(load_set('quick_small')) | set(load_set('quick_medium')[:2])
    for row in rows.values():
        if row['input'] not in wanted:
            continue
        ins = load_instance(ROOT/row['input'])
        actions = row.get('ordered_actions')
        if actions:
            actions = tuple(Place(a['box'], a['slot']) for a in actions)
        elif row['size'] == 'medium':
            evidence = row['evidence'][0]['source']
            raw = json.loads((ROOT/Path(evidence).parent/'raw.json').read_text())
            plan = raw.get('plan') or raw
            actions = tuple(Place(a['container_id'], a['dst']) for a in plan['moves'] if a['kind'] == 'load')
        else:
            continue
        yield ins, actions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--small-cases', type=int, default=220)
    parser.add_argument('--medium-cases', type=int, default=120)
    parser.add_argument('--out', default='results/mask_differential.json')
    parser.add_argument('--limit-maps', type=int, default=0)
    args = parser.parse_args()
    install()
    rng = random.Random(20261101)
    records = []
    profile = cProfile.Profile()
    for map_index, (ins, original) in enumerate(cases()):
        if args.limit_maps and map_index >= args.limit_maps:
            break
        p = Problem(ins)
        oracle = PrefixMask(p)
        sequence = Sequencer(p)
        moves = Moves(p, rng)
        base = {a.box: a.slot for a in original}
        number = args.medium_cases if len(ins.load_list) > 50 else args.small_cases
        candidates = [original, tuple(reversed(original))]
        while len(candidates) < number:
            i = len(candidates)
            if i % 5 == 0:
                # Early and late bad placement, including unknown slots, length
                # mismatch, unsupported gaps, occupied cells and reversed order.
                actions = list(original)
                k = rng.randrange(len(actions))
                a = actions[k]
                slot = '9999999' if i % 10 == 0 else rng.choice(list(p.cells))
                actions[k] = Place(a.box, slot)
                candidates.append(tuple(actions))
            else:
                proposal = getattr(moves, Moves.OPERATORS[i % len(Moves.OPERATORS)])(base)
                actions = sequence.order(proposal) if proposal else None
                if actions:
                    candidates.append(actions)
                else:
                    # A cyclic precedence graph is rejected by the sequencer,
                    # so still exercise the mask on its actual action order.
                    candidates.append(tuple(Place(b,s) for b,s in (proposal or base).items()))
        real_s = fast_s = 0.
        disagreements = []
        accepted = 0
        flags = []
        for i, actions in enumerate(candidates):
            started = perf_counter()
            actual = oracle.check(actions)
            fast_s += perf_counter()-started
            started = perf_counter()
            if i == 0:
                profile.enable()
            expected = _mask_ok(p, actions)
            if i == 0:
                profile.disable()
            real_s += perf_counter()-started
            assert p.engine.depth == 0 and oracle.engine.depth == 0
            accepted += expected
            flags.append(expected)
            if actual != expected:
                disagreements.append(dict(index=i, fast=actual, real=expected))
                raise AssertionError((ins.id, disagreements[-1]))
        # Retest repeated rejected/accepted prefixes and fallback coverage.
        for a in (original, original, original[:-1], original+(original[0],)):
            assert oracle.check(a) == _mask_ok(p, a)
        with rule_freeze_v1(enabled=True):
            assert not oracle.supported(original)
        record = dict(instance=ins.id, boxes=len(ins.load_list), cases=len(candidates),
                      accepted_masks=accepted, disagreements=disagreements,
                      native_s=real_s, fast_s=fast_s, speedup=real_s/max(fast_s,1e-12),
                      supported=oracle.supported(original), stats=oracle.stats,
                      cases_sha256=hashlib.sha256(repr(candidates).encode()).hexdigest(),
                      decisions_sha256=hashlib.sha256(bytes(flags)).hexdigest())
        records.append(record)
        print(json.dumps(record), flush=True)
        stream = io.StringIO()
        pstats.Stats(profile, stream=stream).sort_stats('cumulative').print_stats(35)
        output = dict(records=records, total_cases=sum(x['cases'] for x in records),
                      disagreements=sum(len(x['disagreements']) for x in records),
                      profile=stream.getvalue())
        out = HERE/args.out
        out.parent.mkdir(exist_ok=True, parents=True)
        out.write_text(json.dumps(output,indent=2))
    print(json.dumps(dict(total_cases=sum(x['cases'] for x in records),disagreements=0)),flush=True)

if __name__ == '__main__':
    main()
