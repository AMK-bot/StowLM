"""Descriptive standing-rule evidence from stored validation Frontier 0.9 rows.

Stored ``blocked_finish`` is a checked construction, not the counterfactual
portfolio with the port-blocked start disabled. No solver or reader runs here.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from statistics import mean


RUN = Path('experiments/mvp/frontier/runs/val-0.9')


def _cost(row):
    value = row.get('weighted')
    if row.get('passed') is not True or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _mean(values):
    return mean(values) if values else None


def summarize(cases, root):
    """Summarize exactly the supplied cohort, in its supplied order.

    ``root`` is the repository root; cases carry ``instance_id`` and optionally
    the repo-relative ``baseline_file``. Missing or malformed records remain
    visible in the denominator. Only this validation run directory is allowed.
    Metrics use archived checker results, with no fresh-audit claim.
    """
    root = Path(root).resolve()
    run = (root / RUN).resolve()
    rows, ids = [], set()
    for case in cases:
        identifier = case if isinstance(case, str) else case['instance_id']
        if identifier in ids:
            raise ValueError(f'Duplicate cohort instance: {identifier}')
        ids.add(identifier)
        if not isinstance(identifier, str) or Path(identifier).name != identifier:
            raise ValueError('Invalid cohort instance ID')
        baseline = RUN / f'{identifier}.json'
        if not isinstance(case, str):
            baseline = Path(case.get('baseline_file', baseline))
        path = (root / baseline).resolve()
        if path.parent != run or path.name != f'{identifier}.json':
            raise ValueError('Ablation accepts only matching stored val-0.9 records')
        item = dict(instance_id=identifier, baseline_file=str(path.relative_to(root)))
        rows.append(item)
        try:
            raw = path.read_bytes()
            item['baseline_sha256'] = hashlib.sha256(raw).hexdigest()
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get('instance_id') != identifier:
                raise ValueError('baseline instance ID mismatch')
            input_path = Path(data.get('input', ''))
            if 'val' not in input_path.parts or 'small' not in input_path.parts:
                raise ValueError('baseline is not a small validation map')
            item.update(
                status='stored', portfolio_cost=_cost(data),
                blocked_cost=_cost(data.get('blocked_finish') or {}),
                twostep_cost=_cost(data.get('twostep_finish') or {}),
                blocked_status=data.get('blocked_status'),
                search_starts=[r['start'] for r in data.get('runs', []) if 'start' in r],
            )
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
            item.update(status='unavailable', error_type=type(error).__name__)

    stored = [r for r in rows if r['status'] == 'stored']
    pairs = [r for r in stored if r['blocked_cost'] is not None and r['twostep_cost'] is not None]
    portfolio_pairs = [r for r in stored if r['blocked_cost'] is not None and r['portfolio_cost'] is not None]
    return dict(
        cohort_n=len(rows), stored_n=len(stored), rows=rows,
        evidence_scope='archived_checker_results_only', fresh_audit=False,
        exact_standing_vs_empty_available=False,
        reason=('The stored portfolio always enabled the blocked construction. Removing its '
                'record afterward cannot reproduce an empty brief: it can change the fallback, '
                'reference ballast for the sight gate, candidate ranking, and search time shares.'),
        required_run=('Run paired standing="One port per hatch." versus standing="" on these '
                      'same maps, holding voyage instructions, parser, seed, worker count and '
                      'budgets fixed; finish and freshly audit both arms without repair.'),
        blocked_checked_n=sum(r['blocked_cost'] is not None for r in stored),
        twostep_checked_n=sum(r['twostep_cost'] is not None for r in stored),
        portfolio_checked_n=sum(r['portfolio_cost'] is not None for r in stored),
        search_start_counts=dict(sorted(Counter(start for r in stored for start in r['search_starts']).items())),
        blocked_vs_twostep=dict(
            paired_n=len(pairs),
            blocked_mean=_mean([r['blocked_cost'] for r in pairs]),
            twostep_mean=_mean([r['twostep_cost'] for r in pairs]),
            mean_moves_saved=_mean([r['twostep_cost'] - r['blocked_cost'] for r in pairs]),
            blocked_cheaper_n=sum(r['blocked_cost'] < r['twostep_cost'] for r in pairs),
            equal_n=sum(r['blocked_cost'] == r['twostep_cost'] for r in pairs),
            blocked_dearer_n=sum(r['blocked_cost'] > r['twostep_cost'] for r in pairs),
        ),
        portfolio_vs_blocked=dict(
            paired_n=len(portfolio_pairs),
            portfolio_mean=_mean([r['portfolio_cost'] for r in portfolio_pairs]),
            blocked_mean=_mean([r['blocked_cost'] for r in portfolio_pairs]),
            mean_moves_saved=_mean([r['blocked_cost'] - r['portfolio_cost'] for r in portfolio_pairs]),
        ),
    )


def report_text(summary):
    """A prose paragraph safe to embed in BRIEF.md without causal overclaim."""
    pair = summary['blocked_vs_twostep']
    text = (f"Stored standing-rule evidence: {summary['blocked_checked_n']}/{summary['cohort_n']} "
            f"maps have a passing blocked_finish; {summary['twostep_checked_n']}/{summary['cohort_n']} "
            f"have a passing twostep_finish. ")
    if pair['paired_n']:
        text += (f"On {pair['paired_n']} paired maps, blocked construction costs "
                 f"{pair['blocked_mean']:.2f} vs {pair['twostep_mean']:.2f} weighted nonproductive "
                 f"moves for two-step (mean saving {pair['mean_moves_saved']:.2f}). ")
    text += ('These are archived checker results, not fresh audits or an exact standing-versus-empty '
             f"brief ablation. {summary['reason']} {summary['required_run']}")
    return text
