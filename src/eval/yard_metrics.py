"""D72 yard reporting: total rehandles minus the counting bound in stowage.bounds.

The residual is work above a lower bound, not a proof that every remaining lift
can be eliminated. In particular a zero bound supplies no optimality certificate.
Congestion strata use recorded mixing controls, never infer mixing from noise.
"""
from collections.abc import Mapping
from copy import deepcopy
import json
import math


AVOIDABLE_YARD_METRIC = 'avoidable_yard_rehandles'


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _number(value):
    return (float(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0 else None)


def yard_rehandles(record):
    """Retain the per-instance total, yard-only lower bound, residual and certificate note."""
    from eval.harness_metrics import _checked_passed

    total = _number(_mapping(_mapping(record.get('report')).get('count')).get('relocations'))
    bound = _number(_mapping(record.get('lower_bound')).get('relocations'))
    residual = total - bound if total is not None and bound is not None else None
    if residual is not None and residual < 0 and _checked_passed(record):
        raise ValueError('yard lower bound exceeds rehandles in a checked passing plan')
    return {'total': total, 'lower_bound': bound, 'avoidable': residual,
            'gap_informative': bound > 0 if bound is not None else False,
            'certificate_note': ('yard lower bound unavailable' if bound is None else
                'no certificate (yard lower bound = 0)' if bound == 0 else 'positive yard lower bound')}


def congestion_mixing(record):
    """Copy explicit instance provenance, accepting 4.3's family tags when present."""
    instance = _mapping(record.get('instance'))
    for source in (record, instance, _mapping(instance.get('provenance')),
                   _mapping(record.get('instance_provenance')), _mapping(record.get('provenance'))):
        value = source.get('congestion_mixing')
        if isinstance(value, Mapping) and value:
            return deepcopy(dict(value))
    family = _mapping(instance.get('family')) or _mapping(record.get('family'))
    # Family values are strings in the instance schema. Requested and effective
    # shares remain distinct: noise zero can disable a positive requested share.
    aliases = {'policy': ('congestion_mixing_policy', 'congestion_mix_policy'),
               'congestion_mix_share': ('congestion_mix_share',),
               'configured_share': ('congestion_mixing_configured_share', 'congestion_mix_configured_share'),
               'achieved_share': ('congestion_mixing_achieved_share', 'congestion_mix_achieved_share',
                                  'congestion_mix_realised_share', 'congestion_mix_realized_share')}
    result = {}
    for key, names in aliases.items():
        for name in names:
            if name in family:
                result[key] = family[name]
                break
    return result or None


def mixing_share(value):
    """Normalize a recorded numeric share; missing/invalid values stay unknown."""
    try:
        numeric = float(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None
    return numeric if numeric is not None and math.isfinite(numeric) and 0 <= numeric <= 1 else None


def mixing_stratum(record):
    """Group by policy and configured controls; achieved share is an outcome statistic."""
    evidence = congestion_mixing(record)
    if evidence is None:
        return 'unknown', {}
    controls = {'policy': evidence.get('policy'),
                'congestion_mix_share': mixing_share(evidence.get('congestion_mix_share')),
                'configured_share': mixing_share(evidence.get('configured_share')),
                'synthetic': evidence.get('synthetic')}
    return json.dumps(controls, sort_keys=True, separators=(',', ':')), controls
