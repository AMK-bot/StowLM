"""Once-only newly frozen development variants after the envelope import fix.

Earlier infrastructure failures are retained. This launcher changes the input
file and artifact directory; the frozen semantic scorer and agent choose routes.
"""
from __future__ import annotations

import argparse
import json

from experiments.mvp.pipeline.contract.schema import sha256_file
from .hero_campaign import HERE, save
from . import hero_verified
from .native_runtime import native_caches

OUT = HERE/'fixtures/hero-postfix'
SCENARIOS = HERE/'fixtures/hero-postfix-scenarios.json'
FREEZE = HERE/'fixtures/hero-postfix-scenarios-freeze.json'


def load_postfix_scenarios(path=SCENARIOS, freeze_path=FREEZE):
    freeze = json.loads(freeze_path.read_text())
    if sha256_file(path) != freeze['scenarios_sha256']:
        raise ValueError('Frozen post-fix scenario definitions changed')
    return json.loads(path.read_text())['scenarios']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pin-preparation', action='store_true')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    hero_verified.OUT = OUT
    if args.pin_preparation:
        hero_verified.pin_preparation()
        path = OUT/'receipt-manifest.json'
        manifest = json.loads(path.read_text())
        manifest.update(excluded_first_provider_case=None,
            continuation='Newly frozen development variants, not reruns of either earlier hero cohort. Original exposed and infrastructure-failed cases remain separate.')
        save(path, manifest)
        return
    with native_caches():
        prepared = hero_verified.prepared()
        from . import hero_run
        hero_run.OUT = OUT
        hero_run.load_scenarios = load_postfix_scenarios
        hero_run.validate_native(prepared)
        if args.validate_only:
            print('Post-fix native campaign receipts authenticated; no paid call.', flush=True)
            return
        hero_run.run(prepared, verified=True)


if __name__ == '__main__':
    main()
