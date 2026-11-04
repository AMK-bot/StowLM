"""D277 products are mutable after layer one and pinned by the film layer."""
from __future__ import annotations

from pathlib import Path

import pytest

from experiments.mvp.pipeline.freeze import build, evidence, product_outputs as products
from experiments.mvp.pipeline.freeze.test_freeze import tree, put
from experiments.mvp.pipeline.freeze.verify import verify


@pytest.mark.parametrize('name', [
    products.HERO + '/package/receipt.json',
    build.PIPELINE + '/package/hero-run-anchor.json',
    build.PIPELINE + '/package/fixtures/history/old/package/receipt.json',
    build.PIPELINE + '/package/fixtures/failed-hero-build-2026/outcome.json',
    build.PIPELINE + '/package/fixtures/.hero-build-123/package/receipt.json',
    build.PIPELINE + '/sealed/runs/final/report.json',
    build.PIPELINE + '/sealed/measurements/native-study/report.json',
    build.PIPELINE + '/sealed/results/final.json',
    products.EXPLAIN + '/cache/index.json',
    products.LATTICE_STORE + '/plan.json',
    build.PIPELINE + '/explain/budget/.spend.json',
    build.PIPELINE + '/reader_final/readings/readings.jsonl',
    build.PIPELINE + '/radio/readings.json',
    build.PIPELINE + '/xray/fixtures/real-trace.json',
    products.SUITES + '/pipeline/repo-counts.json',
])
def test_mutable_product_is_pipeline_exclusion_and_film_evidence(name):
    assert products.is_product(name)
    assert products.excluded_pipeline(name)
    assert not build.in_scope(name, 'pipeline')
    assert build.in_scope(name, 'film')


@pytest.mark.parametrize('name', [
    build.PIPELINE + '/package/regenerate.py',
    build.PIPELINE + '/sealed/driver.py',
    build.PIPELINE + '/explain/run_prefetch.py',
    build.PIPELINE + '/reader_final/readings/timing.json',
    build.PIPELINE + '/copilot/replays/hero.json',
    build.PIPELINE + '/xray/fixtures/real-block-trace.json',
    build.PIPELINE + '/sealed/fixtures/d275/protocol.json',
])
def test_runtime_sources_and_adopted_identity_remain_pipeline_pins(name):
    assert not products.is_product(name)
    assert build.in_scope(name, 'pipeline')


def test_code_and_explicit_imports_are_never_product_exclusions():
    code = products.HERO + '/recorded_source.py'
    resource = products.HERO + '/imported-resource.json'
    assert products.is_product(code)
    assert not products.excluded_pipeline(code)
    assert not products.excluded_pipeline(resource, import_closure=[resource])


def test_regeneration_products_and_suite_writes_do_not_drift_pipeline(tree):
    manifest = build.build_manifest(tree)
    path = put(tree, build.FREEZE + '/unit-pipeline.json', manifest)
    additions = [
        products.HERO + '/package/receipt.json',
        build.PIPELINE + '/package/hero-run-anchor.json',
        build.PIPELINE + '/package/fixtures/history/old/package/receipt.json',
        build.PIPELINE + '/package/fixtures/failed-hero-build-1/outcome.json',
        build.PIPELINE + '/package/fixtures/.hero-build-1/receipt.json',
        build.PIPELINE + '/sealed/runs/final/report.json',
        build.PIPELINE + '/sealed/measurements/final/row.json',
        products.EXPLAIN + '/cache/index.json',
        build.PIPELINE + '/explain/fixtures/readiness.json',
        build.PIPELINE + '/reader_final/readings/readings.jsonl',
        products.SUITES + '/pipeline/repo-counts.json',
    ]
    for name in additions:
        put(tree, name, {'new_product': True})
    assert verify(path, root=tree) == []
    put(tree, products.HERO + '/package/receipt.json', {'regenerated': True})
    (tree / build.PIPELINE / 'package/fixtures/.hero-build-1/receipt.json').unlink()
    assert verify(path, root=tree) == []


def test_film_includes_products_even_in_runs_measurements_and_cache(tree):
    names = [products.HERO + '/package/receipt.json',
             build.PIPELINE + '/sealed/runs/final/report.json',
             build.PIPELINE + '/sealed/measurements/final/row.json',
             products.EXPLAIN + '/cache/index.json',
             build.PIPELINE + '/reader_final/readings/readings.jsonl']
    for name in names:
        put(tree, name, {'pipeline_sha256': 'a' * 64})
    pipeline = evidence.inventory(tree, scope='pipeline')
    film = evidence.inventory(tree, scope='film')
    assert not set(names) & set(pipeline['paths'])
    assert set(names) <= set(film['paths'])
    assert set(names) <= set(build.inventory_paths(tree, scope='film', evidence_selection=film, import_closure=[]))


def test_suite_temporary_trees_are_not_film_products(tree):
    receipt = products.SUITES + '/pipeline/repo-counts.json'
    temporary = products.SUITES + '/pipeline/unit-tmp-suite/fake.json'
    put(tree, receipt, {'tests': 1})
    put(tree, temporary, {'temporary': True})
    names = products.film_paths(tree)
    assert receipt in names and temporary not in names
    assert products.SUITES not in names
    assert products.SUITES not in products.bundle_roots(tree)


def test_anchor_and_prefetch_helpers_share_own_output_bundles():
    assert products.bundle_name(build.PIPELINE + '/package/hero-run-anchor.json') == products.HERO
    assert products.bundle_name(build.PIPELINE + '/explain/fixtures/readiness.json') == products.EXPLAIN
    assert products.bundle_name(build.PIPELINE + '/explain/budget/.spend.json') == products.EXPLAIN
    assert products.bundle_name(build.PIPELINE + '/reader_final/readings/queue.json') == build.PIPELINE + '/reader_final/readings'


@pytest.mark.parametrize(('name', 'required'), [
    (products.HERO, True),
    (build.PIPELINE + '/package/hero-run-anchor.json', True),
    (build.PIPELINE + '/package/fixtures/history/old/package/receipt.json', False),
    (build.PIPELINE + '/package/fixtures/failed-hero-build-1/receipt.json', False),
    (build.PIPELINE + '/package/fixtures/.hero-build-1/receipt.json', False),
    (products.SUITES + '/pipeline/counts.json', False),
    (build.PIPELINE + '/reader_final/readings', True),
])
def test_published_products_require_current_binding_and_diagnostics_preserve_their_own(name, required):
    assert products.binding_required(name) is required


@pytest.mark.parametrize('binding_errors', [[], ['product has the wrong pipeline binding']])
def test_film_build_records_product_bindings_and_refuses_bad_readiness(tree, monkeypatch, binding_errors):
    pipeline = build.build_manifest(tree)
    path = put(tree, build.FREEZE + '/unit-pipeline.json', pipeline)
    expected = build.sha256_file(path)
    called = []
    def inspect_outputs(root, files, pipeline_sha256, **kwargs):
        called.append(pipeline_sha256)
        return {'bundles': [], 'errors': binding_errors}
    monkeypatch.setattr(evidence, 'product_output_bindings', inspect_outputs, raising=False)
    film = build.build_manifest(tree, no_weights=True, scope='film', pipeline_manifest=path)
    assert called == [expected]
    assert film['readiness']['ready'] is (not binding_errors)
    assert film['evidence']['product_output_bindings']['errors'] == binding_errors


@pytest.mark.parametrize('scope', ['pipeline', 'film'])
def test_scanner_regression_corpus_escape_is_not_a_launch_input(tree, scope):
    scratch = tree / build.SCAN_SCRATCH / 'corpus/demo/fixtures/link.json'
    scratch.parent.mkdir(parents=True)
    scratch.symlink_to('/Users/example/sample@example.invalid')
    names = build.inventory_paths(tree, import_closure=[], scope=scope)
    assert not any(name.startswith(build.SCAN_SCRATCH + '/') for name in names)
    assert not build.inventory_symlinks(tree, import_closure=[], scope=scope)


def test_real_pinned_escape_is_still_rejected(tree):
    target = tree / build.PIPELINE / 'package/fixtures/link.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to('/Users/example/sample@example.invalid')
    manifest = build.build_manifest(tree, no_weights=True)
    assert manifest['readiness']['ready'] is False
    assert any('outside repository' in error for error in manifest['readiness']['errors'])


def test_audit_sources_are_excluded_even_when_named_by_import_closure(tree):
    name = build.SCAN_SCRATCH + '/helper.py'
    put(tree, name, 'VALUE=1\n')
    assert name not in build.source_paths(tree, import_closure=[name])
    (tree / name).unlink()
    (tree / name).symlink_to('/Users/example/sample@example.invalid')
    assert name not in build.source_paths(tree, import_closure=[name])


def test_hero_coverage_is_one_mutable_product_bundle_pinned_in_film(tree):
    names = [products.COVERAGE + '/state.json', products.COVERAGE + '/store/plan.json',
             products.COVERAGE + '/fixtures/result.json', products.COVERAGE + '/runs/row/receipt.json']
    manifest = build.build_manifest(tree)
    path = put(tree, build.FREEZE + '/unit-pipeline.json', manifest)
    for name in names:
        put(tree, name, {'pipeline_sha256': build.sha256_file(path)})
    pipeline = build.inventory_paths(tree, scope='pipeline', import_closure=[])
    film = build.inventory_paths(tree, scope='film', import_closure=[])
    assert not set(names) & set(pipeline)
    assert set(names) <= set(film)
    assert {products.bundle_name(name) for name in names} == {products.COVERAGE}
    assert not products.binding_required(products.COVERAGE)
    assert verify(path, root=tree) == []


def test_publishing_lattice_entry_and_catalogue_does_not_drift_pipeline(tree):
    catalogue = products.LATTICE_STORE + '/catalogue.json'
    put(tree, catalogue, {'existing': 'a' * 64})
    manifest = build.build_manifest(tree)
    path = put(tree, build.FREEZE + '/unit-pipeline.json', manifest)
    new_entry = products.LATTICE_STORE + '/published-entry.json'
    put(tree, new_entry, {'pipeline_sha256': build.sha256_file(path)})
    put(tree, catalogue, {'existing': 'a' * 64, 'published-entry': build.sha256_file(tree / new_entry)})
    assert not any(name.startswith(products.LATTICE_STORE + '/') for name in manifest['files'])
    assert verify(path, root=tree) == []
    film = build.inventory_paths(tree, scope='film', import_closure=[])
    assert catalogue in film and new_entry in film
    assert products.bundle_name(catalogue) == products.LATTICE_STORE
    assert products.bundle_name(new_entry) == products.LATTICE_STORE
    assert not products.binding_required(products.LATTICE_STORE)


@pytest.mark.parametrize('scope', ['pipeline', 'film'])
@pytest.mark.parametrize('prefix', products.EXCLUDED_ROOTS)
def test_audit_roots_are_excluded_wholesale_even_for_reported_or_imported_paths(tree, prefix, scope):
    code = prefix + '/scan.py'
    reported = prefix + '/report.json'
    escaping = prefix + '/escape.json'
    put(tree, code, 'VALUE=1\n')
    put(tree, reported, {'count': 1})
    (tree / escaping).symlink_to('/Users/example/sample@example.invalid')
    selection = {'paths': [code, reported, escaping], 'symlink_paths': [escaping], 'errors': []}
    names = build.inventory_paths(tree, import_closure=[code], evidence_selection=selection, scope=scope)
    assert not {code, reported, escaping} & set(names)
    assert not build.inventory_symlinks(tree, import_closure=[code], evidence_selection=selection, scope=scope)
    assert all(not build.in_scope(name, scope, import_closure=[code]) for name in (prefix, code, reported, escaping))


def test_growing_lattice_outputs_dont_drift_pipeline_but_film_pins_them(tree):
    value = build.build_manifest(tree)
    path = put(tree, build.FREEZE + '/unit-pipeline.json', value)
    outputs = [prefix + '/new/result.json' for prefix in products.LATTICE_ROOTS]
    outputs.extend(products.LATTICE_FILES)
    for name in outputs:
        put(tree, name, {'generated': True})
    assert verify(path, root=tree) == []
    film = build.inventory_paths(tree, scope='film', import_closure=[])
    assert set(outputs) <= set(film)
    assert all(products.lattice_output(name) and not products.binding_required(name) for name in outputs)


def test_current_lattice_code_and_static_selection_stay_frozen(tree):
    names = [build.PIPELINE + '/lattice/observer.py', build.PIPELINE + '/lattice/screen.py',
             build.PIPELINE + '/lattice/hero.json', build.PIPELINE + '/lattice/manifest.json',
             build.PIPELINE + '/lattice/campaign-control.json', build.PIPELINE + '/lattice/hero-wordings.json']
    for name in names:
        put(tree, name, '' if name.endswith('.py') else '{}')
    selected = build.source_paths(tree, import_closure=[])
    assert set(names) <= set(selected)


def test_historical_producer_source_is_output_unless_explicitly_imported(tree):
    name = products.PRODUCER_SOURCES + '/epoch/copied_source.py'
    put(tree, name, 'VALUE=1\n')
    assert name not in build.source_paths(tree, import_closure=[])
    assert name in build.source_paths(tree, import_closure=[name])
    assert name in build.inventory_paths(tree, import_closure=[name])
    assert name in build.source_paths(tree, import_closure=[], scope='film')
