# Copied from experiments/mvp/frontier/lab/c5/c5-peel/test_tiers.py; local isolated imports and fixtures.
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
import tomllib

import pytest

HERE = Path(__file__).resolve().parent


def report(*violations, passed=None):
    return dict(passed=not violations if passed is None else passed,
                violations=list(violations), parts={'rules': {'ran': True, 'error': None}},
                count={'weighted_nonproductive': 17}, shut_out=[])


def violation(rule_id, measured=2.0, limit=1.0, severity='hard'):
    return dict(rule_id=rule_id, measured=measured, limit=limit, severity=severity, unit='m')


def test_catalogue_maps_exactly_once():
    catalogue = tomllib.loads((ROOT / 'src/stowage/rules/catalogue.toml').read_text())['rules']
    listed = [rule for tier in load_table().data['tiers'].values() for rule in tier['rules']]
    assert Counter(listed) == Counter(row['id'] for row in catalogue)
    assert all(count == 1 for count in Counter(listed).values())




def test_unknown_is_t0_never_allowed():
    assert tier_for('unknown.future.rule') == 'T0'
    r = report(violation('unknown.future.rule'))
    assert analyze_report(r)['level'] is None
    assert not allowed(r, 5)


@pytest.mark.parametrize('rule,level', [('expert.e6.heavy_under_deck', 1),
    ('expert.e2.weight_order', 2), ('objective.all_loaded', 3),
    ('iscode.loading.list', 4), ('iscode.loading.draft', 5)])
def test_tier_ladder(rule, level):
    r = report(violation(rule))
    assert analyze_report(r)['level'] == level
    assert allowed(r, level)
    assert not allowed(r, level-1)


def test_ordinary_pass_and_tolerated_soft_stay_l0():
    r = report(violation('expert.e2.weight_order', severity='tolerated'),
               violation('iscode.stability.margin', severity='soft'), passed=True)
    a = analyze_report(r)
    assert a['level'] == 0
    assert a['broken_rule_ids'] == []
    assert len(a['diagnostics']) == 2


def test_breaches_lower_upper_and_unique_rules_without_mutation():
    r = report(violation('iscode.loading.list', 2, 1),
               violation('iscode.loading.list', -3, -1),
               violation('iscode.loading.gm', .1, .15))
    before = deepcopy(r)
    a = analyze_report(r)
    assert r == before
    assert len(a['broken_rules']) == 2
    assert a['broken_by_tier']['T2'] == ['iscode.loading.list']
    assert a['total_breach'] == pytest.approx(3.05)
    assert a['level'] == 5
    assert rank_report(r) == pytest.approx((0, 0, 0, 2, 3.05, 17))
    assert a['broken_rules'][1]['occurrences'][0]['measured'] == 2


def test_missing_measurement_remains_unknown():
    r = report(violation('expert.e6.heavy_under_deck', None, None))
    a = analyze_report(r)
    assert a['level'] == 1
    assert a['total_breach'] is None
    assert a['broken_rules'][0]['breach'] is None
    assert rank_report(r)[4] == float('inf')
    assert violation_breach({'measured': float('nan'), 'limit': 1}) is None


def test_rank_inside_level_is_rules_then_breach_then_lifts():
    r1 = report(violation('iscode.loading.list', 10, 1))
    r2 = report(violation('iscode.loading.list', 2, 1), violation('iscode.loading.trim', 2, 1))
    assert rank_report(r1) < rank_report(r2)
    r3 = report(violation('iscode.loading.list', 3, 1))
    r4 = report(violation('iscode.loading.list', 3, 1))
    r4['count']['weighted_nonproductive'] = 16
    assert rank_report(r4) < rank_report(r3) < rank_report(r1)


def test_incomplete_checker_fail_closed():
    r = report()
    r['parts']['rules']['ran'] = False
    assert analyze_report(r)['level'] is None
    r = report()
    r['count'] = None
    assert analyze_report(r)['level'] is None
    r = report(passed=False)
    assert analyze_report(r)['level'] is None


def test_duplicate_table_rule_rejected():
    data = load_table().data
    data['tiers']['T5']['rules'].append(data['tiers']['T0']['rules'][0])
    with pytest.raises(ValueError, match='more than once'):
        TierTable(data)





def partial_report(boxes):
    v = violation('objective.all_loaded', None, None)
    v['containers'] = boxes
    r = report(v)
    r['shut_out'] = boxes
    return r


def test_all_loaded_exact_derived_count_preserves_raw_evidence():
    r = partial_report(['A', 'B', 'B'])
    before = deepcopy(r)
    a = analyze_report(r)
    rule = a['broken_rules'][0]
    assert a['ashore_count'] == 2
    assert a['boxes_left_ashore'] == ['A', 'B']
    assert rule['breach'] == 2
    assert rule['unmeasured_occurrences'] == 0
    assert rule['breach_source'].startswith('derived:')
    assert rule['occurrences'][0]['measured'] is None
    assert rule['occurrences'][0]['limit'] is None
    assert rule['occurrences'][0]['breach_unit'] == 'boxes'
    assert r == before
    assert rank_report(r) == (2, float('inf'), float('inf'), 1, 2, 17)


def test_all_loaded_uses_containers_or_report_ids_and_counts_rule_once():
    r = partial_report(['A'])
    r['shut_out'] = ['B']
    r['violations'].append(deepcopy(r['violations'][0]))
    a = analyze_report(r)
    assert a['ashore_count'] == a['broken_rules'][0]['breach'] == 2
    assert a['total_breach'] == 2
    assert violation_breach(r['violations'][0]) == 1


def test_cargo_first_ranking_count_then_teu_then_mass():
    instance = dict(containers=[dict(id='A', length_ft=20, gross_mass_kg=10000),
                                dict(id='B', length_ft=40, gross_mass_kg=5000),
                                dict(id='C', length_ft=20, gross_mass_kg=20000)])
    fewer = partial_report(['A'])
    more = partial_report(['B', 'C'])
    fewer['violations'].append(violation('iscode.loading.list', 2000, 1))
    assert rank_report(fewer, instance=instance) < rank_report(more, instance=instance)
    assert rank_report(partial_report(['A']), instance=instance) < rank_report(partial_report(['B']), instance=instance)
    assert rank_report(partial_report(['A']), instance=instance) < rank_report(partial_report(['C']), instance=instance)
    assert rank_report(partial_report(['A']), instance=instance) == (1, 1, 10000, 1, 1, 17)


def test_complete_cargo_preferred_to_partial_despite_extra_broken_rules():
    complete = report(violation('expert.e8.away_from_heat'), violation('expert.e8.on_deck'))
    partial = partial_report(['A'])
    assert rank_report(complete) < rank_report(partial)
