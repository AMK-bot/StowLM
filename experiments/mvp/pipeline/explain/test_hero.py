"""Publication gates use fake manifests, never promote the historical test plan."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_file, sha256_json
from experiments.mvp.pipeline.explain import hero


@pytest.fixture
def published(tmp_path, monkeypatch):
    root = tmp_path
    lattice = root / 'lattice'
    lattice.mkdir()
    monkeypatch.setattr(hero, 'ROOT', root)
    monkeypatch.setattr(hero, 'LATTICE', lattice)
    monkeypatch.setattr(hero, '_current_runtime', lambda entry: None)
    fixtures = Path(__file__).with_name('fixtures') / 'development'
    instance, plan, report = (json.loads((fixtures / f'{name}.json').read_text())
                              for name in ('instance', 'plan', 'report'))
    (root / 'source.json').write_text(canonical_json(instance))
    frames = [dict(step=step, stability_passed=True, strength_passed=True)
              for step in range(len(plan['moves']) + 1)]
    gate = dict(passed=True, instance_sha256=sha256_json(instance), plan_sha256=sha256_json(plan),
                checker=report, engine_identity=dict(passed=True, engine_version='0.13.0',
                engine_manifest_sha256='e' * 64),
                gauges=dict(passed=True, physics=dict(frames=frames)))
    # This is deliberately fake producer metadata; runtime identity is mocked.
    entry = dict(instance=instance, plan=plan, gate=gate, status='checked_plan', canonical_rules=[],
                 key='a' * 64, instance_sha256=sha256_json(instance), engine_version='0.13.0',
                 engine_manifest_sha256='e' * 64, plan_producer='0.13.0')
    manifest = dict(schema_version='stowlm.lattice-hero/1', synthetic=True,
                    selection_status='hero_and_backup_checked', hero=dict(
                    path='source.json', file_sha256=sha256_file(root / 'source.json'),
                    instance_sha256=sha256_json(instance), base_plan='lattice/base.json',
                    base_entry=f"lattice/store/{entry['key']}.json", screening_result='lattice/screen.json'))

    def write(row=entry, selection=manifest, *, base=None, screen=None):
        (lattice / 'store').mkdir(exist_ok=True)
        entry_path = root / selection['hero']['base_entry']
        entry_path.write_text(canonical_json(row))
        (lattice / 'store/catalogue.json').write_text(canonical_json({entry_path.stem: sha256_file(entry_path)}))
        (lattice / 'base.json').write_text(canonical_json(base or row))
        (lattice / 'screen.json').write_text(canonical_json(screen or row))
        (lattice / 'hero.json').write_text(canonical_json(selection))
    write()
    return lattice, entry, manifest, write


def test_only_named_published_hero(published):
    lattice, entry, _, _ = published
    result = hero.load_hero()
    assert result['binding']['plan_sha256'] == sha256_json(entry['plan'])
    assert result['producer'] == '0.13.0'
    (lattice / 'hero.json').unlink()
    (lattice / 'checked.incumbent.json').write_text(canonical_json(entry))
    assert hero.load_hero() is None


def test_arbitrary_incumbent_is_not_a_hero(published):
    lattice, entry, _, _ = published
    path = lattice / 'incumbent.json'
    path.write_text(canonical_json(entry))
    with pytest.raises(ValueError, match='manifest'):
        hero.load_hero(path)


def test_test_directory_is_never_published(published):
    lattice, _, _, _ = published
    with pytest.raises(ValueError, match='outside test'):
        hero.load_hero(lattice / 'pytest-tmp/hero.json')


def test_catalogue_tamper_withheld(published):
    lattice, entry, manifest, _ = published
    entry['plan_producer'] = 'Proposer'
    (hero.ROOT / manifest['hero']['base_entry']).write_text(canonical_json(entry))
    with pytest.raises(ValueError, match='catalogue'):
        hero.load_hero()


@pytest.mark.parametrize('edit,match', [
    (lambda e: e['gate'].update(passed=False), 'binding'),
    (lambda e: e['gate'].update(plan_sha256='0' * 64), 'binding'),
    (lambda e: e['gate']['checker'].update(passed=False), 'complete checker pass'),
    (lambda e: e['gate']['gauges']['physics']['frames'].pop(), 'every-frame'),
    (lambda e: e['gate']['gauges']['physics']['frames'][0].update(strength_passed=False), 'every-frame'),
    (lambda e: e.update(plan_producer='stowlm@glimmer-30b-4bit+adapter'), 'producer'),
    (lambda e: e.update(engine_version='0.12.0'), 'identity'),
])
def test_failing_publication_gates(published, edit, match):
    _, row, _, write = published
    row = deepcopy(row)
    edit(row)
    write(row)
    with pytest.raises(ValueError, match=match):
        hero.load_hero()


def test_labelled_sequence_only(published):
    _, entry, _, write = published
    entry['plan_producer'] = 'lattice.sequence'
    write(entry)
    with pytest.raises(ValueError, match='label'):
        hero.load_hero()
    entry['producer_label'] = 'Deterministic crane sequence repair after Frontier Engine'
    entry['gate']['sequence_repair'] = dict(label=entry['producer_label'])
    write(entry)
    assert hero.load_hero()['producer'] == 'lattice.sequence'


def test_screening_artifact_must_match(published):
    _, entry, _, write = published
    screen = deepcopy(entry)
    screen['plan']['moves'].pop()
    write(entry, screen=screen)
    with pytest.raises(ValueError, match='binding'):
        hero.load_hero()


def test_original_instance_bytes_are_bound(published):
    (hero.ROOT / 'source.json').write_text('{}')
    with pytest.raises(ValueError, match='source instance file hash'):
        hero.load_hero()


def test_runtime_failure_withheld(published, monkeypatch):
    def stale(entry):
        raise ValueError('hero engine, runtime or physics binding is stale')
    monkeypatch.setattr(hero, '_current_runtime', stale)
    result = hero.prepare_hero(out_dir=hero.ROOT / 'explain')
    assert result['status'] == 'hero_withheld'
    assert 'stale' in result['reason']


def test_waiting_readiness_is_not_an_invented_hero(published):
    lattice, _, _, _ = published
    (lattice / 'hero.json').unlink()
    out = hero.ROOT / 'explain'
    result = hero.prepare_hero(out_dir=out)
    assert result['status'] == 'waiting_for_named_lattice_hero'
    assert result['native_thai_reader_reviewed'] is False
    assert not (out / 'hero-inputs.json').exists()
    assert json.loads((out / 'readiness.json').read_text()) == result


def test_preparation_defers_model_menu_cost_until_selection(published, monkeypatch):
    from . import cache

    def unexpected(*args, **kwargs):
        raise AssertionError('Preparation must not derive all-box model menus')

    monkeypatch.setattr(cache, 'estimate_cost', unexpected)
    readiness = hero.prepare_hero(out_dir=hero.ROOT / 'explain')
    assert readiness['status'] == 'ready'
    assert readiness['model_cost_projection']['status'] == 'pending_selected_boxes'
    assert readiness['model_cost_projection']['aboard_boxes'] == 13


@pytest.fixture
def prefetch_cli(tmp_path, monkeypatch):
    from . import run_prefetch as cli
    boxes = [dict(id=f'B{i:02}', pod=('THLCH' if i % 2 else 'SGSIN'), gross_mass_kg=1000+i,
                  dg=({'imdg_class': '3'} if i == 8 else None), reefer=i == 9, empty=i == 10)
             for i in range(12)]
    bundle = dict(instance=dict(containers=boxes), plan={}, report={}, binding={'plan_sha256': 'a'*64},
                  story_candidates=[dict(dg_container_ids=['B08'], anchor_reefer_ids=['B09'])])
    monkeypatch.setattr(cli, '_boxes', lambda *args: [b['id'] for b in boxes])
    monkeypatch.setattr(cli.hero_source, 'prepare_hero', lambda *args: dict(status='ready', binding=bundle['binding']))
    monkeypatch.setattr(cli.hero_source, 'load_hero', lambda *args: bundle)
    estimates, recordings = [], []

    def estimate(*args, model_box_ids):
        estimates.append(model_box_ids)
        return dict(projected_cost_usd=.25, label='Projection', model_boxes=len(model_box_ids))

    def prefetch(*args, **kwargs):
        recordings.append(dict(kwargs, out_dir=args[3]))
        return dict(entries={'B08': {}}, records=[])

    monkeypatch.setattr(cli, 'estimate_cost', estimate)
    monkeypatch.setattr(cli, 'prefetch', prefetch)
    monkeypatch.setattr(cli, 'remaining_budget_usd', lambda: Decimal('2'))
    return cli, bundle, tmp_path / 'cache', estimates, recordings


def test_demo_selection_covers_cargo_without_cherry_picking(prefetch_cli):
    cli, bundle, _, _, _ = prefetch_cli
    selected = cli.select_demo_boxes(bundle)
    assert len(selected) == len(set(selected)) == 10
    assert selected[:3] == ['B08', 'B09', 'B10']
    assert selected == cli.select_demo_boxes(bundle)
    with pytest.raises(ValueError, match='limit'):
        cli.select_demo_boxes(bundle, limit=0)


def test_prefetch_absent_hero_makes_no_client_or_recording(prefetch_cli, monkeypatch):
    cli, _, directory, estimates, recordings = prefetch_cli
    monkeypatch.setattr(cli.hero_source, 'prepare_hero', lambda *args: dict(status='waiting_for_named_lattice_hero'))
    assert cli.main(['--spark', '--out-dir', str(directory)]) == 2
    assert not estimates and not recordings


def test_prefetch_estimate_stops_before_calls_and_screens(prefetch_cli):
    cli, _, directory, estimates, recordings = prefetch_cli
    assert cli.main(['--spark', '--estimate', '--out-dir', str(directory)]) == 0
    assert len(estimates[0]) == 10
    assert not recordings
    projection = json.loads((directory / 'prefetch-projection.json').read_text())
    assert projection['paid_calls_enabled'] is False
    assert projection['fast_screens_per_demo_box_upper_bound'] == 6


def test_prefetch_spark_uses_global_task_budget(prefetch_cli, monkeypatch):
    from . import budget
    from llm.spark import client
    cli, _, directory, _, recordings = prefetch_cli
    raw, bounded = object(), object()
    monkeypatch.setattr(client, 'SparkClient', lambda **kwargs: raw)

    def wrap(candidate):
        assert candidate is raw
        return bounded

    monkeypatch.setattr(budget, 'task_client', wrap)
    assert cli.main(['--spark', '--out-dir', str(directory)]) == 0
    assert recordings[0]['client'] is bounded
    assert recordings[0]['spend_cap_usd'] == 5
    assert recordings[0]['model_box_ids'] == recordings[0]['demo_box_ids']


def test_prefetch_projection_over_remaining_cap_is_withheld(prefetch_cli, monkeypatch):
    cli, _, directory, _, recordings = prefetch_cli
    monkeypatch.setattr(cli, 'remaining_budget_usd', lambda: Decimal('.01'))
    assert cli.main(['--spark', '--out-dir', str(directory)]) == 3
    assert not recordings


def test_prefetch_offline_uses_templates_for_selected_boxes(prefetch_cli):
    cli, _, directory, _, recordings = prefetch_cli
    assert cli.main(['--out-dir', str(directory)]) == 0
    assert recordings[0]['client'] is None
    assert len(recordings[0]['demo_box_ids']) == 10


def test_prefetch_default_matches_api_hero_cache(prefetch_cli, monkeypatch):
    cli, _, directory, _, recordings = prefetch_cli
    monkeypatch.setattr(cli, 'HERE', directory.parent)
    assert cli.main([]) == 0
    assert recordings[0]['out_dir'] == directory.parent / 'fixtures/hero/cache'
    assert json.loads((directory.parent / 'fixtures/readiness.json').read_text())['status'] == 'ready'
