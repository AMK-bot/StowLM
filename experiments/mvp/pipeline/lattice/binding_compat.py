"""Pinned review-three migration for the unchanged non-hatch planning core."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json

BINDING = Path(__file__).resolve().parent.parent / 'binding'
APPROVAL_SHA256 = '7a88b2275e2c2f07f84faf5910bc94305118265cccdba6900dd0e13bf9fe6dcb'
EXPORT_DELTA_SHA256 = '2e7e36643ad2284a40a58efff11be0124fb47c86cb92bb7c55580ebb737e9c40'
API_SHA256 = 'df491918cc90df91079ab5ce10268faf324235e2f2540a956e8ed39230e60379'
PACKAGE_SHA256 = '53b897a4df90b891fb1a48f4870047d21a8ccc8b81a28cbb63c147688c9518f0'
LEGACY_SHA256 = 'e0d7d3ba142f520899cc326193f49ffc0797d74e6242f46f3b535ff7fdd7a502'
TRANSITIONAL_SHA256 = 'b1e449ef66f56a7aaaf2d4be20bf647bac9813d7d88c58d724faef6e692ec1d4'
TRANSITIONAL_FILES = ('_logic3_activation.py', '_logic3_reporting.py', '_logic3_watchdog.py')
CORE_FILES = ('__init__.py', 'adapter.py', 'admission.py', 'allocation.py', 'confirmation.py',
              'hooks.py', 'provenance.py', 'runtime.py')
REVIEW_FILES = ('review3/__init__.py', 'review3/api.py', 'review3/hatch.py')


def _pinned_json(path, expected):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('reviewed compatibility approval changed')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('reviewed compatibility approval is malformed')
    return value


def nonhatch_compatibility(row, *, binding_root=BINDING):
    """Return a deterministic proof or None. Never changes the historical row.

    This only admits the reviewed export append and evidence wrapper. It does
    not supply a checker verdict, rule approval or any reusable physics.
    """
    try:
        rules = row['canonical_rules']
        if not isinstance(rules, list) or any(not isinstance(rule, dict) or
                (rule.get('constraint') or {}).get('type') not in ('close_bay', 'max_tiers', 'dg_keep_away')
                for rule in rules):
            return None
        root = Path(binding_root)
        approval = _pinned_json(root / 'measurements/logic3-before.json', APPROVAL_SHA256)
        delta = _pinned_json(root / 'review3/init-export-delta.json', EXPORT_DELTA_SHA256)
        old = {name: approval['source_files'][name] for name in CORE_FILES}
        if sha256_json(old) != LEGACY_SHA256 or old['__init__.py'] != delta['old_sha256']:
            return None
        current = {name: sha256_file(root / name) for name in CORE_FILES}
        appended = delta['append'].encode()
        current_init = (root / '__init__.py').read_bytes()
        if not appended or not current_init.endswith(appended) or current['__init__.py'] != delta['new_sha256'] \
                or hashlib.sha256(current_init[:-len(appended)]).hexdigest() != old['__init__.py']:
            return None
        if any(current[name] != old[name] for name in CORE_FILES if name != '__init__.py'):
            return None
        if sha256_file(root / 'review3/api.py') != API_SHA256 or \
                sha256_file(root / 'review3/__init__.py') != PACKAGE_SHA256:
            return None
        pins = {value['payload']['implementation']['source_files']['provenance-pins.json']
                for value in approval['identities'].values()}
        if len(pins) != 1 or sha256_file(root / 'provenance-pins.json') not in pins:
            return None
        from .binding_sources import runtime_source_files
        names = runtime_source_files(root)
        if set(names) != set(CORE_FILES + REVIEW_FILES):
            return None
        closure = {name: sha256_file(root / name) for name in names}
        current_digest = sha256_json(closure)
        compatible_digests = {LEGACY_SHA256, sha256_json(current), current_digest}
        transitional = dict(current)
        if all((root / name).is_file() for name in TRANSITIONAL_FILES):
            transitional.update({name: sha256_file(root / name) for name in TRANSITIONAL_FILES})
            if sha256_json(transitional) == TRANSITIONAL_SHA256:
                compatible_digests.add(TRANSITIONAL_SHA256)
        origin = row.get('planning_origin_binding_source_sha256', row.get('binding_source_sha256'))
        if origin not in ({LEGACY_SHA256, TRANSITIONAL_SHA256} & compatible_digests) or row.get('binding_source_sha256') not in compatible_digests or \
                row.get('binding_source_after_sha256', LEGACY_SHA256) not in compatible_digests:
            return None
        return dict(policy='review3-approved-unchanged-nonhatch-core/1', rule_scope='no one_port_per_hatch',
                    historical_binding_source_sha256=row['binding_source_sha256'],
                    planning_origin_binding_source_sha256=origin,
                    historical_runtime_files=old if origin == LEGACY_SHA256 else transitional,
                    current_binding_source_sha256=current_digest, legacy_runtime_files=old,
                    current_runtime_files=closure, approval_sha256=APPROVAL_SHA256,
                    export_delta_sha256=EXPORT_DELTA_SHA256, reviewed_api_sha256=API_SHA256,
                    old_init_sha256=old['__init__.py'], new_init_sha256=current['__init__.py'],
                    review3_package_sha256=PACKAGE_SHA256, provenance_pins_sha256=next(iter(pins)),
                    native_checker_and_all_frame_regating_required=True)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
