"""Recorded reader answers and stored plans, with no live fallback."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def stored_plan(path=HERE / 'fixtures/stored.json', *, expected_sha256=None):
    """Require a complete recording before the runner can be invoked."""
    from .stored import authenticate_file, BUNDLED_PLAN_SHA256
    raw = Path(path).read_bytes()
    authenticate_file(path, expected_sha256=expected_sha256, bundled=HERE / 'fixtures/stored.json',
                      bundled_sha256=BUNDLED_PLAN_SHA256, data=raw)
    value = json.loads(raw)
    required = ('instance_sha256', 'canonical_rules', 'computed_utc', 'engine_version',
                'engine_manifest_sha256', 'plan')
    if (not isinstance(value, dict) or any(key not in value for key in required)
            or not isinstance(value['plan'], dict) or not isinstance(value['canonical_rules'], list)):
        raise ValueError('Stored plan file needs a plan, instance hash, rules, date and engine provenance')
    return value


def run_recorded_packet(packet, *, out_dir, readings_path=HERE / 'fixtures/readings.json',
                        stored_path=HERE / 'fixtures/stored.json', confirm=None, on_event=None,
                        stored_file_sha256=None, readings_file_sha256=None, confirmation_source=None):
    """Run fixtures through the full checker, labelled as recorded offline replay.

    Unknown readings are refused; stored identity or rule mismatches are errors.
    No model, network request, GPU or live engine search is available as fallback.
    The planner still confirms every accepted voyage reading.
    """
    from . import run_packet
    from .__main__ import recorded_parser
    try:
        from .stored import authenticate_file, BUNDLED_READINGS_SHA256
        from experiments.mvp.pipeline.contract.schema import sha256_json
        candidate = stored_plan(stored_path, expected_sha256=stored_file_sha256)
        readings_hash = authenticate_file(readings_path, expected_sha256=readings_file_sha256,
            bundled=HERE / 'fixtures/readings.json', bundled_sha256=BUNDLED_READINGS_SHA256)
        parser = recorded_parser(readings_path, expected_sha256=readings_hash)
    except (OSError, ValueError, TypeError):
        return dict(status='error', passed=False, plan=None, receipt=None, package=None,
                    label='recorded', synthetic=True, execution_mode='recorded_offline',
                    message='Offline recordings could not be loaded. Supply readable, valid reader and stored-plan JSON with independently trusted SHA256 hashes, then rerun.',
                    offline_message='Recorded answers and stored plan; no network, GPU or live search.')
    parser.recorded_offline = True
    return run_packet(packet, parser=parser, stored=candidate, out_dir=out_dir,
                      confirm=confirm, on_event=on_event, stored_sha256=sha256_json(candidate),
                      confirmation_source=confirmation_source)
