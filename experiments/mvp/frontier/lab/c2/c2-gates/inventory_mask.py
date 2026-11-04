"""Regenerate differential inputs and inventory duplicates; never run a mask.

The generator below is copied verbatim from test_mask.py's original experiment
loop. Its per-map candidate SHA-256 must match that experiment's recorded hash,
so this inventory cannot quietly count different cases. No scoring, replay,
mask evaluation or final checking is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random

from test_mask import HERE, cases
from alns import Moves, Problem, Sequencer
from e9fix import install
from stowage.actions.space import Place


def regenerate(p, original, rng, number):
    """Keep RNG consumption and mapping iteration order identical to the test."""
    sequence = Sequencer(p)
    moves = Moves(p, rng)
    base = {a.box: a.slot for a in original}
    candidates = [original, tuple(reversed(original))]
    while len(candidates) < number:
        i = len(candidates)
        if i % 5 == 0:
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
                candidates.append(tuple(Place(b,s) for b,s in (proposal or base).items()))
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--small-cases', type=int, default=220)
    parser.add_argument('--medium-cases', type=int, default=120)
    parser.add_argument('--evidence', default='results/mask_differential.json')
    parser.add_argument('--out', default='results/mask_inventory.json')
    args = parser.parse_args()
    evidence = json.loads((HERE/args.evidence).read_text())
    expected = {row['instance']: row for row in evidence['records']}
    install()
    rng = random.Random(20261101)
    rows = []
    context_qualified_distinct = set()
    unqualified_distinct = set()
    for ins, original in cases():
        p = Problem(ins)
        number = args.medium_cases if len(ins.load_list) > 50 else args.small_cases
        candidates = regenerate(p, original, rng, number)
        digest = hashlib.sha256(repr(candidates).encode()).hexdigest()
        assert digest == expected[ins.id]['cases_sha256'], (ins.id, 'candidate hash differs')
        distinct = set(candidates)
        context_qualified_distinct.update((ins.id, actions) for actions in distinct)
        unqualified_distinct.update(distinct)
        # test_mask.py additionally compares original twice, then invokes the
        # real fallback for incomplete and duplicate-action sequences once each.
        fallback = (original[:-1], original+(original[0],))
        row = dict(instance=ins.id, boxes=len(ins.load_list),
                   differential_attempts=len(candidates), distinct_action_sequences=len(distinct),
                   duplicate_attempts=len(candidates)-len(distinct),
                   cases_sha256=digest, matches_differential_evidence=True,
                   additional_repeat_comparisons=2,
                   additional_fallback_comparisons=len(fallback),
                   distinct_fallback_sequences=len(set(fallback)),
                   fallback_reasons=['incomplete', 'duplicate_action'],
                   freeze_activation_support_assertions=1)
        rows.append(row)
        print(json.dumps(row), flush=True)
    assert len(rows) == len(expected), 'inventory/evidence map count differs'
    out = dict(mode='generation_only_no_mask_execution', seed=20261101, records=rows,
               differential_attempts=sum(r['differential_attempts'] for r in rows),
               distinct_map_action_sequences=len(context_qualified_distinct),
               distinct_action_sequences_ignoring_map_identity=len(unqualified_distinct),
               duplicate_attempts=sum(r['duplicate_attempts'] for r in rows),
               additional_repeat_comparisons=sum(r['additional_repeat_comparisons'] for r in rows),
               additional_fallback_comparisons=sum(r['additional_fallback_comparisons'] for r in rows),
               distinct_fallback_map_sequences=sum(r['distinct_fallback_sequences'] for r in rows),
               freeze_activation_support_assertions=len(rows),
               matches_all_differential_hashes=True)
    destination = HERE/args.out
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(out,indent=2))
    print(json.dumps({k:v for k,v in out.items() if k != 'records'}),flush=True)

if __name__ == '__main__':
    main()
