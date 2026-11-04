"""Runtime artifacts stay in the feature folder; source files are untouched."""
from stowage.linear import cache
from stowage.checker import stability_liquid, stability_liquid_tables, stability_sounding, stability_tables

from experiments.mvp.pipeline.runner import runtime
from experiments.mvp.pipeline.runner import core


def test_cache_configuration_uses_local_roots_without_scanning_shared_caches(monkeypatch, tmp_path):
    targets = [(cache, '_DEFAULT_ROOT', 'linear'),
               (stability_liquid, 'CACHE_DIRECTORY', 'stability'),
               (stability_liquid_tables, 'CACHE_DIRECTORY', 'stability'),
               (stability_sounding, 'CACHE_DIRECTORY', 'stability'),
               (stability_tables, 'CACHE_DIR', 'stability')]
    monkeypatch.setattr(runtime, 'HERE', tmp_path)
    monkeypatch.setattr(runtime, '_configured', False)
    for module, name, _ in targets:
        monkeypatch.setattr(module, name, tmp_path / 'missing-shared-cache')
    for name in ('load_table_geometry', 'cached_geometry', 'migrate_legacy'):
        function = getattr(stability_tables, name)
        monkeypatch.setattr(function, '__kwdefaults__', dict(function.__kwdefaults__ or {}))
    runtime.configure_caches()
    for module, name, suffix in targets:
        assert getattr(module, name) == tmp_path / 'cache' / suffix
        assert getattr(module, name).is_dir()
    assert stability_tables.cached_geometry.__kwdefaults__['cache_dir'] == tmp_path / 'cache' / 'stability'
    runtime.configure_caches()  # Repeated packets need no directory scan.


def test_busy_output_is_refused_without_touching_its_events(tmp_path):
    (tmp_path / '.runner.lock').write_text('another run\n')
    (tmp_path / 'events.jsonl').write_text('existing events\n')
    outcome = core.run_packet({}, parser=object(), out_dir=tmp_path)
    assert outcome['status'] == 'error' and 'already in use' in outcome['message']
    assert (tmp_path / 'events.jsonl').read_text() == 'existing events\n'
    assert (tmp_path / '.runner.lock').read_text() == 'another run\n'


def test_existing_package_is_refused_and_own_lock_is_removed(tmp_path):
    (tmp_path / 'package').mkdir()
    (tmp_path / 'package' / 'package.json').write_text('existing package\n')
    outcome = core.run_packet({}, parser=object(), out_dir=tmp_path)
    assert outcome['status'] == 'error' and 'already contains a package' in outcome['message']
    assert (tmp_path / 'package' / 'package.json').read_text() == 'existing package\n'
    assert not (tmp_path / '.runner.lock').exists()
