"""Human-facing labels for recorded instruction approval, without inventing identity."""
from __future__ import annotations

import json


BATCH_CONFIRMATION_SOURCE = 'user-authorised batch regeneration, no planner present (demo fixture)'
_LEGACY_BATCH_SOURCE = 'user_authorized_batch:package_regeneration'


def confirmation_label(row, confirmation=None):
    """Keep planner identity/time; disclose the demo's batch policy in plain words."""
    confirmation = confirmation or {}
    source = (row.get('confirmation_source') or confirmation.get('source')
              or confirmation.get('confirmation_source'))
    if not source:
        return "Confirmation recorded; who confirmed isn't recorded"
    if source in (_LEGACY_BATCH_SOURCE, BATCH_CONFIRMATION_SOURCE,
                  'batch regeneration, no planner present (demo fixture)'):
        return 'Confirmed by: ' + BATCH_CONFIRMATION_SOURCE
    if source == 'sealed_expected_match':
        return 'Confirmed by: exact match to the expected rule (test harness, no planner)'
    timestamp = next((record.get(key) for record in (row, confirmation)
                      for key in ('confirmed_at', 'confirmed_utc', 'confirmation_utc', 'confirmed_at_utc')
                      if record.get(key)), None)
    return f"Confirmed by: source recorded as '{source}'" + (f' at {timestamp}' if timestamp else '')


def canonical_rule_label(row):
    """Describe the supported canonical rule in short words suitable for paper."""
    reading = row.get('reading', row.get('parsed'))
    if reading is None:
        return 'canonical reading not recorded'
    constraint = reading.get('constraint', reading) if isinstance(reading, dict) else {}
    kind = constraint.get('type')
    if kind == 'close_bay':
        return f"close bay {constraint['bay']:02d}"
    if kind == 'one_port_per_hatch':
        return 'one discharge port per hatch'
    if kind == 'max_tiers':
        bay = constraint.get('bay')
        location = f'bay {bay:02d}' if bay is not None else 'every bay'
        return f"at most {constraint['max_tiers']} tiers in the {constraint['zone']}, {location}"
    if kind == 'dg_keep_away':
        target = constraint['away_from']
        location = 'accommodation' if target['kind'] == 'accommodation' else f"bay {target['bay']:02d}"
        return f"DG class {constraint['imdg_class']} at least {constraint['min_bays']} bays from {location}"
    return 'canonical rule recorded below'


def canonical_rule_json(row):
    """Keep the exact complete canonical reading beside its short printed label."""
    reading = row.get('reading', row.get('parsed'))
    return json.dumps(reading, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
