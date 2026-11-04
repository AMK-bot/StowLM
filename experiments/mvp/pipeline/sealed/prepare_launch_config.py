"""Prepare public sealed settings without a manifest, claim, map or GPU call."""
from __future__ import annotations

import argparse

from .common import HERE, ROOT, HarnessError, atomic_json, read_json, sha256

FACTORY = 'experiments.mvp.pipeline.sealed.readers:adopted_reader'
IDENTITY_KEYS = ('name', 'adapter_sha256', 'decoder_sha256', 'domain_words_sha256', 'decoding_policy')


def registered_reader_identity():
    from experiments.mvp.pipeline.reader_final.reader import ADOPTION, identity
    known = identity()
    if known['name'] != 'swa-last-5':
        raise HarnessError('D266 adopted swa-last-5 reader is required')
    return {key: known[key] for key in IDENTITY_KEYS}, dict(
        path=str(ADOPTION.relative_to(ROOT)), sha256=sha256(ADOPTION))


def configuration(*, runtime_measurement=None, cap_calibration_receipt=None):
    """Receipts may be pending; release verifies them before any sealed claim."""
    prereg = read_json(HERE/'preregistration.json')
    execution = prereg['execution']
    adoption = prereg['reader_adoption']
    known, evidence = registered_reader_identity()
    if (adoption.get('decision') != 'D266' or adoption.get('name') != known['name']
            or adoption.get('mode') != 'live'
            or any(adoption.get(key) != known[key] for key in IDENTITY_KEYS)):
        raise HarnessError('Launch reader differs from the registration')
    if (execution['sealed_workers'] != 4 or execution['registered_profile']['name'] != 'standard'
            or execution['registered_profile']['outer_planning_deadline_s'] != 1200):
        raise HarnessError('Registered sealed worker/profile/deadline recipe changed')
    return dict(
        mode='sealed', cohorts=['tiny15', 'small30', 'envelope24'],
        arms=prereg['required_arms'], workers=4, profile='standard', required_enabled_cores=18,
        pipeline_manifest=str(ROOT/'experiments/mvp/pipeline/freeze/manifest-pipeline.json'),
        admission_path=str(HERE/'measurements/release-admission.json'),
        preregistration=str(HERE/'preregistration.json'),
        twostep_budget_s=30, twostep_s=15, twostep_finalize_s=15,
        arm_deadlines_s=execution['arm_deadlines_s'],
        audit_timeout_s=execution['audit_timeout_s'], prepass_timeout_s=execution['prepass_timeout_s'],
        runtime_measurement=str(runtime_measurement or HERE/'measurements/runtime-budget-D276.json'),
        cap_calibration_receipt=str(cap_calibration_receipt or
            HERE/'measurements/calibration-reaudit-D276-v3/cap-calibration.json'),
        reader_factory=FACTORY, reader_mode='live', reader_adoption=evidence,
        parser_options=dict(adapter_sha256=known['adapter_sha256'], reader_identity=known),
        standing='', confirmation_source='sealed_expected_match')


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', default=str(HERE/'launch-config.json'))
    cli.add_argument('--runtime-measurement')
    cli.add_argument('--cap-calibration-receipt')
    args = cli.parse_args()
    atomic_json(args.output, configuration(runtime_measurement=args.runtime_measurement,
        cap_calibration_receipt=args.cap_calibration_receipt))
    print(args.output)


if __name__ == '__main__':
    main()
