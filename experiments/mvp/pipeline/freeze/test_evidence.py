"""Synthetic film sources and fixture identity comparisons."""
import hashlib
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.freeze import evidence


def put(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, (dict, list)) else value)
    return path


def hashes(root, names):
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}


def test_fill_sources_override_exclusions_and_locations(tmp_path):
    source = 'experiments/mvp/pipeline/binding/measurements/private-cache/report.json'
    put(tmp_path, source, {'n': 3})
    for folder in ('app', 'experiments/mvp/ui', 'experiments/mvp/video'):
        put(tmp_path, folder + '/fill-v6.json', {'{{number}}': {'value': 3, 'source': source + '#n'}})
    selection = evidence.inventory(tmp_path, scope='film')
    assert source in selection['paths']
    assert len(selection['fill_sources']) == 3
    assert selection['errors'] == []
    assert evidence.validate_fill_sources(selection, set(selection['paths']) - {source})


def test_fill_missing_unresolved_and_plain_relative_source_fail_closed(tmp_path):
    put(tmp_path, 'app/fill-v6.json', {'{{missing}}': {'value': 1, 'source': 'experiments/mvp/ui/missing.json'}, '{{unsourced}}': 2})
    selection = evidence.inventory(tmp_path, scope='film')
    assert any('missing film source' in error for error in selection['errors'])
    assert any('unpinned fill source' in error for error in selection['errors'])
    assert any('no source path' in error for error in selection['errors'])
    put(tmp_path, 'report.json', {'n': 1})
    put(tmp_path, 'app/fill-v6.json', {'{{relative}}': {'value': 1, 'source': 'report.json#n'}})
    assert evidence.inventory(tmp_path, scope='film')['errors'] == []
    assert 'report.json' in evidence.inventory(tmp_path, scope='film')['paths']


def test_film_evidence_directories_and_hero_references(tmp_path):
    paths = [
        'experiments/mvp/ui/cache/v6-hero/plan.json',
        'experiments/mvp/ui/cache/regate/check.json',
        'experiments/mvp/voice/runs/v5-1.3/answer.json',
        'experiments/mvp/pipeline/binding/measurements/deadline-live-x/result.json',
        'experiments/mvp/pipeline/runner/measurements/profile-study-x/summary.json',
        'experiments/mvp/reader/v3/results-final/final-adoption.json',
        'experiments/mvp/reader/v3/parser/freeze.json',
        'experiments/mvp/reader/schema.py',
        'experiments/mvp/rules.json',
        'experiments/mvp/video/STORY-NEEDS-v6.md',
        'experiments/mvp/voice/asr.py',
        'experiments/mvp/pipeline/explain/fixtures/cache/en.json',
        'experiments/mvp/custom/recording.json',
    ]
    for name in paths:
        put(tmp_path, name, {})
    put(tmp_path, 'app/v6_hero.json', {'reader_recordings': paths[-1]})
    assert set(paths) <= set(evidence.inventory(tmp_path, scope='film')['paths'])


def test_private_keys_ignored_public_half_pinned_without_reading_private(tmp_path, monkeypatch):
    private = put(tmp_path, 'experiments/mvp/pipeline/package/fixtures/.keys/signing.pem', 'DO NOT READ')
    public = put(tmp_path, 'experiments/mvp/pipeline/package/fixtures/published-public-key.pem', 'PUBLIC')
    original = Path.open
    def guarded(path, *args, **kwargs):
        assert path != private
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    selected = evidence.inventory(tmp_path)
    assert private.relative_to(tmp_path).as_posix() not in selected['paths']
    assert public.relative_to(tmp_path).as_posix() in selected['paths']
    assert selected['errors'] == []
    put(tmp_path, 'app/fill-v6.json', {'{{bad}}': {'source': private.relative_to(tmp_path).as_posix()}})
    assert any('private key excluded' in error for error in evidence.inventory(tmp_path, scope='film')['errors'])


def test_sealed_sources_not_opened(tmp_path, monkeypatch):
    sealed = put(tmp_path, 'experiments/mvp/reader/v3/sealed/report.json', {'secret': True})
    put(tmp_path, 'app/fill-v6.json', {'{{bad}}': {'source': sealed.relative_to(tmp_path).as_posix()}})
    original = Path.open
    def guarded(path, *args, **kwargs):
        assert path != sealed
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    assert any('sealed evidence excluded' in error for error in evidence.inventory(tmp_path, scope='film')['errors'])


def test_evidence_directory_symlink_rejected(tmp_path):
    target = put(tmp_path, 'target/value.json', {})
    prefix = tmp_path / 'experiments/mvp/ui/cache/v6-hero'
    prefix.mkdir(parents=True)
    (prefix / 'linked').symlink_to(target.parent, target_is_directory=True)
    assert any('symlink excluded' in error for error in evidence.inventory(tmp_path, scope='film')['errors'])


def test_evidence_leaf_links_require_independently_pinned_targets(tmp_path):
    from experiments.mvp.pipeline.freeze import links
    target = put(tmp_path, 'experiments/mvp/pipeline/lattice/store/value.json', {})
    prefix = tmp_path / 'experiments/mvp/ui/cache/v6-hero'
    prefix.mkdir(parents=True)
    linked = prefix / 'linked.json'
    linked.symlink_to(target)
    name = linked.relative_to(tmp_path).as_posix()
    selected = evidence.inventory(tmp_path, scope='film')
    assert selected['errors'] == []
    assert name in selected['symlink_paths']
    assert name not in selected['paths']
    assert links.inspect(tmp_path, name, selected['paths'])['resolved_target'] == target.relative_to(tmp_path).as_posix()
    with pytest.raises(ValueError, match='not independently pinned'):
        links.inspect(tmp_path, name, [])
    linked.unlink()
    linked.symlink_to(tmp_path.parent / 'outside.json')
    assert any('outside repository' in error for error in evidence.inventory(tmp_path, scope='film')['errors'])


def test_fixture_provenance_matches_mismatches_and_absent_not_invented(tmp_path):
    prefix = 'experiments/mvp/pipeline/xray/fixtures/'
    matching = {'engine_manifest_sha256': 'engine', 'binding_source_sha256': 'binding', 'adapter_sha256': 'reader'}
    put(tmp_path, prefix + 'matching.json', matching)
    put(tmp_path, prefix + 'old.json', dict(matching, adapter_sha256='old-reader'))
    put(tmp_path, prefix + 'no-identities.json', {'a': 1})
    put(tmp_path, prefix + 'missing-frozen-value.json', {'engine_manifest_sha256': 'engine'})
    names = [prefix + name for name in ('matching.json', 'old.json', 'no-identities.json', 'missing-frozen-value.json')]
    context = {'engine': {'final_manifest_sha256': 'engine'}, 'binding_source_sha256': 'binding',
               'adapters': {'reader': {'identity': {'adapter_sha256': 'reader'}}, 'intake': {'status': 'cut'}}}
    described = evidence.describe(tmp_path, hashes(tmp_path, names), context)
    rows = described['fixture_provenance']
    assert rows[prefix + 'matching.json']['label'] == 'computed on the frozen code'
    assert rows[prefix + 'old.json']['label'] == 'computed on pre-freeze tree old-reader'
    assert rows[prefix + 'old.json']['mismatches'][0]['expected'] == ['reader']
    assert prefix + 'no-identities.json' not in rows
    context['engine'] = {}
    unknown = evidence.describe(tmp_path, hashes(tmp_path, names), context)
    assert unknown['fixture_provenance'][prefix + 'missing-frozen-value.json']['label'].startswith('computed on pre-freeze tree')


def test_model_names_reported_models_and_embedded_stage_notes(tmp_path):
    spark = 'src/llm/spark/client.py'
    voice = 'experiments/mvp/voice/asr.py'
    fixture = 'experiments/mvp/pipeline/radio/fixtures/recorded.json'
    put(tmp_path, spark, 'MODEL = "muse-spark-1.3"\n')
    put(tmp_path, voice, 'MODEL = "muse-voice-transcribe-1.0"\n')
    put(tmp_path, fixture, {'reported_model': 'voice-revision-7', 'model': 'muse-voice-transcribe-1.0',
                           'notes': json.dumps({'engine_manifest': {'manifest_sha256': 'old-engine'}})})
    result = evidence.describe(tmp_path, hashes(tmp_path, [spark, voice, fixture]), {'engine': {'final_manifest_sha256': 'frozen'}})
    assert result['api_models']['Spark']['names'] == ['muse-spark-1.3']
    assert result['api_models']['Voice']['names'] == ['muse-voice-transcribe-1.0']
    assert result['fixture_reported_models'][fixture]['reported_model'] == ['voice-revision-7']
    assert result['fixture_provenance'][fixture]['mismatches'][0]['recorded'] == 'old-engine'


def test_review_manifest_is_authoritative_when_final_differs(tmp_path):
    fixture = 'experiments/mvp/pipeline/xray/fixtures/review.json'
    put(tmp_path, fixture, {'engine_manifest_sha256': 'review-fixed-tree'})
    result = evidence.describe(tmp_path, hashes(tmp_path, [fixture]), {
        'engine': {'review_manifest_sha256': 'review-fixed-tree', 'final_manifest_sha256': 'older-final-tree'}})
    assert result['fixture_provenance'][fixture]['label'] == 'computed on the frozen code'
    assert result['fixture_provenance'][fixture]['checks'][0]['expected'] == ['review-fixed-tree']


def test_binding_digest_uses_canonical_relative_runtime_names(tmp_path):
    from experiments.mvp.pipeline.lattice.binding_sources import runtime_fingerprint
    prefix = 'experiments/mvp/pipeline/binding/'
    paths = [prefix + '__init__.py', prefix + 'adapter.py', prefix + 'runtime.py']
    for name in paths:
        put(tmp_path, name, '# synthetic runtime\n')
    files = hashes(tmp_path, paths)
    expected = runtime_fingerprint(tmp_path / prefix)
    assert evidence._binding_digest(tmp_path, files) == expected
    fixture = 'experiments/mvp/pipeline/lattice/store/binding.json'
    put(tmp_path, fixture, {'binding_source_sha256': expected, 'engine_manifest_sha256': 'engine'})
    result = evidence.describe(tmp_path, files | hashes(tmp_path, [fixture]), {'engine': {'review_manifest_sha256': 'engine'}, 'files': files})
    assert result['fixture_provenance'][fixture]['label'] == 'computed on the frozen code'


@pytest.mark.parametrize('field', ['binding_snapshot', 'implementation'])
def test_binding_only_identity_is_not_skipped_by_marker_scan(tmp_path, field):
    fixture = 'experiments/mvp/pipeline/xray/fixtures/' + field + '.json'
    put(tmp_path, fixture, {field: {'source_sha256': 'historical-source'}})
    result = evidence.describe(tmp_path, hashes(tmp_path, [fixture]), {})
    row = result['fixture_provenance'][fixture]
    assert row['label'] == 'computed on pre-freeze tree historical-source'
    assert row['mismatches'][0]['field'].endswith('/' + field + '/source_sha256')


def test_each_film_figure_citation_pins_regular_source_and_names_owner(tmp_path):
    figures = 'experiments/mvp/ui/fixtures/film-figures.json'
    source = 'experiments/mvp/reader/v3/GPU_HANDOFF.md'
    put(tmp_path, source, '# fixed reference\n')
    put(tmp_path, 'docs/impact.md', '# Projection\n')
    put(tmp_path, 'experiments/mvp/reader/v3/results-final/only-a-directory/child.json', {})
    put(tmp_path, figures, {'figures': {
        'reference': {'value': '126', 'named_source': source + ', fixed reference; its summary sits in the sealed folder'},
        'projection': {'value': '1.2', 'named_source': 'docs/impact.md, total annual prize'},
        'missing': {'value': '2', 'named_source': 'experiments/mvp/missing.json, shot 12'},
        'directory': {'value': '3', 'named_source': 'experiments/mvp/reader/v3/results-final/only-a-directory, summary'},
        'no-source': {'value': '4'},
    }})
    selected = evidence.inventory(tmp_path, scope='film')
    assert source in selected['paths']
    assert 'docs/impact.md' in selected['paths']
    assert selected['figure_sources']['reference'] == [source]
    assert {row['figure'] for row in selected['unresolved_figures']} == {'missing', 'directory', 'no-source'}
    assert all(row['owner'] == 'p22-demo' for row in selected['unresolved_figures'])
    assert len([error for error in selected['errors'] if 'p22-demo figure source unresolved' in error]) == 3


def test_app_result_paths_resolved_without_importing_app(tmp_path):
    put(tmp_path, 'app/v6.py', '''
raise RuntimeError("This module must never be imported")
ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "experiments/mvp/pipeline"
FILL = ROOT / "experiments/mvp/video/fill-v6"
RESULTS = (PIPELINE / "intake/measurements/not-used.json", PIPELINE / "intake/measurements/development.json")
ADOPTIONS = tuple(ROOT / "experiments/mvp/reader/v3/results-final" / name for name in ("new.json", "old.json"))
PACKAGE = PIPELINE / "package/fixtures/complete"

def _fill():
    for path in (FILL.with_suffix(".json"), FILL / "fill.json"):
        data = read_optional(path)
    return data

def development():
    path = RESULTS[1]
    return _json(path)

def adopted():
    path = next(p for p in ADOPTIONS if p.is_file())
    return _json(path)

def rule_counts():
    return _json(PACKAGE.parent / "verification-summary.json")

def _helper(case):
    return _json(case / "final.json")

def caller():
    case = PIPELINE / "intake/fixtures/demo"
    return _helper(case)
''')
    expected = {
        'experiments/mvp/video/fill-v6.json',
        'experiments/mvp/pipeline/intake/measurements/development.json',
        'experiments/mvp/reader/v3/results-final/new.json',
        'experiments/mvp/reader/v3/results-final/old.json',
        'experiments/mvp/pipeline/package/fixtures/verification-summary.json',
        'experiments/mvp/pipeline/intake/fixtures/demo/final.json',
    }
    for name in expected:
        put(tmp_path, name, {})
    app_sources = evidence.app_result_sources(tmp_path)
    assert set(app_sources['paths']) == expected
    assert {row['path'] for row in app_sources['unresolved']} == {'experiments/mvp/video/fill-v6/fill.json'}
    assert all(row['owner'] == 'p22-demo' for row in app_sources['unresolved'])
    assert expected <= set(evidence.inventory(tmp_path, scope='film')['paths'])


def test_app_dynamic_filename_pins_prefix_and_never_live_outputs(tmp_path):
    put(tmp_path, 'app/v6.py', '''
ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / "experiments/mvp/voice/runs/demo"
LIVE_DIR = ROOT / "experiments/mvp/ui/hero-live"
def recorded(identity):
    return _json(VOICE / "checkpoints" / f"{identity}-radio.json")
def new_job():
    return _json(LIVE_DIR / "future-job/outcome.json")
''')
    prefix = 'experiments/mvp/voice/runs/demo/checkpoints'
    put(tmp_path, prefix + '/chosen-radio.json', {})
    put(tmp_path, 'experiments/mvp/ui/hero-live/future-job/outcome.json', {})
    selected = evidence.inventory(tmp_path, scope='film')
    assert prefix + '/chosen-radio.json' in selected['paths']
    assert not any('/hero-live/' in name for name in selected['app_result_sources']['paths'])
    assert 'experiments/mvp/ui/hero-live/future-job/outcome.json' in selected['paths']


def test_pipeline_scope_is_independent_of_film_contents(tmp_path, monkeypatch):
    common = [
        'experiments/mvp/pipeline/lattice/hero.json',
        'experiments/mvp/pipeline/runner/measurements/profile-study-one/summary.json',
        'experiments/mvp/pipeline/binding/measurements/deadline-live-one/result.json',
        'experiments/mvp/reader/v3/results-final/final-adoption.json',
        'experiments/mvp/voice/runs/v5-1.3/recorded.json',
        'experiments/mvp/rules.json',
    ]
    for name in common:
        put(tmp_path, name, {})
    initial = evidence.inventory(tmp_path)
    assert set(common) <= set(initial['paths'])
    put(tmp_path, 'app/v6.py', 'not valid python !!!')
    put(tmp_path, 'app/v6_hero.json', {'reader_recordings': 'experiments/mvp/extra/film-only.json'})
    put(tmp_path, 'experiments/mvp/ui/fixtures/film-figures.json', '{malformed')
    put(tmp_path, 'experiments/mvp/video/fill-v6.json', {'{{unsourced}}': 17})
    put(tmp_path, 'experiments/mvp/video/STORY-NEEDS-v6.md', '# Changed story')
    put(tmp_path, 'experiments/mvp/ui/cache/other-cache/result.json', {})
    put(tmp_path, 'experiments/mvp/extra/film-only.json', {})
    original = Path.open
    def guarded(path, *args, **kwargs):
        name = path.relative_to(tmp_path).as_posix()
        assert not name.startswith(('app/', 'experiments/mvp/ui/', 'experiments/mvp/video/'))
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', guarded)
        assert evidence.inventory(tmp_path) == initial
    film = evidence.inventory(tmp_path, scope='film')
    assert 'experiments/mvp/ui/cache/other-cache/result.json' in film['paths']
    assert 'experiments/mvp/extra/film-only.json' in film['paths']
    assert any('film figures must be a JSON object' in error for error in film['errors'])
    assert any('fill value has no source path' in error for error in film['errors'])
    assert film['app_result_sources']['unresolved'][0]['owner'] == 'p22-demo'
    assert initial['fill_sources'] == initial['figure_sources'] == {}
    assert initial['app_result_sources'] == {'paths': [], 'references': [], 'unresolved': []}
    assert initial['scope'] == 'pipeline'
    assert film['scope'] == 'film'


def test_unknown_evidence_scope_rejected(tmp_path):
    with pytest.raises(ValueError, match='unknown evidence scope'):
        evidence.inventory(tmp_path, scope='all')


def test_generated_products_are_film_only_and_override_run_exclusions(tmp_path):
    from experiments.mvp.pipeline.freeze import product_outputs
    product_paths = [
        product_outputs.HERO + '/package/receipt.json',
        'experiments/mvp/pipeline/sealed/runs/final/jobs/row.json',
        'experiments/mvp/pipeline/sealed/measurements/study/summary.json',
        product_outputs.EXPLAIN + '/cache/prefetch.json',
        'experiments/mvp/pipeline/reader_final/readings/readings.jsonl',
        'experiments/mvp/pipeline/reader_final/readings/queue.json',
    ]
    for name in product_paths:
        put(tmp_path, name, {})
    timing = 'experiments/mvp/pipeline/reader_final/readings/timing.json'
    put(tmp_path, timing, {'identity': {'adapter_path': 'adopted'}})
    pipeline = evidence.inventory(tmp_path)
    assert not set(product_paths) & set(pipeline['paths'])
    film = evidence.inventory(tmp_path, scope='film')
    assert set(product_paths) <= set(film['paths'])
    assert timing not in product_paths
    # Product creation and mutation do not change runtime evidence.
    put(tmp_path, product_paths[0], {'pipeline_sha256': 'changed'})
    assert evidence.inventory(tmp_path) == pipeline


def test_product_bundle_receipt_binds_siblings_but_null_or_wrong_fields_fail(tmp_path):
    from experiments.mvp.pipeline.freeze import product_outputs
    expected = 'a' * 64
    hero = product_outputs.HERO
    receipt = hero + '/package/receipt.json'
    payload = hero + '/package/plan.json'
    put(tmp_path, receipt, {'pipeline_sha256': expected})
    put(tmp_path, payload, {'stored_plan': True})
    assert evidence.validate_product_outputs(tmp_path, expected, only=[hero]) == []
    bound = evidence.product_output_bindings(tmp_path, pipeline_sha256=expected, only=[hero])
    assert bound['bundles'][hero]['files'] == sorted([payload, receipt])
    put(tmp_path, hero + '/outcome.json', {'receipt': {'pipeline_sha256': None}})
    errors = evidence.validate_product_outputs(tmp_path, expected, only=[hero])
    assert any('p111-package' in error and 'recorded None' in error for error in errors)
    put(tmp_path, hero + '/outcome.json', {'receipt': {'pipeline_sha256': 'b' * 64}})
    assert any('mismatch' in error for error in evidence.validate_product_outputs(tmp_path, expected, only=[hero]))


def test_sealed_runs_cannot_borrow_sibling_binding_and_jsonl_is_checked(tmp_path):
    expected = 'a' * 64
    root = 'experiments/mvp/pipeline/sealed/runs'
    put(tmp_path, root + '/new/receipt.json', {'pipeline_sha256': expected})
    put(tmp_path, root + '/old/rows.jsonl', json.dumps({'row': 1}) + '\n')
    result = evidence.product_output_bindings(tmp_path, pipeline_sha256=expected)
    assert root + '/old' in result['bundles']
    assert any('binding missing' in error and 'p112-sealed' in error and '/old' in error for error in result['errors'])
    put(tmp_path, root + '/old/rows.jsonl', json.dumps({'pipeline_sha256': expected}) + '\n')
    assert evidence.validate_product_outputs(tmp_path, expected) == []
    put(tmp_path, root + '/old/rows.jsonl', json.dumps({'pipeline_sha256': 'b' * 64}) + '\n')
    assert any('line-1' in error for error in evidence.validate_product_outputs(tmp_path, expected))


def test_explicit_hero_missing_or_empty_fails_but_no_selected_products_is_valid(tmp_path):
    from experiments.mvp.pipeline.freeze import product_outputs
    expected = 'a' * 64
    assert evidence.validate_product_outputs(tmp_path, expected) == []
    errors = evidence.validate_product_outputs(tmp_path, expected, only=[product_outputs.HERO])
    assert any('output missing' in error and 'p111-package' in error for error in errors)
    (tmp_path / product_outputs.HERO).mkdir(parents=True)
    assert any('binding missing' in error for error in evidence.validate_product_outputs(tmp_path, expected, only=[product_outputs.HERO]))


def test_reader_prepared_files_share_bundle_and_suites_need_no_binding(tmp_path):
    from experiments.mvp.pipeline.freeze import product_outputs
    expected = 'a' * 64
    readings = 'experiments/mvp/pipeline/reader_final/readings'
    put(tmp_path, readings + '/readings.jsonl', json.dumps({'evidence': {'pipeline_sha256': expected}}) + '\n')
    put(tmp_path, readings + '/queue.json', {'prepared': []})
    put(tmp_path, product_outputs.SUITES + '/film/repo-counts.json', {'tests': 1})
    put(tmp_path, product_outputs.SUITES + '/unit-tmp-fake/key.json', {})
    bound = evidence.product_output_bindings(tmp_path, pipeline_sha256=expected)
    assert bound['errors'] == []
    assert list(bound['bundles']) == [readings]
    film = evidence.inventory(tmp_path, scope='film')
    assert product_outputs.SUITES + '/film/repo-counts.json' in film['paths']
    assert not any('unit-tmp-fake' in name for name in film['paths'])
    put(tmp_path, readings + '/readings.jsonl', json.dumps({'row': 1}) + '\n')
    assert any('p32-showcases' in error for error in evidence.validate_product_outputs(tmp_path, expected))


def test_after_hero_cli_guards_pipeline_manifest_and_validates_requested_bundle(tmp_path, capsys):
    from experiments.mvp.pipeline.freeze import build, product_outputs
    manifest = {'schema_version': 'stowlm.pipeline-freeze/1', 'task': '2.1', 'status': 'frozen',
                'scope': 'pipeline', 'dry_run': False, 'readiness': {'ready': True},
                'files': {}, 'source_files': {}, 'tree_sha256': build.tree_digest({}),
                'selection': {'import_closure': {'paths': []}}, 'evidence': {'errors': []}}
    path = put(tmp_path, build.FREEZE + '/manifest-pipeline.json', manifest)
    args = ['--root', str(tmp_path), '--pipeline-manifest', str(path), '--only', product_outputs.HERO]
    assert evidence.main(args) == 1
    assert 'p111-package' in capsys.readouterr().out
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    put(tmp_path, product_outputs.HERO + '/package/receipt.json', {'pipeline_sha256': digest})
    assert evidence.main(args) == 0
    assert json.loads(capsys.readouterr().out)['ready'] is True
    path.write_text('{}')
    assert evidence.main(args) == 1
    assert 'verification failed' in capsys.readouterr().out


def test_film_guard_rechecks_pipeline_binding_even_if_product_hash_updated(tmp_path):
    from experiments.mvp.pipeline.freeze import build, product_outputs, verify
    base = {'schema_version': 'stowlm.pipeline-freeze/1', 'task': '2.1', 'status': 'frozen',
            'scope': 'pipeline', 'dry_run': False, 'readiness': {'ready': True},
            'files': {}, 'source_files': {}, 'tree_sha256': build.tree_digest({}),
            'selection': {'import_closure': {'paths': []}}, 'evidence': {'errors': []}}
    pipeline = put(tmp_path, build.FREEZE + '/manifest-pipeline.json', base)
    pipeline_digest = hashlib.sha256(pipeline.read_bytes()).hexdigest()
    receipt_name = product_outputs.HERO + '/package/receipt.json'
    receipt = put(tmp_path, receipt_name, {'pipeline_sha256': pipeline_digest})
    film = dict(base, scope='film', files=hashes(tmp_path, [receipt_name]),
                pipeline_layer={'path': pipeline.relative_to(tmp_path).as_posix(),
                                'manifest_sha256': pipeline_digest, 'tree_digest': base['tree_sha256']})
    film_path = put(tmp_path, build.FREEZE + '/manifest-film.json', film)
    assert verify.require_frozen(film_path, root=tmp_path)['tree_digest'] == base['tree_sha256']
    receipt.write_text(json.dumps({'pipeline_sha256': 'b' * 64}))
    film['files'] = hashes(tmp_path, [receipt_name])
    film_path.write_text(json.dumps(film))
    with pytest.raises(verify.FreezeDrift) as caught:
        verify.require_frozen(film_path, root=tmp_path)
    assert any('pipeline_sha256 mismatch' in row.get('detail', '') and 'p111-package' in row.get('detail', '')
               for row in caught.value.drifts)
    # Products remain independent of the pipeline-layer guard.
    assert verify.require_frozen(pipeline, root=tmp_path)['manifest_sha256'] == pipeline_digest


def test_diagnostic_history_is_pinned_without_claiming_current_pipeline_binding(tmp_path):
    from experiments.mvp.pipeline.freeze import product_outputs
    expected = 'a' * 64
    current = product_outputs.HERO + '/package/receipt.json'
    historical = [
        'experiments/mvp/pipeline/package/fixtures/history/old/receipt.json',
        'experiments/mvp/pipeline/package/fixtures/failed-hero-build-old/outcome.json',
        'experiments/mvp/pipeline/package/fixtures/.hero-build-old/receipt.json',
    ]
    put(tmp_path, current, {'pipeline_sha256': expected})
    for name in historical:
        put(tmp_path, name, {'pipeline_sha256': None, 'engine_manifest_sha256': 'historical-engine'})
    selected = evidence.inventory(tmp_path, scope='film')
    assert set(historical) <= set(selected['paths'])
    binding = evidence.product_output_bindings(tmp_path, hashes(tmp_path, selected['paths']), expected)
    assert binding['errors'] == []
    assert set(binding['bundles']) == {product_outputs.HERO}
    provenance = evidence.describe(tmp_path, hashes(tmp_path, historical), {'engine': {'review_manifest_sha256': 'current-engine'}})
    assert all(provenance['fixture_provenance'][name]['label'].startswith('computed on pre-freeze tree') for name in historical)


def test_lattice_entries_need_engine_and_binding_and_use_verified_pipeline_values(tmp_path):
    store = 'experiments/mvp/pipeline/lattice/store'
    entries = {
        'matching': {'engine_manifest_sha256': 'frozen-engine', 'binding_source_sha256': 'frozen-binding', 'plan': {}},
        'old': {'engine_manifest_sha256': 'old-engine', 'binding_source_sha256': 'old-binding', 'plan': {}, 'source_tree_sha256': 'old-tree'},
        'missing-engine': {'binding_source_sha256': 'frozen-binding', 'plan': {}},
        'missing-binding': {'engine_manifest_sha256': 'frozen-engine', 'plan': {}},
        'missing-both': {'plan': {}},
    }
    names = []
    for name, entry in entries.items():
        names.append(store + '/' + name + '.json')
        put(tmp_path, names[-1], entry)
    files = hashes(tmp_path, names)
    pipeline = {'scope': 'pipeline', 'engine': {'review_manifest_sha256': 'frozen-engine'},
                'evidence': {'binding_source_sha256': 'frozen-binding'}}
    # Film-side recomputation must never replace the verified layer's identity.
    film = {'scope': 'film', 'engine': {'review_manifest_sha256': 'old-engine'},
            'binding_source_sha256': 'old-binding'}
    described = evidence.describe(tmp_path, files, film, pipeline_manifest=pipeline)
    rows = described['lattice_provenance']
    assert rows[store + '/matching.json']['label'] == 'computed on the frozen code'
    assert rows[store + '/old.json']['label'] == 'computed on pre-freeze tree old-tree'
    for name in ('missing-engine', 'missing-binding', 'missing-both'):
        assert rows[store + '/' + name + '.json']['label'].startswith('computed on pre-freeze tree')
        assert rows[store + '/' + name + '.json']['missing_fields']
    assert rows[store + '/missing-both.json']['label'] == 'computed on pre-freeze tree unknown'
    assert described['fixture_provenance'][store + '/old.json'] == rows[store + '/old.json']
    assert evidence.product_output_bindings(tmp_path, files, 'a' * 64, pipeline_manifest=pipeline)['errors'] == []


def test_nested_lattice_coverage_entries_are_never_skipped_by_path_or_missing_markers(tmp_path):
    source = 'experiments/mvp/pipeline/lattice/hero-coverage/nested/work/results.json'
    put(tmp_path, source, {'entries': [
        {'key': 'matching', 'plan': {}, 'engine_manifest_sha256': 'engine', 'binding_source_sha256': 'binding'},
        {'key': 'missing', 'plan': {}},
        {'key': 'old', 'status': 'checked_plan', 'engine_manifest_sha256': 'old', 'binding_source_sha256': 'binding'},
    ]})
    pipeline = {'engine': {'review_manifest_sha256': 'engine'}, 'evidence': {'binding_source_sha256': 'binding'}}
    rows = evidence.lattice_provenance(tmp_path, hashes(tmp_path, [source]), pipeline)
    assert set(rows) == {source + '#/entries/' + str(index) for index in range(3)}
    assert rows[source + '#/entries/0']['label'] == 'computed on the frozen code'
    assert rows[source + '#/entries/1']['missing_fields'] == ['engine_manifest_sha256', 'binding_source_sha256']
    assert rows[source + '#/entries/2']['label'] == 'computed on pre-freeze tree old'


def test_lattice_expected_identity_uses_pinned_file_map_over_metadata(tmp_path):
    from experiments.mvp.pipeline.lattice.binding_sources import runtime_fingerprint
    source = 'experiments/mvp/pipeline/binding/adapter.py'
    put(tmp_path, source, '# runtime\n')
    review = 'experiments/mvp/frontier/engine/source-manifest-0.13.0-review-fixes.json'
    expected_binding = runtime_fingerprint(tmp_path / 'experiments/mvp/pipeline/binding')
    entry = 'experiments/mvp/pipeline/lattice/store/entry.json'
    put(tmp_path, entry, {'engine_manifest_sha256': 'actual-engine-pin', 'binding_source_sha256': expected_binding})
    pipeline = {'files': {**hashes(tmp_path, [source]), review: 'actual-engine-pin'},
                'engine': {'review_manifest_sha256': 'wrong-metadata'},
                'evidence': {'binding_source_sha256': 'wrong-metadata'}}
    row = evidence.lattice_provenance(tmp_path, hashes(tmp_path, [entry]), pipeline)[entry]
    assert row['label'] == 'computed on the frozen code'


@pytest.mark.parametrize('folder', ['repo', 'repo-health'])
def test_repository_reports_cannot_be_pinned_even_by_film_citation(tmp_path, folder, monkeypatch):
    report = 'experiments/mvp/pipeline/' + folder + '/report.json'
    target = put(tmp_path, report, {'report': 'must not be read'})
    put(tmp_path, 'experiments/mvp/ui/fixtures/film-figures.json', {
        'figures': {'forbidden': {'value': '1', 'named_source': report + ', required claim'}}})
    put(tmp_path, 'experiments/mvp/video/fill-v6.json', {'{{forbidden}}': {'source': report}})
    original = Path.open
    def guarded(path, *args, **kwargs):
        assert path != target
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    for scope in ('pipeline', 'film'):
        selected = evidence.inventory(tmp_path, scope=scope)
        assert report not in selected['paths']
        assert report not in selected['symlink_paths']
    film = evidence.inventory(tmp_path, scope='film')
    assert film['unresolved_figures'][0]['owner'] == 'p22-demo'
    assert any('unpinned fill source' in error for error in film['errors'])


def test_film_guard_trusts_pipeline_layer_and_accepts_honest_historical_lattice_labels(tmp_path):
    from experiments.mvp.pipeline.freeze import build, verify
    pipeline_value = {'schema_version': 'stowlm.pipeline-freeze/1', 'task': '2.1', 'status': 'frozen',
                      'scope': 'pipeline', 'dry_run': False, 'readiness': {'ready': True},
                      'files': {}, 'source_files': {}, 'tree_sha256': build.tree_digest({}),
                      'selection': {'import_closure': {'paths': []}},
                      'engine': {'review_manifest_sha256': 'frozen-engine'},
                      'evidence': {'errors': [], 'binding_source_sha256': 'frozen-binding'}}
    pipeline = put(tmp_path, build.FREEZE + '/manifest-pipeline.json', pipeline_value)
    digest = hashlib.sha256(pipeline.read_bytes()).hexdigest()
    entry = 'experiments/mvp/pipeline/lattice/hero-coverage/deep/results.json'
    put(tmp_path, entry, {'entries': [{'key': 'old', 'plan': {}, 'engine_manifest_sha256': 'old-engine',
                                      'binding_source_sha256': 'old-binding', 'pipeline_sha256': None},
                                     {'key': 'unknown', 'plan': {}}]})
    files = hashes(tmp_path, [entry])
    labels = evidence.lattice_provenance(tmp_path, files, pipeline_value)
    film = dict(pipeline_value, scope='film', files=files,
                engine={'review_manifest_sha256': 'old-engine'},
                evidence={'errors': [], 'binding_source_sha256': 'old-binding', 'lattice_provenance': labels,
                          'fixture_provenance': labels, 'product_output_bindings': {'errors': [], 'lattice_provenance': labels}},
                pipeline_layer={'path': pipeline.relative_to(tmp_path).as_posix(), 'manifest_sha256': digest,
                                'tree_digest': pipeline_value['tree_sha256']})
    film_path = put(tmp_path, build.FREEZE + '/manifest-film.json', film)
    assert verify.require_frozen(film_path, root=tmp_path)
    assert verify.require_frozen(pipeline, root=tmp_path)['manifest_sha256'] == digest
    forged = json.loads(json.dumps(labels))
    forged[entry + '#/entries/0']['label'] = 'computed on the frozen code'
    film['evidence']['lattice_provenance'] = forged
    film_path.write_text(json.dumps(film))
    with pytest.raises(verify.FreezeDrift) as caught:
        verify.require_frozen(film_path, root=tmp_path)
    assert any(row['reason'] == 'lattice_provenance_changed' for row in caught.value.drifts)
