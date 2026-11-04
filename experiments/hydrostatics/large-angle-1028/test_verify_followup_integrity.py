"""Read-only end-to-end mutation probes for the follow-up verifier."""
import json
from pathlib import Path
import runpy
import sys

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))


def run_verifier():
    runpy.run_path(str(HERE / 'verify_followup.py'), run_name='__main__')


def alter_json(monkeypatch, target, mutate):
    original = Path.read_text

    def read(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        if path.resolve() == target.resolve():
            value = json.loads(text)
            mutate(value)
            return json.dumps(value)
        return text

    monkeypatch.setattr(Path, 'read_text', read)


def test_verifier_reads_and_rejects_corrupted_historical_input(monkeypatch):
    target = ROOT / 'experiments/mask/c2-on-current/random-tiny-1.instance.json'
    original = Path.read_bytes
    reads = []

    def corrupt(path):
        if path.resolve() == target.resolve():
            reads.append(path)
            return b'CORRUPTED HISTORICAL INSTANCE'
        return original(path)

    monkeypatch.setattr(Path, 'read_bytes', corrupt)
    with pytest.raises((AssertionError, RuntimeError)):
        run_verifier()
    assert reads, 'Historical source input was never read'


@pytest.mark.parametrize('artifact,nested', [
    ('feeder_500-historical.json', True),
    ('feeder_500-historical-combined.json', False),
    ('historical-arrival-area-convergence.json', False),
])
def test_verifier_checks_every_recorded_historical_digest(monkeypatch, artifact, nested):
    def mutate(value):
        if nested:
            value['representative_condition']['source_sha256'] = '0' * 64
        else:
            value['source_instance_sha256'] = '0' * 64

    alter_json(monkeypatch, HERE / 'followup' / artifact, mutate)
    with pytest.raises((AssertionError, RuntimeError, ValueError)):
        run_verifier()


def test_verifier_rejects_partial_area_tampering_with_unchanged_pass_flags(monkeypatch):
    def mutate(value):
        for row in value['rows']:
            row['metrics']['area_0_30_mrad'] = 999.

    alter_json(monkeypatch, HERE / 'followup/feeder_800-C1.json', mutate)
    with pytest.raises(AssertionError, match='area_0_30_mrad'):
        run_verifier()


def test_verifier_rejects_reference_ordinate_tampering_with_unchanged_pass_flags(monkeypatch):
    def mutate(value):
        value['rows'][0]['iges_upper']['gz_m'] += 1.

    alter_json(monkeypatch, HERE / 'iges-upper19-results.json', mutate)
    with pytest.raises(AssertionError):
        run_verifier()
