"""Real CPU binding runs with selected profiles and an explicit two-second cap.

No profile is shortened: only the runner-owned engine wall is overridden. This
tests profile dispatch and deadline enforcement, not 720/1800-second outcomes.
The native-entry observer records its arguments and delegates unchanged.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import socket
from time import perf_counter
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUTPUT = HERE / 'fixtures/profile-short'
SOURCE = ROOT / 'experiments/mvp/corpus/val/tiny/feeder_1700/random_yard/seed-020000008.json'


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def run_probe():
    from experiments.mvp.pipeline.runner import core
    from experiments.mvp.pipeline.runner.admission import wait_for_capacity
    from experiments.mvp.pipeline.runner.profiles import get_profile, engine_kwargs
    from experiments.mvp.pipeline.contract.schema import sha256_file
    from experiments.mvp.pipeline.binding import adapter

    OUTPUT.mkdir(parents=True, exist_ok=False)
    selection_path = HERE / 'fixtures/profile-selection.json'
    selection = json.loads(selection_path.read_text())
    summary = dict(schema_version='stowlm.profile-short-probe/1',
        label='Real synthetic CPU binding runs; explicit 2 s engine cap; not profile-quality calibration.',
        started_utc=datetime.now(timezone.utc).isoformat(), pid=os.getpid(),
        selection_sha256=sha256_file(selection_path), source_sha256=sha256_file(SOURCE),
        source=str(SOURCE.relative_to(ROOT)), no_network=True, no_gpu=True, cost_usd=0., runs=[])
    original_native = adapter.engine_planner.plan_and_check
    original_connect = socket.socket.connect

    class RecordedEmptyBrief:
        def parse(self, sentence, context):
            from experiments.mvp.reader.schema import refusal
            assert sentence == ''
            return refusal('not_an_instruction'), dict(recorded=True,
                parser='RecordedEmptyBrief', model='synthetic empty-text schema fixture', adapter_sha256=None)

    def no_network(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            raise AssertionError('Network disabled in the real short probe')
        return original_connect(sock, address)

    for name in ('live', 'standard'):
        directory = OUTPUT / name
        directory.mkdir()
        profile = get_profile(name)
        selected = selection['profiles'][name]
        expected = selected['engine_kwargs']
        kwargs = engine_kwargs(profile)
        actual = dict(kwargs, config=asdict(kwargs['config']) if kwargs['config'] is not None else None)
        assert actual == expected
        assert profile['engine_timeout_s'] == selected['engine_timeout_s']
        admission = wait_for_capacity()
        entry_path = directory / 'native-entry.json'

        def observed_native(instance, **kwargs):
            _write(entry_path, dict(pid=os.getpid(), kwargs=dict(kwargs,
                config=asdict(kwargs['config']) if kwargs['config'] is not None else None)))
            return original_native(instance, **kwargs)

        packet = dict(packet_id='profile-short-' + name,
            created_utc=datetime.now(timezone.utc).isoformat(), synthetic=True,
            base_instance=dict(path=str(SOURCE.relative_to(ROOT)), sha256=sha256_file(SOURCE)),
            standing='', voyage_sentences=[], profile=name)
        _write(directory / 'packet.json', packet)
        started = perf_counter()
        with patch.object(adapter.engine_planner, 'plan_and_check', observed_native), \
                patch.object(socket.socket, 'connect', no_network):
            outcome = core.run_packet(packet, parser=RecordedEmptyBrief(), out_dir=directory / 'run',
                                      engine_timeout_s=2.)
        wall_s = perf_counter() - started
        entry = json.loads(entry_path.read_text())
        assert entry['kwargs'] == expected
        assert outcome['status'] == 'no_checked_plan', outcome['message']
        deadline = outcome['evidence']['runner_deadline']
        assert deadline['expired'] and deadline['timeout_s'] == 2.
        assert outcome['profile']['engine_timeout_s'] == selected['engine_timeout_s']
        assert outcome['plan'] is None and not (directory / 'run/package/package.json').exists()
        engine_steps = [step for step in outcome['receipt']['steps'] if step['stage'] == 'engine']
        assert len(engine_steps) == 1
        assert outcome['receipt']['engine_version'] == adapter.VERSION
        try:
            os.kill(entry['pid'], 0)
        except ProcessLookupError:
            worker_reaped = True
        else:
            worker_reaped = False
        assert worker_reaped, 'The exact recorded engine worker PID must be reaped'
        # Final receipt and disk writes follow the hard engine deadline.
        assert 2. <= engine_steps[0]['wall_s'] < 5., engine_steps
        assert wall_s < 10., wall_s
        row = dict(profile=name, selected_engine_kwargs=actual,
            selected_engine_wall_s=selected['engine_timeout_s'], explicit_probe_engine_wall_s=2.,
            actual_native_kwargs=entry['kwargs'], native_worker_pid=entry['pid'],
            native_worker_reaped=worker_reaped, admission=admission,
            status=outcome['status'], message=outcome['message'], engine_wall_s=engine_steps[0]['wall_s'],
            runner_wall_s=wall_s, runner_deadline=deadline,
            engine_version=outcome['receipt']['engine_version'],
            engine_manifest_sha256=outcome['receipt']['engine_manifest_sha256'],
            package_present=False)
        outcome_path = directory / 'run/outcome.json'
        row['outcome_sha256'] = sha256_file(outcome_path)
        with gzip.open(outcome_path.with_suffix('.json.gz'), 'wb') as stream:
            stream.write(outcome_path.read_bytes())
        outcome_path.unlink()
        row['outcome_artifact'] = str(outcome_path.with_suffix('.json.gz').relative_to(OUTPUT))
        summary['runs'].append(row)
        _write(OUTPUT / 'summary.json', summary)
    summary['passed'] = True
    _write(OUTPUT / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    print(json.dumps(run_probe(), allow_nan=False))
