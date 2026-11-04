"""Offline provenance labels for authenticated release-cache hits."""
from copy import deepcopy

import pytest

from .test_tools import tools, propose, catalogue_row


@pytest.mark.parametrize('passed', [True, False])
def test_tagged_cached_release_is_stored_without_changing_acceptance(monkeypatch, passed):
    report = dict(passed=True, count={'weighted_nonproductive':70.})
    t = tools(checker=lambda *_:report, release_gate=lambda *_: gate)
    rid = propose(t)
    t.confirm_reading(rid)
    row = catalogue_row(t, t._rules(), frames_passed=passed)
    t.plan = row['plan']
    gate = row['gate']
    gate.update(reused_recorded=True, freshly_rechecked=False,
                gate_source='authenticated_release_cache', current_identity_verified=True,
                checked_utc='2026-11-02T05:33:34.342527+00:00')
    monkeypatch.setattr(t, '_audit', lambda *_: [])
    result = t.call('check_plan', {})
    assert result['passed'] is passed
    assert ('plan_sha256' in result) is passed
    assert result['mode'] == 'stored' and result['reused_recorded']
    assert result['freshly_rechecked'] is False
    assert result['checked_utc'] == gate['checked_utc']
    assert result['current_identity_verified'] is True
    assert 'Reused recorded native gate' in result['text']
    assert 'No new native numerical check' in result['text']


def test_cached_gate_tag_does_not_mutate_original_native_evidence():
    from .hero_run import tag_recorded_release
    original = {'passed':True,'checked_utc':'recorded-date','gauges':{'all_frames':True}}
    before = deepcopy(original)
    result = tag_recorded_release(original)
    assert original == before
    assert result['reused_recorded'] and result['mode'] == 'stored'
    assert not result['freshly_rechecked'] and result['current_identity_verified']
    result['gauges']['all_frames'] = False
    assert original == before
