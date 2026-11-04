"""Admission/fallback controls; no real maps, optimizers or physics jobs."""
from pathlib import Path
from types import SimpleNamespace as NS
from time import monotonic
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cover_ballast as cb


class Plan:
    def __init__(self, name):
        self.name = name
    def model_copy(self):
        return self
    def model_dump_json(self):
        return self.name


def report(passed=False, rules=('iscode.loading.trim',), lifts=70.):
    return NS(passed=passed, violations=[NS(rule_id=r, severity='hard') for r in rules],
              count=NS(weighted_nonproductive=lifts))


def setup(monkeypatch, final):
    incumbent, candidate, repaired = Plan('incumbent'), Plan('candidate'), Plan('repaired')
    def check(instance, plan):
        return report(True, (), 80.) if plan is incumbent else final if plan is repaired else report()
    monkeypatch.setattr(cb, '_check', check)
    monkeypatch.setattr(cb, '_complete', lambda *_: True)
    monkeypatch.setattr(cb, '_optimize', lambda *a, **kw: repaired)
    return NS(id='synthetic', load_list=[]), incumbent, candidate, repaired


def test_rule_gate_excludes_unknown_and_umbrella_ids():
    assert cb.trim_draught_only(report(rules=('iscode.loading.draft', 'port.call.draft')))
    assert not cb.trim_draught_only(report(rules=()))
    assert not cb.trim_draught_only(report(rules=('port.stability.cargo_operations',)))
    assert not cb.trim_draught_only(report(rules=('iscode.loading.trim', 'unknown.trim')))


def test_full_pass_and_strict_gain_required(monkeypatch):
    instance, incumbent, candidate, repaired = setup(monkeypatch, report(True, (), 70.))
    result, meta = cb.repair(instance, candidate, monotonic()+30., incumbent=incumbent,
                            out=HERE/'cover-ballast/test-pass')
    assert result is repaired and meta['accepted']
    assert meta['candidate_lifts'] == 70.


def test_new_securing_violation_retains_exact_incumbent(monkeypatch):
    instance, incumbent, candidate, repaired = setup(monkeypatch,
        report(False, ('class.securing',), 70.))
    result, meta = cb.repair(instance, candidate, monotonic()+30., incumbent=incumbent,
                            out=HERE/'cover-ballast/test-rejected')
    assert result is incumbent and not meta['accepted']


def test_no_lift_gain_retains_exact_incumbent(monkeypatch):
    instance, incumbent, candidate, repaired = setup(monkeypatch, report(True, (), 80.))
    result, meta = cb.repair(instance, candidate, monotonic()+30., incumbent=incumbent,
                            out=HERE/'cover-ballast/test-tie')
    assert result is incumbent and not meta['accepted']


def test_unrelated_initial_failure_never_calls_optimizer(monkeypatch):
    instance, incumbent, candidate, repaired = setup(monkeypatch, report(True, (), 70.))
    monkeypatch.setattr(cb, '_check', lambda i, p: report(True, (), 80.) if p is incumbent else
                        report(False, ('solas.v22.visibility',), 70.))
    monkeypatch.setattr(cb, '_optimize', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('called')))
    result, meta = cb.repair(instance, candidate, monotonic()+30., incumbent=incumbent)
    assert result is incumbent and meta['status'] == 'not_only_trim_draught'


def test_optimizer_exception_preserves_incumbent_and_cache(monkeypatch):
    instance, incumbent, candidate, repaired = setup(monkeypatch, report(True, (), 70.))
    monkeypatch.setenv('STOWAGE_VESSEL_CACHE_DIR', 'previous-cache')
    def fail(*a, **kw):
        raise ValueError('synthetic solver failure')
    monkeypatch.setattr(cb, '_optimize', fail)
    result, meta = cb.repair(instance, candidate, monotonic()+30., incumbent=incumbent,
                            out=HERE/'cover-ballast/test-exception')
    assert result is incumbent and meta['status'] == 'incumbent_retained_exception'
    assert cb.os.environ['STOWAGE_VESSEL_CACHE_DIR'] == 'previous-cache'


def test_deadline_and_output_fence(monkeypatch):
    instance, incumbent, candidate, repaired = setup(monkeypatch, report(True, (), 70.))
    result, meta = cb.repair(instance, candidate, monotonic()+1., incumbent=incumbent)
    assert result is incumbent and meta['status'] == 'insufficient_ballast_budget'
    result, meta = cb.repair(instance, candidate, monotonic()+30., incumbent=incumbent, out='/tmp/outside')
    assert result is incumbent and 'output must stay' in meta['error']


def test_partial_candidate_cannot_be_accepted(monkeypatch):
    instance, incumbent, candidate, repaired = setup(monkeypatch, report(True, (), 70.))
    monkeypatch.setattr(cb, '_complete', lambda i, p: p is incumbent)
    result, meta = cb.repair(instance, candidate, monotonic()+30., incumbent=incumbent)
    assert result is incumbent and meta['status'] == 'incomplete_or_illegal_candidate'
