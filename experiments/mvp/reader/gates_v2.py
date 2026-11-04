"""D219 gates: metric reports only, no sentence or prediction rewriting."""
from __future__ import annotations


def gate_report_v2(tuned: dict, untrained: dict, spark: dict) -> dict:
    if not isinstance(tuned.get('data_sha256'), str) or not tuned['data_sha256']:
        raise ValueError('gates require a nonempty sealed data hash')
    ids = [row['id'] for row in tuned['details']]
    if len(ids) != tuned['count'] or len(set(ids)) != len(ids):
        raise ValueError('invalid tuned evaluation ids')
    for other in (untrained, spark):
        other_ids = [row['id'] for row in other['details']]
        if (other['count'] != tuned['count'] or len(other_ids) != len(ids)
                or len(set(other_ids)) != len(other_ids) or set(other_ids) != set(ids)):
            raise ValueError('gates require identical evaluation ids')
        if other.get('data_sha256') != tuned.get('data_sha256'):
            raise ValueError('gates require identical sealed data hashes')
        if other.get('loosening_count') != tuned['loosening_count']:
            raise ValueError('gates require identical loosening counts')
    if tuned['count'] < 120 or tuned['loosening_count'] < 30:
        raise ValueError('sealed v2 requires at least 120 rows and 30 loosening requests')
    gap = tuned['exact_match_pct'] - spark['exact_match_pct']
    return {'R1': tuned['exact_match_pct'] >= 90,
            'R2': tuned['false_acceptance_count'] == 0,
            'R3': gap >= -3,
            'gain_over_untrained_pp': tuned['exact_match_pct'] - untrained['exact_match_pct'],
            'difference_from_spark_pp': gap,
            'criteria': {'R1_min_exact_pct': 90, 'R2_max_false_acceptances': 0,
                         'R3_max_shortfall_from_spark_pp': 3}}
