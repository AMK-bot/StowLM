"""Recheck an existing synthetic TRAIN plan and record its explicit handover mapping.

Run as a module from repo root with the preamble's uv command; no search or solver is run.
The historical c6 plan is deliberately not labelled Engine 0.13 output.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import time
from unittest.mock import patch

from experiments.mvp.pipeline.contract.schema import (
    Mode, StageRecord, VoyagePacket, chain, sha256_file, sha256_json,
)
from stowage.checker import CHECKER_VERSION, check
from stowage.model.instance import Instance
from stowage.model.plan import MovePlan
from stowage.model.results import CheckReport

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
CANDIDATE = ROOT / ('experiments/mvp/frontier/lab/c6/c6-lifts/results/candidate-all48/'
                    'D192.medium-feeder_800-f70-low-s192200100.json')


def wait_for_admission():
    """Time-limited CPU work waits until the fleet admission reading is fresh."""
    while True:
        try:
            row = json.loads((ROOT / 'experiments/fleet/core-state.json').read_text())
            mask = int(row['mask'], 0) if isinstance(row['mask'], str) else row['mask']
            if mask.bit_count() >= 8 and row['load1'] <= 45 and 0 <= time.time() - row['t'] < 120:
                return
        except (OSError, ValueError, KeyError, TypeError):
            pass
        time.sleep(5)


def attach_historical_manifest(outcome, source, out_dir):
    """Name the historical source evidence honestly and hash actual manifest bytes."""
    config = source['config']

    def component(name):
        recorded = dict(config[name])
        path = Path(recorded['path'])
        if path.is_absolute() and path.is_relative_to(ROOT):
            recorded['path'] = str(path.relative_to(ROOT))
        return recorded

    manifest = {
        'schema_version': 'stowlm.historical-engine-manifest/1',
        'synthetic': True, 'mode': 'generated',
        'description': 'Generated provenance manifest for a historical c6 development TRAIN plan; not the frozen Engine 0.13.0 manifest.',
        'frozen_engine_0_13_0': False,
        'engine_version': 'historical-c6-development-not-frozen-0.13.0',
        'historical_engine_version': None,
        'historical_source_sha256': config['source_sha256'],
        'source_hash_scope': 'Source snapshot digest recorded by the historical c6 run; the original complete file inventory is unavailable here.',
        'candidate': {'path': str(CANDIDATE.relative_to(ROOT)), 'sha256': sha256_file(CANDIDATE)},
        'instance': {'path': source['input'], 'sha256': source['input_sha256']},
        'components': {'propose': component('method'), 'finish': component('finish')},
        'recorded_component_versions': source['plan']['provenance']['versions'],
        'seed': config.get('seed'),
        'budget_s': config.get('budget_s'), 'finalize_s': config.get('finalize_s'),
        'limitations': ['Generated after the historical run from recorded result metadata.',
                        'This manifest is not a source freeze or an assertion that current engine files reproduce the historical search.',
                        'A current full checker rerun gates the exact stored plan; it does not rerun planning.'],
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / 'historical-engine-manifest.json'
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    outcome['engine_manifest'] = manifest
    outcome['engine_manifest_sha256'] = sha256_file(path)
    return outcome


def build_fixture(out_dir=None):
    out_dir = Path(out_dir) if out_dir is not None else HERE / 'fixtures'
    source = json.loads(CANDIDATE.read_text())
    input_path = ROOT / source['input']
    # This source is a development TRAIN map; never discover or read sealed maps.
    if '/maps/train/' not in source['input'] or 'sealed' in str(input_path):
        raise ValueError('fixture source must be the explicit development TRAIN map')
    instance = Instance.model_validate_json(input_path.read_text())
    plan = MovePlan.model_validate(source['plan'])
    assert len(instance.load_list) == 190
    wait_for_admission()
    started = time.perf_counter()
    report = check(instance, plan)
    checker_s = time.perf_counter() - started
    if not report.passed:
        raise ValueError('historical fixture failed current full checker')
    instant = datetime.now(timezone.utc).isoformat()
    packet = VoyagePacket(
        packet_id='package-demo-190-historical-train', created_utc=instant,
        base_instance={'path': source['input'], 'sha256': sha256_file(input_path)},
        standing='', profile='standard',
    ).model_dump(mode='json')
    instance_dump, plan_dump = instance.model_dump(mode='json'), plan.model_dump(mode='json')
    report_dump = report.model_dump(mode='json')
    stages = chain([
        StageRecord(stage='plan', mode=Mode.stored,
                    input_sha256=sha256_json(instance_dump), output_sha256=sha256_json(plan_dump),
                    source_sha256=source['config']['source_sha256'],
                    wall_s=source['wall_s'], notes='Historical c6 development TRAIN plan; software planner; no model; not computed by frozen Engine 0.13.0.'),
        StageRecord(stage='checker', mode=Mode.live,
                    input_sha256=sha256_json({'instance': instance_dump, 'plan': plan_dump}),
                    output_sha256=sha256_json(report_dump),
                    source_sha256=sha256_file(ROOT / 'src/stowage/checker/full.py'),
                    wall_s=checker_s, notes=f'Current full checker {CHECKER_VERSION}; every part ran; no search.'),
    ])
    outcome = {
        'status': 'checked_plan', 'mode': 'stored', 'label': 'stored', 'synthetic': True,
        'instance': instance_dump, 'plan': plan_dump, 'checker': report_dump,
        'packet': packet, 'packet_sha256': sha256_json(packet),
        'instance_sha256': sha256_json(instance_dump), 'plan_sha256': sha256_json(plan_dump),
        'steps': [s.model_dump(mode='json') for s in stages], 'rules': [],
        'engine_version': 'historical-c6-development-not-frozen-0.13.0',
        'lifts': report.count.weighted_nonproductive, 'broken_rules': [],
        'fixture_provenance': {
            'label': 'Historical synthetic development TRAIN plan, rechecked now',
            'candidate_path': str(CANDIDATE.relative_to(ROOT)),
            'candidate_sha256': sha256_file(CANDIDATE),
            'instance_source_path': source['input'], 'recorded_utc': instant,
            'load_list_boxes': len(instance.load_list),
            'arrival_placements': len(instance.onboard.placements),
            'checker_version': CHECKER_VERSION, 'checker_wall_s_measured': checker_s,
            'mapping': 'Runner was under construction. Explicit fixture adapter: source.plan -> plan; current check(instance, plan) -> checker; source.wall_s and config.source_sha256 -> historical plan stage. No reading or confirmed instruction exists; standing is empty. Arrival placement count is measured separately.'
        },
    }
    attach_historical_manifest(outcome, source, out_dir)
    (out_dir / 'outcome.json').write_text(json.dumps(outcome, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    (out_dir / 'provenance.json').write_text(json.dumps(outcome['fixture_provenance'], indent=2) + '\n')
    return outcome


def record_spark(outcome, out_dir=None):
    """Optional one-time paid recording; all normal fixture playback stays offline.

    Credentials/readiness are supplied only by the invoking shell. A bounded
    request uses the existing shared ledger and restart-safe $1 task allocation.
    """
    import llm.spark.client as client_module
    from llm.spark.planner_budget import allocation_budget
    from .note import render_note, build_note_prompt
    from .scope import scope_summary
    from .crane import cargo_work

    out_dir = Path(out_dir) if out_dir is not None else HERE / 'fixtures'
    path = out_dir / 'spark-selection.json'
    instance = Instance.model_validate(outcome['instance'])
    plan = MovePlan.model_validate(outcome['plan'])
    report = CheckReport.model_validate(outcome['checker'])
    scope = scope_summary(instance, plan, report)
    note_args = dict(scope=scope, rules=outcome.get('rules', []), confirmation=outcome.get('confirmation'),
                     cargo_work=cargo_work(instance, plan, report, banner=scope['banner'])['evidence'])
    prompt = build_note_prompt(instance, plan, report, **note_args)
    if len(prompt.encode('utf-8')) > 24576:
        raise ValueError('package note prompt exceeds bounded 32k-token request budget')
    if path.exists():
        record = json.loads(path.read_text())
        if record.get('plan_sha256') != outcome['plan_sha256']:
            raise ValueError('existing note recording belongs to another plan')
    else:
        client = client_module.SparkClient(max_retries=0, timeout=60)
        purpose = 'pipeline-package-note'
        with client.ledger.locked():
            prior_rows = [r for r in client.ledger._records() if r.get('purpose') == purpose]
            actual, held = client.ledger._totals(prior_rows)
            started = time.perf_counter()
            with patch.object(client_module, 'CONTEXT_TOKENS', 32768):
                with allocation_budget(client.ledger, allocation_usd=Decimal('1'),
                                       prior_job_usd=actual + held):
                    response = client.complete(prompt, purpose=purpose, max_output_tokens=2048,
                                               reasoning_effort='minimal')
            elapsed = time.perf_counter() - started
        record = {
            'schema_version': 'stowlm.package-note-recording/1', 'synthetic': True,
            'mode': 'recorded', 'raw': response.text, 'model': client_module.MODEL,
            'request_id': response.request_id, 'cost_usd': str(response.cost_usd),
            'usage': vars(response.usage), 'wall_s_measured': elapsed,
            'recorded_utc': datetime.now(timezone.utc).isoformat(),
            'purpose': purpose, 'plan_sha256': outcome['plan_sha256'],
            'reservation_context_bound_tokens': 32768,
        }
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    note = render_note(instance, plan, report, recorded_selection=record['raw'], **note_args)
    record['prompt_sha256'] = note['prompt_sha256']
    record['source_sha256'] = note['source_sha256']
    record['selection'] = note['selection']
    record['validated'] = note['selection'] is not None
    record['rejection'] = note['rejection']
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    outcome['note_selection'] = record['raw'] if record['validated'] else None
    outcome['note_evidence'] = {k: v for k, v in record.items() if k != 'raw'}
    (out_dir / 'outcome.json').write_text(json.dumps(outcome, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out-dir', type=Path)
    parser.add_argument('--spark', action='store_true', help='Record one bounded paid selection (credentials in environment)')
    args = parser.parse_args()
    outcome = build_fixture(args.out_dir)
    print(json.dumps(outcome['fixture_provenance'], indent=2))
    if args.spark:
        record = record_spark(outcome, args.out_dir)
        print(json.dumps({k: record[k] for k in ('validated', 'cost_usd', 'model', 'rejection')}))
