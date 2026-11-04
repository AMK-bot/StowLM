"""Small reporting preserves accepted tiny bytes and complete failed populations."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('small_reporter', Path(__file__).with_name('summarize_small_v2.py'))
reporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reporter)


def record(identity, sha, passed):
    return dict(instance_id=identity, input_sha256=sha, rung='small', result={'repaired': False, 'status': 'ok'},
                report={'passed': passed, 'violations': [] if passed else [{'rule_id': 'test.failure', 'severity': 'hard'}],
                        'parts': {}, 'count': {'weighted_nonproductive': 2}},
                shippable=passed, report_binding_valid=True)


def save(folder, row):
    path = folder / 'instances' / row['instance_id'] / 'record.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row))


def test_append_and_rerender_preserve_exact_tiny_prefix(tmp_path):
    report = tmp_path / 'test-references.md'
    tiny = b'# Frozen tiny\r\n\r\n| measured | 1.234 |\r\n'
    report.write_bytes(tiny)
    backup = tmp_path / 'backup'
    first = reporter.append_section(report, 'small first\n', backup)
    second = reporter.append_section(report, 'small second\n', backup)
    assert report.read_bytes() == tiny + reporter.MARKER + b'\n\nsmall second\n' + reporter.END_MARKER + b'\n'
    assert (backup / 'tiny-report-prefix.md').read_bytes() == tiny
    assert first == second
    report.write_bytes(b'changed tiny' + report.read_bytes())
    with pytest.raises(ValueError, match='differs'):
        reporter.append_section(report, 'third', backup)


def test_stale_journal_failure_and_unattempted_stay_visible(tmp_path):
    expected = [dict(path=f'test/small/ship/{i}.json', sha256=str(i), ship='ship') for i in range(3)]
    folder = tmp_path / 'test-solver-small-v2'
    passed, failed = record('pass', '0', True), record('fail', '1', False)
    save(folder, passed)
    save(folder, failed)
    (folder / 'records.jsonl').write_text(json.dumps(passed) + '\n')
    summary = reporter.summarize(tmp_path, expected)
    solver = summary['methods']['solver']
    assert solver['n_instances'] == 2
    assert solver['n_passed'] == 1
    assert solver['own_pass_rate'] == .5
    assert solver['expected_instances'] == 3
    assert solver['missing_paths'] == ['test/small/ship/2.json']
    assert summary['methods']['twostep']['status'] == 'waiting for leader go'
    assert summary['methods']['twostep']['n_instances'] == 0
    text = reporter.markdown(summary)
    assert 'test.failure' in text and 'unattempted' in text
    assert '1/3' in text and '1/2' in text
    assert text.count('waiting for leader go') >= 3
    assert len((folder / 'records.jsonl').read_text().splitlines()) == 2


def test_release_amendment_requires_explicit_count(tmp_path):
    release = tmp_path / 'release.json'
    release.write_text(json.dumps({'hashes': {'test/small/ship/a.json': 'x', 'test/small/ship/b.json': 'y'}}))
    with pytest.raises(ValueError, match='explicit count'):
        reporter.released_inputs(release, tmp_path / 'manifest.json', 1)


def test_missing_pairs_do_not_silently_intersect(tmp_path):
    expected = [dict(path=f'test/small/ship/{i}.json', sha256=str(i)) for i in range(2)]
    save(tmp_path / 'test-solver-small-v2', record('case-0', '0', True))
    save(tmp_path / 'test-twostep-small-v2', record('case-0', '0', True))
    summary = reporter.summarize(tmp_path, expected, twostep_released=True)
    assert 'Full matching' in summary['paired_comparison']['unavailable']
