"""D128 gate/attribution fixtures against a staged overlay; no live source edits."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
PATCHES = ROOT/'experiments/mvp/exit/patches'


def load(name, directory):
    spec = importlib.util.spec_from_file_location(f'_d128_{name}', directory/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def overlay(monkeypatch):
    modules = {}
    live = ROOT/'src/llm/exit'
    active = (live/'finisher_outcomes.py').exists()
    for name in ('outcomes', 'metrics', 'audit', 'finisher_outcomes'):
        directory = live if active else PATCHES/('d124' if name in ('outcomes', 'metrics') else 'd129')
        module = load(name, directory)
        modules[name] = module
        monkeypatch.setitem(sys.modules, f'llm.exit.{name}', module)
    return modules


def rows(passes, cost=10, n=30):
    return [dict(instance_id=str(i), passed=i<passes,
                 weighted_moves=cost if i<passes else None) for i in range(n)]


@pytest.mark.parametrize('passes,cost,accepted', [
    (22,10,True), (22,10.5,True), (22,10.5001,False),
    (21,1,False), (20,9.5,True), (20,9.51,False), (19,1,False)])
def test_d128_preserves_primary_rule_and_caps_paired_regression(overlay, passes, cost, accepted):
    result = overlay['audit'].audit_decision(rows(20), rows(passes, cost))
    assert result['accepted'] is accepted
    assert result['pass_count_denominator'] == 30
    assert result['maximum_relative_move_regression'] == .05
    assert result['paired_baseline_mean_moves'] == 10
    assert result['paired_child_mean_moves'] == cost


def test_gain_in_passes_cannot_hide_worse_mutual_moves(overlay):
    result = overlay['audit'].audit_decision(rows(20), rows(22,20))
    assert result['primary_rule_passed'] is True
    assert result['mutual_moves_within_limit'] is False
    assert result['reason'] == 'mutual_moves_regressed'
    assert result['accepted'] is False


@pytest.mark.parametrize('paired_cost,other_baseline_cost,child_cost,accepted', [
    (50,10.125,52,True), (10,70.125,11,False)])
def test_gate_uses_paired_baseline_not_global_34_05(overlay, paired_cost, other_baseline_cost, child_cost, accepted):
    baseline = rows(20)
    for i, row in enumerate(baseline[:20]):
        row['weighted_moves'] = paired_cost if i<12 else other_baseline_cost
    assert sum(r['weighted_moves'] for r in baseline if r['passed'])/20 == pytest.approx(34.05)
    child = [dict(instance_id=str(i), passed=i<12 or i>=20,
                  weighted_moves=child_cost if i<12 or i>=20 else None) for i in range(30)]
    # Same 30 maps: child drops eight baseline passes and gains ten new ones.
    result = overlay['audit'].audit_decision(baseline, child)
    assert result['pass_gain'] == 2
    assert result['paired_baseline_mean_moves'] == paired_cost
    assert result['paired_child_mean_moves'] == child_cost
    assert result['accepted'] is accepted


def test_no_mutual_passes_cannot_authorize_worse_unknown_moves(overlay):
    baseline = rows(0)
    result = overlay['audit'].audit_decision(baseline, rows(2))
    assert result['primary_rule_passed'] is True
    assert result['accepted'] is False
    assert result['reason'] == 'no_mutual_passes'


def test_unavailable_remains_distinct_and_blocks_audit_gain(overlay):
    child = rows(22)
    child[-1].update(unavailable=True)
    result = overlay['audit'].audit_decision(rows(20), child)
    assert result['child_pass_count'] == 22
    assert result['accepted'] is False
    assert result['reason'] == 'verification_unavailable'
    assert result['child_outcomes']['unavailable'] == 1
    assert result['child_outcomes']['physics_failed'] == 0


@pytest.mark.parametrize('cause', ['timeout:search', 'timeout:worker', 'timeout:no_budget', 'timeout:acceptance'])
def test_structured_finisher_timeouts_are_separate_from_dead_ends(overlay, cause):
    row = dict(passed=False, status='dead_end',
               verification={'finisher':{'ballast_status':'not_found','not_found_cause':cause}})
    counts = overlay['finisher_outcomes'].finisher_outcome_counts([row])
    assert counts['finisher_timeouts'] == 1
    assert counts['planner_dead_ends'] == counts['checker_unavailable'] == 0


def test_finisher_counts_do_not_guess_timeout_from_unknown_search_or_duration(overlay):
    rows = [dict(passed=False, verification={'finisher':{'not_found_cause':'unknown_witness'}}),
            dict(passed=False, status='dead_end'),
            dict(passed=False, verification={'checker_available':False}),
            dict(passed=True), dict(passed=False, timings={'ballast_s':300}, status='timeout')]
    assert overlay['finisher_outcomes'].finisher_outcome_counts(rows) == dict(total=5,
        passed=1, finisher_timeouts=0, planner_dead_ends=1, checker_unavailable=1, other_not_passed=2)


def test_stowlm_labels_attribute_ballast_and_keep_hybrid_separate(overlay):
    label = overlay['finisher_outcomes'].stowlm_row_labels(checker_passed=True,
        backtracking=True, mask_override_count=12, mask_decision_count=100)
    assert label['label'] == ('cargo sequence decoded by StowLM under the feasibility mask (+ bounded rewinds); '
                              'ballast by the deterministic V4 finisher; full checker passed')
    assert label['row_kind'] == 'model_alone'
    assert label['mask_override_rate'] == .12
    hybrid = overlay['finisher_outcomes'].stowlm_row_labels(checker_passed=True, polished=True)
    assert hybrid['row_kind'] == 'model_plus_polish'
    assert hybrid['hybrid_label'] == 'model + 15 s LNS polish'
    assert hybrid['mask_override_rate'] is None
    assert 'full checker passed' not in overlay['finisher_outcomes'].stowlm_row_labels(
        checker_passed=False)['label']


def test_mask_override_rate_refuses_partial_or_invalid_measurements(overlay):
    labels = overlay['finisher_outcomes'].stowlm_row_labels
    for kw in ({'mask_override_count':1}, {'mask_override_count':2,'mask_decision_count':1}):
        with pytest.raises(ValueError):
            labels(checker_passed=True, **kw)


def test_d128_registration_preserves_original_and_pins_extension(overlay, monkeypatch, tmp_path):
    audit = overlay['audit']
    corpus = tmp_path/'corpus'
    paths = [corpus/'val'/'small'/f'{i}.json' for i in range(30)]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}')
    listing = tmp_path/'audit.txt'
    listing.write_text(''.join(str(p)+'\n' for p in paths))
    original = dict(rule=audit.ORIGINAL_RULE,
        list_sha256=hashlib.sha256(listing.read_bytes()).hexdigest(),
        maps=[dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths])
    target = tmp_path/'audit-rule.json'
    original_bytes = json.dumps(original).encode()
    target.write_bytes(original_bytes)
    import llm.exit.identity
    import stowage.io
    monkeypatch.setattr(llm.exit.identity, 'identity_guard', lambda *a:SimpleNamespace(require_allowed=lambda *a,**kw:None))
    monkeypatch.setattr(stowage.io, 'load_instance', lambda path:object())
    monkeypatch.setitem(sys.modules, 'llm.exit.runner', SimpleNamespace(
        write_json=lambda path,row:Path(path).write_text(json.dumps(row))))
    assert audit.frozen_audit_paths(tmp_path,corpus,listing,tmp_path) == paths
    assert target.read_bytes() == original_bytes
    extension = tmp_path/'audit-rule-D128-extension.json'
    record = json.loads(extension.read_text())
    assert record['original_registration_sha256'] == hashlib.sha256(original_bytes).hexdigest()
    assert record['extended_rule']['maximum_relative_move_regression'] == .05
    assert record['maps'] == original['maps']
    audit.frozen_audit_paths(tmp_path,corpus,listing,tmp_path)  # Idempotent resume.
    record['extended_rule']['maximum_relative_move_regression'] = .5
    extension.write_text(json.dumps(record))
    with pytest.raises(RuntimeError, match='D128 audit extension'):
        audit.frozen_audit_paths(tmp_path,corpus,listing,tmp_path)
