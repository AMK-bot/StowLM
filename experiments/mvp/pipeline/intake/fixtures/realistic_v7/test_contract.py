"""Validate independent fixture contracts without opening blind document contents."""
import csv
import io
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from stowage.model.instance import Instance

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]


def test_blind_bytes_and_header_variants_stay_frozen():
    path = HERE / 'blind_freeze.json'
    manifest = json.loads(path.read_text())
    assert sha256_file(path) == (HERE / 'blind_freeze.sha256').read_text().split()[0]
    assert manifest['frozen_before_tuning'] and manifest['frozen_before_scoring']
    assert manifest['independent_fixture_author']
    assert len(manifest['cases']) == 5
    assert manifest['total_boxes'] == sum(case['rows'] for case in manifest['cases']) == 40
    assert manifest['generator_sha256'] == sha256_file(HERE.parents[1] / 'make_realistic_fixtures.py')
    for case in manifest['cases']:
        assert 'oracle_reading.json' not in case['files']
        for filename, expected_sha in case['files'].items():
            assert sha256_file(HERE / case['split'] / filename) == expected_sha


@pytest.mark.parametrize('name', ['development_01', 'development_02', 'demo'])
def test_public_case_has_real_document_families_and_an_exact_assembly_target(name):
    case = HERE / name
    expected = json.loads((case / 'expected.json').read_text())
    base_json = json.loads((case / 'base_instance.json').read_text())
    base = Instance.model_validate(base_json)
    assert len(base.load_list) == len(expected['rows']) == 8
    assert base.port.locode == base.rotation.calls[base.call_index].locode == 'THLCH'
    assert all(row['pol'] == 'THLCH' and row['gross_mass_kg'] % 10 == 0 for row in expected['rows'])
    assert sha256_json(base_json) == expected['base_instance_sha256']
    by_id = {row['id']: row for row in base_json['containers']}
    assert all(by_id[row['id']] == row for row in expected['rows'])
    documents = json.loads((case / 'documents.json').read_text())
    assert {'booking','shipping_instructions','vgm','dg_declaration','gate_slip','tally','email'} <= {d['role'] for d in documents}
    assert len({d['id'] for d in documents}) == len(documents)
    for doc in documents:
        assert doc['synthetic'] is True
        source = ROOT / doc['path']
        assert source.is_file()
        if 'text' in doc:
            assert source.read_bytes().decode('utf-8') == doc['text']
            assert not any(word in doc['text'].lower() for word in ('trap', 'conflict ?', 'vgm tba', 'temp to follow', 'duplicate gate scan', 'declaration json'))
        if doc['mime_type'] == 'text/csv':
            cells = list(csv.reader(io.StringIO(doc['text'])))
            assert len(cells) >= 9
            assert not any(cell.lstrip().startswith(('{', '[')) for row in cells for cell in row)
    email = next(d['text'] for d in documents if d['role'] == 'email')
    assert '2569' in email and 'ส่งใบจอง ใบชั่งและใบรับตู้' in email
    assert 'please' not in email.lower()
    clean = [r for r in expected['rows'] if r['id'] in expected['clean_control_ids']]
    assert any(r['reefer'] and r['reefer_setpoint_c'] == -18 for r in clean)
    assert any(r['dg'] and r['dg']['additional'] for r in clean)
    assert any(r['dg'] and r['dg']['limited_quantity'] and r['dg']['marine_pollutant'] for r in clean)
    assert len(expected['tally_cells']) == 8
