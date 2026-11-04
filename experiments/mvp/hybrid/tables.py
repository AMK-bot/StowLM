"""Paired statistics and explicit final-plan lineage for the hybrid experiment."""
from __future__ import annotations

from math import comb
from fractions import Fraction
import random
try:
    from .validation import validate_row
except ImportError:
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('hybrid_tables_validation', Path(__file__).with_name('validation.py'))
    validation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validation)
    validate_row = validation.validate_row


def sign(fewer: int, more: int) -> float:
    """Two-sided exact sign test; ties are excluded, as in frontier/report.py."""
    if fewer < 0 or more < 0:
        raise ValueError('sign-test counts must be nonnegative')
    n = fewer + more
    return min(1., 2 * sum(comb(n, i) for i in range(min(fewer, more) + 1)) / 2**n) if n else 1.


def origin(row: dict) -> str:
    """Return recorded ancestry, never infer ancestry from cost or search labels."""
    value = row.get('final_origin')
    if not isinstance(value, str) or not value or value.startswith('search-'):
        raise ValueError(f"missing explicit final origin for {row.get('instance_id', '<unknown>')}")
    if not isinstance(row.get('final_search_changed'), bool):
        raise ValueError('final_search_changed must explicitly say whether search changed the start')
    return 'shared' if row.get('final_shared_stow') else value


def extra_origin(row: dict, arm: str) -> bool:
    origin(row)
    return _extra_label(row, arm, row.get('final_search_origin', row['final_origin']))


def _extra_label(row: dict, arm: str, value: str) -> bool:
    # Software starts can never become model/random proposals through metadata.
    if value in ('twostep', 'blocked', 'master', 'yard_first') or not isinstance(value, str):
        return False
    prefix, separator, suffix = value.partition(':')
    if not separator or not suffix or prefix not in (arm, 'adapter'):
        return False
    return row.get('arm') == arm and any(candidate.get('label') == value
            for candidate in (row.get('candidates') or {}).get('admitted', []))


def selected_extra(row: dict, arm: str) -> bool:
    return any(_extra_label(row, arm, run.get('origin', run.get('start', '')))
               for run in row.get('runs', []))


def retained(row: dict, none: dict, arm: str) -> dict:
    """Fixed logic P0.1 plus pre-result conservative mixed-shared exclusion."""
    evidence = row['final_contribution']
    if evidence['load_boxes'] != none['final_contribution']['load_boxes']:
        raise ValueError('paired plans disagree about the load-list decision universe')
    final_none = none['final_contribution']['final_decisions']
    pairs = evidence['retained_pairs']
    unique = sum(final_none.get(p['box']) != p['slot'] for p in pairs)
    raw = (extra_origin(row, arm) and evidence['total_load_boxes'] > 0
           and evidence['retained_count'] * 2 >= evidence['total_load_boxes'] and unique >= 2)
    mixed = row['final_shared_stow'] and any(not _extra_label(row, arm, r['origin'])
                                           for r in row['final_reaching_runs'])
    saved = float(none['weighted']) - float(row['weighted'])
    return dict(retained=evidence['retained_count'], total=evidence['total_load_boxes'], unique=unique,
                fraction=evidence['retained_fraction'], saved=saved, qualified=bool(raw and not mixed),
                shared_qualified=bool(raw and mixed), mixed_shared=bool(mixed),
                H1m=bool(raw and not mixed and saved >= 1))


def audited_pass(row: dict, manifest: dict) -> bool:
    return row.get('passed') is True and validate_row(row, manifest=manifest)


def paired(left: dict, right: dict, manifest: dict) -> dict:
    ids = sorted(i for i in left.keys() & right.keys() if audited_pass(left[i], manifest) and audited_pass(right[i], manifest))
    pairs = [(float(left[i]['weighted']), float(right[i]['weighted'])) for i in ids]
    fewer = sum(a < b for a, b in pairs)
    more = sum(a > b for a, b in pairs)
    mean_left = sum(a for a, _ in pairs) / len(pairs) if pairs else None
    mean_right = sum(b for _, b in pairs) / len(pairs) if pairs else None
    gain = (mean_right - mean_left) / mean_right if mean_right else None
    return dict(ids=ids, n=len(ids), mean_left=mean_left, mean_right=mean_right,
                fewer=fewer, equal=len(pairs)-fewer-more, more=more, p=sign(fewer, more), gain=gain)


def interval(pairs, *, draws=10000, seed=0):
    """Paired bootstrap 95% interval for percentage mean moves saved."""
    if not pairs:
        return None
    rng = random.Random(seed)
    values = []
    for _ in range(draws):
        sample = [rng.choice(pairs) for _ in pairs]
        baseline = sum(b for _, b in sample)
        if baseline:
            values.append(1 - sum(a for a, _ in sample)/baseline)
    if not values:
        return None
    values.sort()
    return values[int(.025*len(values))], values[min(len(values)-1, int(.975*len(values)))]


def gates(arms: dict, manifest: dict) -> dict:
    ln = paired(arms['llm'], arms['none'], manifest)
    lr = paired(arms['llm'], arms['random'], manifest)
    presence = sum(retained(arms['llm'][i], arms['none'][i], 'llm')['qualified'] for i in ln['ids'])
    passes = {a: sum(audited_pass(r, manifest) for r in rows.values()) for a, rows in arms.items()}
    left_total = sum(Fraction(str(arms['llm'][i]['weighted'])) for i in ln['ids'])
    right_total = sum(Fraction(str(arms['none'][i]['weighted'])) for i in ln['ids'])
    return dict(
        H1=bool(ln['n'] and presence * 5 >= ln['n']),
        H2=bool(ln['n'] and right_total > 0 and left_total * 100 <= right_total * 97
                and ln['fewer'] > ln['more'] and ln['p'] < .05 and passes['llm'] >= passes['none']),
        H3=bool(lr['n'] and lr['mean_left'] < lr['mean_right']
                and lr['fewer'] > lr['more'] and lr['p'] < .05 and passes['llm'] >= passes['random']),
        presence=presence, presence_denominator=ln['n'], passes=passes,
    )
