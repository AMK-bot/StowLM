"""Chief-officer handover regressions; checker injection is explicit here."""
from copy import deepcopy
import csv
from io import StringIO
import json
from pathlib import Path

import pytest

import experiments.mvp.pipeline.package as package
from experiments.mvp.pipeline.package.provenance import hero_provenance
from stowage.model import CheckReport

HERE = Path(__file__).parent


def read(path):
    return json.loads(Path(path).read_text())


@pytest.fixture
def written(tmp_path, monkeypatch):
    outcome = read(HERE / 'fixtures/outcome.json')
    report = CheckReport.model_validate(read(HERE / 'fixtures/complete/checker-report.json'))
    monkeypatch.setattr(package, '_checker', lambda *args: report)
    directory = tmp_path / 'handover'
    package.write_package(outcome, directory)
    return directory


def rows(path):
    return list(csv.DictReader(StringIO('\n'.join(line for line in path.read_text().splitlines()
                                                  if not line.startswith('# ')))))


def test_every_human_artifact_discloses_half_ship(written):
    scope = read(written / 'handover-scope.json')
    for path in written.iterdir():
        if path.suffix in {'.md', '.html', '.csv'}:
            assert scope['banner'].split(' Synthetic ships')[0] in path.read_text().splitlines()[0], path.name
    assert '190 boxes planned in 8 of 14 bays' in scope['banner']
    assert '268 fixed synthetic background boxes (3,856 t)' in scope['banner']
    assert scope['weight_closes']
    assert scope['weight_total_kg'] == pytest.approx(scope['displacement_kg'])
    assert 'No ship can sail on this plan alone.' in scope['banner']


def test_chief_officer_gets_mass_cargo_and_operational_hold(written):
    manifest = rows(written / 'manifest.csv')
    assert len(manifest) == 458
    assert sum(float(row['mass_t']) for row in manifest) == pytest.approx(
        sum(row['mass_t'] for row in read(written / 'handover-scope.json')['weights']
            if row['component'] in {'Planned section cargo aboard', 'Fixed synthetic background cargo'}))
    assert sum(bool(row['dg_un']) for row in manifest) == 5
    assert sum(row['reefer'] == 'True' for row in manifest) == 6
    assert sum(row['oog'] == 'True' for row in manifest) == 2
    assert all(row['dg_psn'] and row['dg_pg'] and row['dg_ems'] for row in manifest if row['dg_un'])
    quay, yard = rows(written / 'crane-sequence.csv'), rows(written / 'yard-sequence.csv')
    assert len(yard) == 92
    assert all(row['kind'] == 'relocate' for row in yard)
    assert not any(row['kind'] == 'relocate' for row in quay)
    assert any(row['kind'] == 'heel_ballast_hold' for row in quay)
    assert any(row['list_over_1_deg'] == 'YES' for row in quay)
    assert all(row['crane'] and row['mass_t'] for row in quay if row['source_move_index'])
    assert any(row['kind'] == 'ballast_condition' for row in quay)
    restows = rows(written / 'restows.csv')
    assert {row['box'] for row in restows} == {'QZXU0000380', 'QZXU0000776'}
    note = (written / 'note-to-master.md').read_text()
    for text in ('by the head', 'GM', 'draught', 'heel', 'displacement', '5 DG', '6 reefer', '2 OOG',
                 'All 54 active rules ran. 8 are off, including stability during cargo work.', 'HEEL-BALLAST-01'):
        assert text in note
    for text in ('D11', 'TRAIN', 'expert Q14', 'QZWU'):
        assert text not in note
    excluded = (written / 'not-included.html').read_text()
    assert '0 shut out' in excluded and 'BAPLIE omitted' in excluded and '@page{size:A4' in excluded


def test_historical_fixture_cannot_be_presented_as_hero(tmp_path):
    outcome = read(HERE / 'fixtures/outcome.json')
    original = deepcopy(outcome)
    assert not hero_provenance(outcome)['eligible']
    outcome['presentation'] = 'hero'
    with pytest.raises(ValueError, match='hero provenance:.*Engine 0.13.0'):
        package.write_package(outcome, tmp_path / 'false-hero')
    assert not (tmp_path / 'false-hero').exists()
    outcome.pop('presentation')
    assert outcome == original


def test_hero_runner_receipt_requires_independent_anchor(tmp_path, monkeypatch):
    from experiments.mvp.pipeline.package import provenance
    from experiments.mvp.pipeline.contract.schema import sha256_json
    outcome = read(HERE / 'fixtures/outcome.json')
    anchor = tmp_path / 'published-hero.json'
    monkeypatch.setattr(provenance, 'HERO_ANCHOR', anchor)
    result = hero_provenance(outcome)
    assert any('not independently published' in error for error in result['errors'])
    anchor.write_text(json.dumps({'runner_receipt_sha256': 'a' * 64}))
    result = hero_provenance(outcome)
    assert any('untrusted hero runner receipt' in error for error in result['errors'])
    anchor.write_text(json.dumps({'runner_receipt_sha256': sha256_json(outcome.get('receipt', {}))}))
    # A matching pin alone cannot turn a historical plan into a confirmed Engine 0.13.0 run.
    assert not hero_provenance(outcome)['eligible']


def test_runner_confirmation_covers_audit_rows_without_per_rule_flag():
    from experiments.mvp.pipeline.package.provenance import confirmed_instruction_rows
    hero = read(HERE / 'fixtures/hero-run/outcome.json')
    rules, confirmation, steps = hero['rules'], hero['confirmation'], hero['receipt']['steps']
    evidence = dict(reading=hero['reading'], runner_receipt=hero['receipt'], packet=hero['packet'])
    result = confirmed_instruction_rows(rules, confirmation, steps, **evidence)
    assert result[0]['confirmed'] is True
    assert result[0]['confirmation_source'] == confirmation['source']
    assert 'confirmed' not in rules[0]
    assert confirmed_instruction_rows(rules, confirmation, []) == []
    assert confirmed_instruction_rows(rules, confirmation, steps) == []
    changed = deepcopy(steps)
    step = next(row for row in changed if row['stage'] == 'confirmation')
    step['notes'] = json.dumps({'confirmed_count': 0, 'confirmation': confirmation})
    assert confirmed_instruction_rows(rules, confirmation, changed, **evidence) == []


def test_shipped_package_claims_only_supported_runner_provenance():
    """This becomes the hero acceptance test when its real run is handed over.

    No synthetic stage list is accepted in lieu of a recorded run_packet receipt.
    Until then, the shipped historical fixture must not advertise itself as hero.
    """
    directory = HERE / 'fixtures/complete'
    index = read(directory / 'package.json')
    if index.get('presentation') == 'hero':
        proof = read(directory / 'hero-provenance.json')
        receipt = read(directory / 'runner-receipt.json')
        result = hero_provenance(dict(packet=read(directory / 'packet.json'),
            instance=read(directory / 'instance.json'), plan=read(directory / 'plan.json'),
            receipt=receipt, evidence={'runner_rules': receipt['rules'], 'confirmation': proof['confirmation']}))
        assert result['eligible'], result['errors']
        assert result == proof
        assert receipt['engine_version'] == '0.13.0'
        from experiments.mvp.pipeline.package.provenance import confirmed_instruction_rows
        assert confirmed_instruction_rows(receipt['rules'], proof['confirmation'], receipt['steps'],
                                          runner_receipt=receipt, packet=read(directory / 'packet.json'))
    else:
        assert index.get('presentation') == 'development_fixture'
        assert read(directory / 'engine-manifest.json')['frozen_engine_0_13_0'] is False


def test_verifier_recomputes_handover_accounting(written):
    scope = read(written / 'handover-scope.json')
    scope['background_boxes'] = 0
    (written / 'handover-scope.json').write_text(json.dumps(scope))
    result = package.verify_package(written)
    assert not result['handover']['passed']
    assert any(row.get('file') == 'handover-scope.json' for row in result['handover']['errors'])


def test_estimate_keeps_its_signed_source_gm_when_fresh_checker_scalars_vary(written, monkeypatch):
    """Simulate qualified-cache/direct scalar variation; never fake a hero run."""
    report = CheckReport.model_validate(read(written / 'checker-report.json'))
    report.parts['stability'].metrics['gm_m'] += 1e-6
    monkeypatch.setattr(package, '_checker', lambda *args: report)
    assert package.verify_package(written)['passed']
