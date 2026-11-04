"""Task 1.4 preregistered gates; incomplete or incomparable arms fail closed."""
from __future__ import annotations

FREEZE_UTC = '2026-11-03T02:00:00+00:00'


def gate_report(tuned, untrained, spark):
    arms = (tuned, untrained, spark)
    same = ('data_sha256', 'decoder_sha256', 'schema_sha256', 'prompt_sha256',
            'decoding_policy', 'max_output_tokens', 'evaluator_sha256')
    for key in same:
        if not tuned.get(key) or any(a.get(key) != tuned[key] for a in arms):
            raise ValueError(f'incomparable arms: {key}')
    for arm, name in zip(arms, ('tuned', 'untrained', 'spark')):
        if (arm.get('arm') != name or arm.get('count') != 200
            or arm.get('recorded_prediction_count') != 200 or arm.get('missing_count') != 0
            or not arm.get('schema_constrained') or not 0 <= arm.get('exact_count', -1) <= 200
            or arm.get('dg_count', 0) <= 0):
            raise ValueError('all arms require complete, masked, same-sealed 200-row results including DG')
        if arm['dg_count'] != tuned['dg_count'] or arm.get('row_exact') != arm['exact_count'] / 200:
            raise ValueError('reported metrics disagree with the fixed population')
    if (tuned.get('model') != untrained.get('model')
        or not tuned.get('adapter_sha256') or untrained.get('adapter_sha256') is not None):
        raise ValueError('require bare-base comparison and a bound tuned adapter')
    gates = {'I1': tuned['exact_count'] >= 180,
             'I2': tuned['silent_dg_errors'] == 0,
             'I3': tuned['exact_count'] - untrained['exact_count'] >= 20,
             'I4': tuned['exact_count'] >= spark['exact_count'] - 10}
    return {'schema_version': 'stowlm.intake-gates/1', **gates,
            'all_pass': all(gates.values()),
            'decision': 'eligible' if all(gates.values()) else 'cut',
            'fallback': 'Spark intake', 'label': 'measured',
            'arms': {a['arm']: {k: a[k] for k in ('exact_count', 'count', 'row_exact',
                        'silent_dg_errors', 'malformed_count')} for a in arms}}
