import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('lab_summary', Path(__file__).parents[1]/'summary.py')
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)


def row(identity, size, lifts, passed=True, wall_s=2):
    return dict(instance_id=identity, size=size, lifts=lifts, passed=passed, wall_s=wall_s)


def test_summary_uses_mutual_passes_and_separates_sizes():
    rows = [row('a', 'tiny', 2), row('b', 'tiny', 8), row('c', 'tiny', None, False, 8),
            row('d', 'small', 100), row('e', 'medium', None, False)]
    base = {'a': row('a', 'tiny', 4), 'b': row('b', 'tiny', 6),
            'c': row('c', 'tiny', 3), 'd': row('d', 'small', 200),
            'e': {'passed': None, 'lifts': None}}
    output = summary.summarize(rows, base)['by_size']
    tiny = output['tiny']
    assert tiny['passes'] == 2 and tiny['baseline_passes'] == 3
    assert tiny['mutual_passes'] == 2
    assert tiny['mean_lifts'] == tiny['baseline_mean_lifts'] == 5
    assert tiny['paired_mean_change'] == 0
    assert tiny['paired_mean_change_ci95'] == [-2, 2]
    assert (tiny['wins'], tiny['ties'], tiny['losses']) == (1, 0, 1)
    assert tiny['mean_wall_s'] == 4
    assert output['small']['paired_mean_change'] == -100
    assert output['medium']['baseline_measured'] == 0
    assert output['medium']['paired_mean_change_ci95'] is None


def test_bootstrap_is_seeded_and_degenerate_pairs_exact():
    assert summary.paired_interval([(3, 4)]) == [-1, -1]
    pairs = [(3, 4), (2, 8), (3, 1)]
    assert summary.paired_interval(pairs, seed=7) == summary.paired_interval(pairs, seed=7)


def test_ties_and_duplicate_rejection():
    a = row('a', 'small', 4)
    assert summary.summarize([a], {'a': a})['by_size']['small']['ties'] == 1
    with pytest.raises(ValueError, match='Duplicate'):
        summary.summarize([a, a], {'a': a})


def test_unreadable_unknown_size_is_reported_without_pooling():
    broken = dict(instance_id='broken', input='broken.json', passed=False,
                  status='input_error', failure_reason='malformed JSON')
    result = summary.summarize([broken], {})
    assert result['by_size'] == {}
    assert result['unclassified_errors'][0]['failure_reason'] == 'malformed JSON'
