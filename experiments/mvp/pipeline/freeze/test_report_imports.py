"""Report roots are skipped before import source or symlink traversal."""
from __future__ import annotations

from pathlib import Path

import pytest

from experiments.mvp.pipeline.freeze import imports, product_outputs


def put(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.mark.parametrize('report_root', product_outputs.EXCLUDED_ROOTS)
def test_default_report_exclusion_skips_report_source_and_symlink_but_keeps_runtime(tmp_path, report_root):
    root = tmp_path / 'repo'
    put(root, 'app/v6.py', 'from outside import runtime\n'
        f'importlib.import_module({(report_root + "/scanner").replace("/", ".")!r})\n')
    put(root, 'outside/runtime.py', "raise RuntimeError('original body must never execute')\n")
    outside = put(tmp_path, 'outside-report.py', 'this is deliberately invalid Python source !!!')
    report = root / report_root / 'scanner.py'
    report.parent.mkdir(parents=True)
    report.symlink_to(outside)
    graph = imports.discover(root, ('app.v6',))
    assert 'outside/runtime.py' in graph['paths']
    assert not any(name == report_root or name.startswith(report_root + '/') for name in graph['paths'])
    assert graph['excluded_roots'] == sorted(product_outputs.EXCLUDED_ROOTS)


def test_default_report_exclusion_does_not_parse_regular_report_source(tmp_path):
    report_root = product_outputs.EXCLUDED_ROOTS[0]
    put(tmp_path, 'app/v6.py', 'import ' + (report_root + '/scanner').replace('/', '.') + '\n')
    put(tmp_path, report_root + '/scanner.py', 'invalid AST if visited !!!')
    assert imports.discover(tmp_path, ('app.v6',))['paths'] == ['app/v6.py']


def test_explicit_report_exclusion_filters_entrypoint_before_traversal(tmp_path):
    outside = put(tmp_path, 'outside.py', 'invalid AST !!!')
    alias = tmp_path / 'analysis/scanner.py'
    alias.parent.mkdir()
    alias.symlink_to(outside)
    graph = imports.discover(tmp_path, ('analysis.scanner',), excluded_roots=('analysis',))
    assert graph['paths'] == []
    assert graph['missing_entrypoints'] == ['analysis.scanner']


def test_report_exclusion_keeps_other_runtime_symlink_rejection(tmp_path):
    root = tmp_path / 'repo'
    put(root, 'app/v6.py', 'import outside.runtime\n')
    target = put(tmp_path, 'runtime-outside.py', '# must not follow this target\n')
    alias = root / 'outside/runtime.py'
    alias.parent.mkdir()
    alias.symlink_to(target)
    with pytest.raises(ValueError, match='symlink excluded'):
        imports.discover(root, ('app.v6',))


def test_explicit_empty_exclusions_restore_strict_report_symlink_rejection(tmp_path):
    report_root = product_outputs.EXCLUDED_ROOTS[0]
    put(tmp_path, 'app/v6.py', 'import ' + (report_root + '/scanner').replace('/', '.') + '\n')
    target = put(tmp_path, 'target.py', '# synthetic\n')
    alias = tmp_path / report_root / 'scanner.py'
    alias.parent.mkdir(parents=True)
    alias.symlink_to(target)
    with pytest.raises(ValueError, match='symlink excluded'):
        imports.discover(tmp_path, ('app.v6',), excluded_roots=())


@pytest.mark.parametrize('name', ['../outside', '/absolute', ''])
def test_invalid_import_exclusion_root_fails_closed(tmp_path, name):
    with pytest.raises(ValueError, match='invalid import exclusion root'):
        imports.discover(tmp_path, (), excluded_roots=(name,))
