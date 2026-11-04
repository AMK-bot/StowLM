"""Current-version reporting refuses mixed, missing or relabelled historical evidence."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('v44_report', Path(__file__).with_name('summarize_twostep_v44.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def record(i=0, version='4.4.0', source='a'*64, passed=True):
    return dict(instance_id=f'case-{i}', input_sha256=str(i), completed=True,
                source_before={'src/stowage/solver/twostep.py': source},
                result={'planner_version': version, 'status': 'ok', 'repaired': False},
                shippable=passed, report_binding_valid=True,
                report={'passed': passed, 'count': {'weighted_nonproductive': 5}, 'violations': []})


def test_current_requires_all_frozen_cases_and_completed_attempts():
    assert r.validate_current([record()], ['0']) == 'a'*64
    with pytest.raises(ValueError, match='every frozen input'):
        r.validate_current([record()], ['0', '1'])
    value = record()
    value['completed'] = False
    with pytest.raises(ValueError, match='Incomplete'):
        r.validate_current([value], ['0'])


def test_version_cannot_be_relabelled_and_sources_cannot_mix():
    with pytest.raises(ValueError, match='version'):
        r.validate_current([record(version='4.3.0')], ['0'])
    with pytest.raises(ValueError, match='Mixed'):
        r.validate_current([record(), record(1, source='b'*64)], ['0', '1'])
    with pytest.raises(ValueError, match='source hash'):
        r.validate_current([record(source='')], ['0'])


def test_comparison_includes_failed_move_diagnostics_and_status():
    old = record(version='4.3.0', passed=False)
    new = record(passed=False)
    value = r.changes([old], [new])
    assert value['changed_n'] == 0  # Version alone is not a numerical/verdict change.
    new['report']['count']['weighted_nonproductive'] = 7
    assert r.changes([old], [new])['rows'][0]['changed_fields'] == ['counts']
    new['result']['status'] = 'timeout'
    assert 'planner_status' in r.changes([old], [new])['rows'][0]['changed_fields']
    with pytest.raises(ValueError, match='identical frozen'):
        r.changes([old], [record(1)])


def test_render_replaces_only_current_twostep_and_preserves_solver_numbers(monkeypatch):
    monkeypatch.setattr(r.h, 'markdown', lambda s: '# Tiny\n## Every plan attempt\ntwostep | 5\n[test](test-bounds/summary.json)')
    monkeypatch.setattr(r.small, 'markdown', lambda s: 'Small 4.3.0\ntwostep | 6\n[test](test-twostep-small-v21/summary.json)')
    archive = b'old twostep | 999\n' + r.v21.MARKER + b'\nsolver | 12 | 23.182\nTwo-step awaits leader go; exact remains tiny only.\n' + r.v21.END_MARKER + b'old twostep | 888'
    tiny = dict(twostep_source_sha256='a'*64)
    small = dict(twostep_source_sha256='a'*64)
    text = r.render(tiny, small, archive, 'archive/report.md')
    assert 'solver | 12 | 23.182' in text
    assert 'old twostep' not in text and '999' not in text and '888' not in text
    assert 'Small 4.4.0' in text and 'awaits leader go' not in text
    assert 'test-twostep-small-v21-v44/summary.json' in text
    assert 'test-twostep-tiny-v44/summary.json' in text
    assert '[Archived pre-4.4 report](archive/report.md)' in text
    with pytest.raises(ValueError, match='unique'):
        r.render(tiny, small, archive + r.v21.MARKER, 'archive.md')
