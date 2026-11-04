"""Paired, map-level comparisons; never pool different instance sizes."""
from __future__ import annotations

import math
import random
from statistics import mean


def _size(row):
    size = row.get('size')
    if size in ('tiny', 'small', 'medium'):
        return size
    identity = str(row.get('instance_id', '')) + '/' + str(row.get('input', ''))
    for size in ('tiny', 'small', 'medium'):
        if f'.{size}.' in identity or f'/{size}/' in identity:
            return size
    raise ValueError(f"Unknown map size: {row.get('instance_id')}")


def _lifts(row):
    value = row.get('lifts', row.get('weighted'))
    if not row.get('passed') or value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def paired_interval(pairs, *, seed=0, draws=10000):
    """Percentile bootstrap of mean(candidate - baseline), resampling whole maps."""
    if not pairs:
        return None
    rng = random.Random(seed)
    differences = [a-b for a, b in pairs]
    samples = sorted(mean(rng.choices(differences, k=len(differences))) for _ in range(draws))
    return [samples[int(.025*draws)], samples[min(draws-1, int(.975*draws))]]


def summarize(rows, baseline, *, seed=0):
    """Compare only selected rows with baseline evidence, grouped by tiny/small/medium.

    ``baseline`` is keyed by instance_id. Null baseline passes mean unmeasured,
    distinct from measured failures. Lift means/deltas use mutual passes only.
    """
    groups = {size: [] for size in ('tiny', 'small', 'medium')}
    seen = set()
    unclassified = []
    for row in rows:
        identity = row['instance_id']
        if identity in seen:
            raise ValueError(f'Duplicate map in summary: {identity}')
        seen.add(identity)
        try:
            size = _size(row)
        except ValueError:
            if row.get('passed'):
                raise
            unclassified.append({key: row.get(key) for key in
                                 ('instance_id', 'input', 'status', 'failure_reason')})
            continue
        groups[size].append(row)
    result = {'bootstrap': {'draws': 10000, 'seed': seed, 'unit': 'map',
                            'interval': 'percentile 95%', 'change': 'candidate minus baseline'},
              'by_size': {}, 'unclassified_errors': unclassified}
    for size, group in groups.items():
        if not group:
            continue
        pairs = []
        missing = []
        baseline_passes = 0
        for row in group:
            old = baseline.get(row['instance_id'], {})
            if old.get('passed') is None:
                missing.append(row['instance_id'])
            baseline_passes += old.get('passed') is True
            a, b = _lifts(row), _lifts(old)
            if a is not None and b is not None:
                pairs.append((a, b))
        wall = [float(r['wall_s']) for r in group if r.get('wall_s') is not None]
        result['by_size'][size] = {
            'maps': len(group), 'passes': sum(r.get('passed') is True for r in group),
            'baseline_passes': baseline_passes,
            'baseline_measured': len(group)-len(missing), 'baseline_unmeasured_ids': missing,
            'mutual_passes': len(pairs),
            'mean_lifts': mean(a for a, _ in pairs) if pairs else None,
            'baseline_mean_lifts': mean(b for _, b in pairs) if pairs else None,
            'paired_mean_change': mean(a-b for a, b in pairs) if pairs else None,
            'paired_mean_change_ci95': paired_interval(pairs, seed=seed),
            'wins': sum(a < b-1e-9 for a, b in pairs),
            'ties': sum(abs(a-b) <= 1e-9 for a, b in pairs),
            'losses': sum(a > b+1e-9 for a, b in pairs),
            'mean_wall_s': mean(wall) if wall else None,
        }
    return result


def print_summary(summary):
    if summary.get('unclassified_errors'):
        print(f"Unclassified input errors: {len(summary['unclassified_errors'])}; excluded from size means", flush=True)
    for size, row in summary['by_size'].items():
        wall = row['mean_wall_s']
        text = (f"{size}: passes {row['passes']}/{row['maps']} vs 0.9.0 "
                f"{row['baseline_passes']}/{row['baseline_measured']} measured")
        if row['mutual_passes']:
            lo, hi = row['paired_mean_change_ci95']
            text += (f"; paired {row['mutual_passes']}, mean lifts {row['mean_lifts']:.3f} vs "
                     f"{row['baseline_mean_lifts']:.3f}; change {row['paired_mean_change']:+.3f} "
                     f"(95% [{lo:+.3f}, {hi:+.3f}]); W/T/L "
                     f"{row['wins']}/{row['ties']}/{row['losses']}")
        else:
            text += '; no mutual passes'
        text += f'; mean wall {wall:.3f}s' if wall is not None else '; wall unavailable'
        print(text, flush=True)
