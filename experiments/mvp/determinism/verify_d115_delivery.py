"""Independent D115 artifact audit; no project imports or case execution."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def junit(path, tests, failures):
    suites = ET.parse(path).getroot().findall('testsuite')
    counts = {key: sum(int(s.attrib[key]) for s in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    assert counts == dict(tests=tests, failures=failures, errors=0, skipped=0), counts
    return counts


def main():
    archive = read(HERE / 'D115_APPLY_ARCHIVE.json')
    for name, expected in archive['after'].items():
        assert digest(REPO / name) == expected
        ast.parse((REPO / name).read_text())
    assert (REPO / 'experiments/mvp/lookahead/archive_assets.py').read_bytes() == (
        HERE / 'patches/archive_asset_closure.py').read_bytes()

    patch = HERE / 'patches/securing-fail-closed.patch'
    ledger = read(HERE / 'D115_COMPUTE.json')
    assert ledger['closed_utc'] and all(j['reaped'] for j in ledger['jobs'])
    assert ledger['cores'] == ledger['native_threads'] == 1 and not ledger['gpu']
    output = Path(ledger['output'])
    assert output.is_relative_to(HERE)
    counts_before = junit(output / 'before/junit.xml', 15, 13)
    counts_after = junit(output / 'after/junit.xml', 15, 0)
    before_sources = {str(p.relative_to(output / 'before/src')): p.read_bytes()
                      for p in (output / 'before/src').rglob('*.py')}
    after_sources = {str(p.relative_to(output / 'after/src')): p.read_bytes()
                     for p in (output / 'after/src').rglob('*.py')}
    changed = {name for name in before_sources.keys() | after_sources.keys()
               if before_sources.get(name) != after_sources.get(name)}
    expected_changed = {line.removeprefix('+++ b/src/')
                        for line in patch.read_text().splitlines()
                        if line.startswith('+++ b/src/')}
    assert changed == expected_changed, changed ^ expected_changed
    comparison = read(output / 'physical-comparison.json')
    assert comparison['patch_sha256'] == digest(patch)
    assert comparison['all_workers_reaped'] and all(comparison['equal_fields'].values())
    physical = []
    for mode in ('before', 'after'):
        raw = read(output / mode / 'physical-smoke.json')
        assert Path(raw['origin']).is_relative_to(output / mode / 'src')
        assert raw['bounds_available'] is True and raw['metrics']['evaluations'] == 6
        assert raw['native_legal'] == ["Place(box='QZXU0000117', slot='0020784')"]
        assert raw['legal'] == raw['token_root'] == []
        assert not any(v for k, v in raw['metrics'].items()
                       if k.startswith(('unavailable:', 'fail_open:')))
        physical.append(raw)
    for key in comparison['equal_fields']:
        assert physical[0][key] == physical[1][key], key

    for path in (HERE / 'patches/fail_closed_candidate').rglob('*.py'):
        relative = path.relative_to(HERE / 'patches/fail_closed_candidate')
        assert path.read_bytes() == (output / 'after' / relative).read_bytes()
        ast.parse(path.read_text())
    assert (output / 'after/test_fail_closed.py').read_bytes() == (
        HERE / 'patches/fail_closed_candidate/tests/llm/securing/test_fail_closed.py').read_bytes()

    existing = read(HERE / 'D115_EXISTING_TESTS.json')
    assert existing['closed_utc'] and existing['cores'] == existing['native_threads'] == 1
    assert not existing['gpu'] and all(j['reaped'] and j['exit_code'] == 0 for j in existing['jobs'])
    assert Path(existing['runtime']) == output / 'after'
    counts_existing = junit(Path(existing['output']) / 'junit.xml', 17, 0)
    assert digest(output / 'after/src/llm/securing/mask_filter.py') == existing['source_sha256']

    checks = [
        ['--reverse', '--check', 'experiments/mvp/determinism/patches/archive_dependency_closure.patch'],
        ['--check', 'experiments/mvp/determinism/patches/securing-fail-closed.patch'],
        ['--check', 'experiments/mvp/fixwindow/heat-ids.patch'],
        ['--check', 'experiments/mvp/fixwindow/manual-c2.patch'],
        ['--check', 'experiments/mvp/fixwindow/heat-ids.patch', 'experiments/mvp/fixwindow/manual-c2.patch'],
    ]
    for options in checks:
        subprocess.run(['git', 'apply', *options], cwd=REPO, check=True, capture_output=True, text=True)

    runbook = REPO / 'experiments/mvp/fixwindow/RUNBOOK.md'
    prepared = read(REPO / 'experiments/mvp/fixwindow/runbook-static-verification.json')
    assert prepared['runbook_sha256'] == digest(runbook)
    text = runbook.read_text()
    shell = re.findall(r'```sh\n(.*?)\n```', text, re.S)
    assert len(shell) == 7
    for block in shell:
        subprocess.run(['/bin/bash', '-n'], input=block, check=True, capture_output=True, text=True)
    snippets = re.findall(r"<<'PY'\n(.*?)\nPY", text, re.S)
    assert len(snippets) == 3
    for code in snippets:
        ast.parse(code)
    print(json.dumps(dict(verified=True, archive_patch_applied=True,
        production_patch_applied=False, production_patch_sha256=digest(patch),
        before=counts_before, after=counts_after, existing_tests=counts_existing,
        train_physical_force_evaluations_each=6, train_actions_and_tokens_unchanged=True,
        isolated_source_changes_exactly_match_patch=True,
        fixwindow_patches_applied=False, patch_checks=len(checks),
        runbook_shell_blocks=len(shell), runbook_python_snippets=len(snippets),
        all_recorded_workers_reaped=True), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
