"""Replay and audit the actual paid observations offline."""
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import Mode, sha256_file, sha256_json
from experiments.mvp.pipeline.yard_vision import read_yard_picture
from experiments.mvp.pipeline.yard_vision.experiment import audited_photo

HERE = Path(__file__).resolve().parent


def test_frozen_selection_and_integrity():
    frozen = json.loads((HERE / 'fixtures/frozen.json').read_text())
    config = {k: v for k, v in frozen.items() if k not in ('config_sha256', 'frozen_utc')}
    assert sha256_json(config) == frozen['config_sha256']
    assert [c['split'] for c in frozen['cases']] == ['development'] * 2 + ['held_out'] * 4
    manifest = json.loads((HERE / 'fixtures/manifest.json').read_text())
    for name, digest in manifest['files'].items():
        assert sha256_file(HERE / 'fixtures' / name) == digest, name


@pytest.mark.parametrize('number', range(1, 7))
def test_real_fixture_replays_without_network(number, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Recorded case attempted network')

    monkeypatch.setattr('urllib.request.urlopen', forbidden)
    monkeypatch.setattr('requests.post', forbidden)
    directory = HERE / 'fixtures' / f'case-{number:02}'
    base = json.loads((directory / 'base.json').read_text())
    original = json.loads((directory / 'clean_render.yard_read.json').read_text())
    result = read_yard_picture(directory / 'clean.png', base_instance=base)
    assert result.status == original['status']
    assert result.state == original['state']
    if result.status == 'complete':
        assert result.state == json.loads((directory / 'truth.json').read_text())
    else:
        assert result.state is None and result.questions
    assert all(record.mode == Mode.recorded for record in result.records)
    assert all(record.cost_usd == 0 for record in result.records)
    assert all(record.wall_s == 0 for record in result.records if record.model)


def test_every_repaint_has_a_disposition_and_cost():
    summary = json.loads((HERE / 'fixtures/summary.json').read_text())
    assert summary['photo_trials'] == summary['spend']['muse_image_calls']
    for audit in summary['photo_audits']:
        directory = HERE / 'fixtures' / audit['case_id']
        assert sha256_file(directory / audit['photo_file']) == audit['photo_sha256']
        assert sha256_file(directory / 'clean.png') == audit['clean_sha256']
        assert sha256_file(directory / 'truth.json') == audit['truth_sha256']
        assert audit['reason'] and audit['method']
        if audit['preserved'] is not True:
            assert not (directory / 'repainted_photo.score.json').exists()


def test_strict_photo_gate_rejects_truthy_string(tmp_path):
    (tmp_path / 'photo.png').write_bytes(b'photo')
    (tmp_path / 'pair_audit.json').write_text(json.dumps({'photo_file': 'photo.png',
        'preserved': 'false', 'counts_preserved': True, 'layout_preserved': True,
        'identities_preserved': True}))
    with pytest.raises(ValueError, match='discarded'):
        audited_photo(tmp_path)


def test_held_out_code_was_frozen():
    lock = json.loads((HERE / 'fixtures/evaluation_lock.json').read_text())
    assert lock['no_tuning_after_lock'] is True
    assert lock['frozen_manifest_sha256'] == sha256_file(HERE / 'fixtures/frozen.json')
    for name, digest in lock['source_sha256'].items():
        assert sha256_file(HERE / name) == digest


def test_spend_matches_recorded_provider_usage():
    from decimal import Decimal
    from llm.spark.spend import Usage, calculate_cost

    summary = json.loads((HERE / 'fixtures/summary.json').read_text())
    spark_total = Decimal(0)
    for row in json.loads((HERE / 'fixtures/recorded.json').read_text()):
        evidence = row['evidence']
        sam = evidence.get('raw', {}).get('sam_response') or evidence.get('attempt', {}).get('sam', {}).get('raw', {})
        assert sam.get('usage', {}).get('images_processed') == 1
        spark = next(r for r in row['records'] if r.get('model') == 'muse-spark-1.3')
        spark_total += Decimal(str(spark['cost_usd']))
    assert spark_total == Decimal(summary['spend']['spark_usd'])
    usage_rows = json.loads((HERE / 'fixtures/spark_usage.json').read_text())
    assert len(usage_rows) == summary['spend']['spark_completed_calls']
    for row in usage_rows:
        assert calculate_cost(Usage(row['input_tokens'], row['output_tokens'], row['cached_tokens'])) == Decimal(row['cost_usd'])
    assert sum((Decimal(r['cost_usd']) for r in usage_rows), Decimal(0)) == spark_total
    assert Decimal(summary['spend']['sam_image_committed_usd']) <= Decimal('5')
    assert summary['spend']['vision_unknown_holds'] == 0


def test_demo_is_local_and_all_referenced_images_exist():
    import re

    html = (HERE / 'demo.html').read_text()
    assert 'https://' not in html and 'http://' not in html
    cases = json.loads(re.search(r'<script type="application/json" id="cases">(.*?)</script>', html, re.S).group(1))
    assert len(cases) == 6
    for case in cases:
        directory = HERE / 'fixtures' / case['id']
        assert all((directory / name).is_file() for name in ('clean.png', 'sam.png'))
        if case['read']['state']:
            assert (directory / 'recovered.png').is_file()
