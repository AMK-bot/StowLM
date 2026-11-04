"""Actual frozen ladder and checker probe on an explicitly synthetic routing case.

The four-box source is approved validation data. Only its routing metadata is
changed to exercise the frozen medium ladder without running a medium cohort.
This is release-path evidence, never medium performance or envelope evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, fields
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
from time import perf_counter
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SOURCE = ROOT / 'experiments/mvp/corpus/val/tiny/feeder_800/random_yard/seed-020000040.json'
LABEL = ('Synthetic four-box relaxed-path probe; size_class changed from tiny to medium only '
         'to exercise frozen ladder routing. Physical geometry remains a tiny section; '
         'not medium performance or medium-envelope evidence. Ordinary L0 lanes disabled by Config ablation.')


def run_probe(out_dir=HERE / 'fixtures/relaxed-review'):
    from .benchmark import wait_for_capacity
    from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
    directory = Path(out_dir).resolve()
    directory.relative_to(HERE)
    directory.mkdir(parents=True, exist_ok=True)
    metadata = dict(label=LABEL, pid=os.getpid(), source=str(SOURCE.relative_to(ROOT)),
                    source_sha256=sha256_file(SOURCE), started_utc=datetime.now(timezone.utc).isoformat(),
                    no_network=True, no_gpu=True, recorded_answer=True,
                    incidental_cache_note='The unchanged binding API may write derived caches under binding/runtime-cache; no binding files are edited directly.')
    (directory / 'process.json').write_text(json.dumps(metadata, indent=2) + '\n')
    admission = wait_for_capacity()
    metadata['admission'] = admission
    (directory / 'process.json').write_text(json.dumps(metadata, indent=2) + '\n')
    from . import core
    from .evidence import json_evidence
    from engine.config import Config
    from experiments.mvp.reader.schema import OnePortPerHatch
    payload = json.loads(SOURCE.read_text())
    assert payload['size_class'] == 'tiny'
    payload['size_class'] = 'medium'
    instance_path = directory / 'synthetic-routing-instance.json'
    instance_path.write_text(json.dumps(payload, separators=(',', ':')) + '\n')
    packet = dict(packet_id='synthetic-relaxed-routing-review', synthetic=True,
                  created_utc=datetime.now(timezone.utc).isoformat(),
                  base_instance=dict(path=str(instance_path.relative_to(ROOT)), sha256=sha256_file(instance_path)),
                  standing='One port per hatch.', voyage_sentences=[], profile='live')
    (directory / 'packet.json').write_text(json.dumps(packet, indent=2) + '\n')
    switches = {field.name: False for field in fields(Config) if isinstance(getattr(Config(), field.name), bool)}
    config = Config(**dict(switches, peel_seam=True, peel_ladder=True, e9_fix=True),
                    peel_proposal_s=45., peel_finalize_s=1., peel_candidates=1)
    profile = dict(name='relaxed_release_path_ablation', target_wall_s=1200., budget_s=0.,
                   finalize_s=0., seed=0, config=config, pending_measurement=False, notes=LABEL)
    metadata['profile'] = {**profile, 'config': asdict(config)}
    (directory / 'process.json').write_text(json.dumps(metadata, indent=2) + '\n')

    class RecordedStanding:
        def parse(self, sentence, context):
            if sentence != 'One port per hatch.':
                raise ValueError('Only the recorded standing preference is available')
            return OnePortPerHatch(type='one_port_per_hatch'), dict(recorded=True,
                parser='RecordedStandingPreference', model='synthetic schema fixture', adapter_sha256=None)

    def network_unavailable(*args, **kwargs):
        raise RuntimeError('Network is disabled for the relaxed release-path probe')

    started = perf_counter()
    with patch.object(core, 'get_profile', lambda name: profile), \
            patch.object(socket, 'create_connection', network_unavailable), \
            patch.object(socket, 'getaddrinfo', network_unavailable), \
            patch.object(socket.socket, 'connect', network_unavailable):
        outcome = core.run_packet(packet, parser=RecordedStanding(), out_dir=directory / 'run',
                                  engine_timeout_s=1200.)
    return record_summary(directory, outcome=outcome, wall_s=perf_counter()-started)


def record_summary(directory, *, outcome=None, wall_s=None):
    """Summarize the recorded genuine invocation without rerunning its search."""
    from .evidence import json_evidence
    from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
    directory = Path(directory).resolve()
    directory.relative_to(HERE)
    metadata = json.loads((directory / 'process.json').read_text())
    if outcome is None:
        outcome = json.loads((directory / 'run/outcome.json').read_text())
    summary = dict(label=LABEL, admission=metadata['admission'], pid=metadata['pid'],
                   wall_s=outcome['wall_s'] if wall_s is None else wall_s,
                   incidental_cache_note=metadata.get('incidental_cache_note'),
                   engine_run_started_utc=metadata['started_utc'],
                   recorded_utc=datetime.now(timezone.utc).isoformat(),
                   engine_version=outcome.get('evidence', {}).get('engine_version'),
                   engine_manifest_sha256=outcome.get('evidence', {}).get('engine_manifest_sha256'),
                   engine_manifest_verified=(outcome.get('evidence', {}).get('engine_manifest_after') or {}).get('passed'),
                   source=metadata['source'], source_sha256=metadata['source_sha256'],
                   tier_table_sha256=sha256_file(ROOT / 'experiments/mvp/frontier/engine/tiers.toml'),
                   packet_sha256=outcome.get('packet_sha256'), instance_sha256=outcome.get('instance_sha256'),
                   status=outcome['status'], message=outcome['message'], passed=outcome['passed'],
                   level=outcome.get('level'), broken_rules=outcome.get('broken_rules'),
                   best_safe_partial=outcome.get('best_safe_partial'),
                   completion=outcome.get('evidence', {}).get('completion'),
                   evidence_nonfinite_values=outcome.get('evidence', {}).get('nonfinite_values', []),
                   package=outcome.get('package'), outcome_sha256=sha256_json(outcome))
    if outcome.get('checker') is not None:
        from engine.peel_ladder.tiers import load_table, rank_report
        from stowage.model import Instance
        instance = Instance.model_validate(outcome['instance'])
        table = load_table(ROOT / 'experiments/mvp/frontier/engine/tiers.toml')
        summary['rank'] = json_evidence({'rank': rank_report(outcome['checker'], table, instance=instance)})
        summary['rank_sha256'] = sha256_json(summary['rank'])
    summary = json_evidence(summary)
    (directory / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    return summary


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--out', type=Path, default=HERE / 'fixtures/relaxed-review')
    cli.add_argument('--summarize-existing', action='store_true', help='Rebuild summary from the completed actual run; no search')
    args = cli.parse_args(argv)
    result = record_summary(args.out) if args.summarize_existing else run_probe(args.out)
    print(json.dumps(result, allow_nan=False))
    return 0 if result['status'] == 'relaxed_plan' else 2


if __name__ == '__main__':
    raise SystemExit(main())
