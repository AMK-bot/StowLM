"""D224 adoption from fixed same-sealed-v3 metrics, never sentence contents.

Running v3 is unconditional. Replacing v2 requires passing all three gates and
no exact-match or accepted-loosening regression against completed v2 on the
same cases with the same local decoder. An unfinished v2 leaves adoption
pending; it never prevents training v3 from the stopped v1 fallback.
"""
from __future__ import annotations

from experiments.mvp.reader.v3.gates import gate_report


def adoption_report(tuned, untrained, spark, v2_reference=None):
    gates = gate_report(tuned, untrained, spark)
    result = {'run_v3': True, 'gates': gates, 'adopt_v3': False,
              'comparison_policy': 'Same sealed-v3 cases, identical local decoder; no lower exact match and no higher accepted loosening count.',
              'v2_scoring_untouched': True}
    if v2_reference is None:
        result.update(status='pending_completed_v2_reference',
                      reason='V3 may run from v1; adoption waits for completed v2 scored on sealed v3.')
        return result
    # Reuse the comparator's ID/hash/population checks without imposing that the
    # v2 reference itself passes: improving a failed v2 is an allowed outcome.
    comparison = gate_report(tuned, v2_reference, spark)
    tuned_count = tuned.get('exact_match_count')
    v2_count = v2_reference.get('exact_match_count')
    if tuned_count is None or v2_count is None:
        raise ValueError('adoption requires exact-match counts, not rounded percentages')
    no_worse_exact = tuned_count >= v2_count
    no_worse_loosening = tuned['false_acceptance_count'] <= v2_reference['false_acceptance_count']
    eligible = gates['all_pass'] and no_worse_exact and no_worse_loosening
    result.update(status='adopt_v3' if eligible else 'retain_v2', adopt_v3=eligible,
                  no_worse_exact_match=no_worse_exact,
                  no_worse_accepted_loosening=no_worse_loosening,
                  exact_match_count_difference=tuned_count-v2_count,
                  exact_match_difference_pp=comparison['gain_over_untrained_pp'])
    return result
