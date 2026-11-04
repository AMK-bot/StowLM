"""D206 admission from the leader's fresh, explicitly recorded core-state file."""
from datetime import datetime, timezone
import json
from math import isfinite
import os
from pathlib import Path
import time

ORIGINAL_ROOT = Path(os.environ.get('HYBRID_ORIGINAL_ROOT', Path(__file__).resolve().parents[3])).resolve()
RULE = dict(source='experiments/fleet/core-state.json', min_core_count=8,
            max_load_1m=45., max_age_s=120., workers=4)


def source_path():
    return ORIGINAL_ROOT / RULE['source']


def _number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
    except OverflowError:
        return False


def _state_errors(state, observed_at):
    if not isinstance(state, dict):
        return ['core state must be an object']
    errors = []
    try:
        json.dumps(state, allow_nan=False)
    except (TypeError, ValueError):
        errors.append('core state is not finite JSON')
    mask, load, timestamp = (state.get(key) for key in ('mask', 'load1', 't'))
    if not isinstance(mask, int) or isinstance(mask, bool) or mask < 0:
        errors.append('mask must be a nonnegative integer')
    elif mask.bit_count() < RULE['min_core_count']:
        errors.append('mask has fewer than eight cores')
    if not _number(load) or not 0 <= load <= RULE['max_load_1m']:
        errors.append('load1 must be finite, nonnegative, and at most 45')
    if not _number(observed_at) or not _number(timestamp):
        errors.append('observation and source timestamps must be finite numbers')
    else:
        age = observed_at - timestamp
        if not _number(age) or not 0 <= age < RULE['max_age_s']:
            errors.append('core state is future-dated or at least 120 seconds old')
    return errors


def state_evidence(state, *, observed_at, path=None):
    """Keep the entire leader reading plus values derived at admission time."""
    result = dict(utc=datetime.fromtimestamp(observed_at, timezone.utc).isoformat(),
                  observed_at=observed_at, source=RULE['source'],
                  source_path=str(Path(path or source_path()).resolve()), raw=state,
                  t=None, age_s=None, core_mask=None, core_count=None,
                  load_1m=None, allowed=False)
    if isinstance(state, dict):
        mask, timestamp = state.get('mask'), state.get('t')
        result.update(t=timestamp, core_mask=mask, load_1m=state.get('load1'))
        if isinstance(mask, int) and not isinstance(mask, bool) and mask >= 0:
            result['core_count'] = mask.bit_count()
        if _number(timestamp) and _number(observed_at):
            result['age_s'] = observed_at - timestamp
    errors = _state_errors(state, observed_at)
    result['allowed'] = not errors
    if errors:
        result['error'] = '; '.join(errors)
    return result


def reading(path=None, now=None):
    """Missing, stale, future, malformed, or insufficient readings fail closed."""
    path = Path(path or source_path()).resolve()
    observed_at = time.time() if now is None else now
    try:
        def unique_object(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError(f'duplicate core-state field: {key}')
                value[key] = item
            return value
        def finite_constant(value):
            raise ValueError(f'non-JSON numeric constant in core state: {value}')
        state = json.loads(path.read_text(), object_pairs_hook=unique_object, parse_constant=finite_constant)
        return state_evidence(state, observed_at=observed_at, path=path)
    except (OSError, ValueError, TypeError) as error:
        result = state_evidence(None, observed_at=observed_at, path=path)
        result['error'] = f'{type(error).__name__}: {error}'
        return result


def evidence_errors(value, *, expected_source_path=None):
    """Check freshness at historical observation time, not the reporting clock."""
    if not isinstance(value, dict):
        return ['row lacks recorded admission evidence']
    errors = _state_errors(value.get('raw'), value.get('observed_at'))
    expected_path = str(Path(expected_source_path or source_path()).resolve())
    if value.get('source') != RULE['source'] or value.get('source_path') != expected_path:
        errors.append('admission source differs from the declared leader core-state file')
    if value.get('allowed') is not True or value.get('error'):
        errors.append('recorded admission was not allowed')
    raw, observed = value.get('raw'), value.get('observed_at')
    if isinstance(raw, dict) and _number(observed) and _number(raw.get('t')):
        mask = raw.get('mask')
        count = mask.bit_count() if isinstance(mask, int) and not isinstance(mask, bool) and mask >= 0 else None
        expected = dict(t=raw.get('t'), age_s=observed - raw['t'], core_mask=mask,
                        core_count=count, load_1m=raw.get('load1'))
        for key, item in expected.items():
            if (value.get(key) != item or isinstance(value.get(key), bool)
                    or key in ('core_mask', 'core_count') and not isinstance(value.get(key), int)):
                errors.append(f'admission {key} differs from the exact recorded reading')
        try:
            when = datetime.fromisoformat(value.get('utc', '').replace('Z', '+00:00'))
            if when.tzinfo is None or abs(when.timestamp() - observed) > .00001:
                errors.append('admission UTC and observation epoch differ')
        except (AttributeError, TypeError, ValueError, OverflowError):
            errors.append('admission UTC is malformed')
    return errors


def wait_for_admission(*, stopped, log, poll_s=15., sample=reading):
    while not stopped():
        current = sample()
        log('admission_probe', **current)
        if current['allowed'] and not stopped():
            return current
        time.sleep(poll_s)
    return None
