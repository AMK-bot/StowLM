import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from experiments.mvp.pipeline.copilot import spending


def policy(monkeypatch, tmp_path, **changes):
    value = dict(state='hero_authorized', cap_usd='12',
                 authorization_source='Fixture leader announcement',
                 hero_instance_sha256='a'*64, lattice_catalogue_sha256='b'*64)
    value.update(changes)
    path = tmp_path/'policy.json'
    path.write_text(json.dumps(value))
    monkeypatch.setattr(spending, 'POLICY', path)
    return path


def test_user_pause_stops_even_a_hero_request(monkeypatch, tmp_path):
    policy(monkeypatch, tmp_path, state='awaiting_leader_hero')
    with pytest.raises(spending.SpendingPaused):
        spending.assert_paid_allowed('pipeline-t1.6-copilot/hero-final/negotiation')


@pytest.mark.parametrize('changes', [dict(state='awaiting_leader_hero'),
    dict(cap_usd='8'), dict(cap_usd='invalid'), dict(authorization_source=''),
    dict(hero_instance_sha256=None), dict(lattice_catalogue_sha256='unverified')])
def test_hero_identity_and_explicit_host_release_are_required(monkeypatch, tmp_path, changes):
    policy(monkeypatch, tmp_path, **changes)
    with pytest.raises(spending.SpendingPaused):
        spending.assert_paid_allowed('pipeline-t1.6-copilot/hero-final/negotiation')


def test_after_release_cap_is_reserved_for_hero_and_shot10(monkeypatch, tmp_path):
    policy(monkeypatch, tmp_path)
    spending.assert_paid_allowed('pipeline-t1.6-copilot/hero-final/negotiation')
    spending.assert_paid_allowed('pipeline-t1.6-copilot/shot10/take')
    with pytest.raises(spending.SpendingPaused):
        spending.assert_paid_allowed('pipeline-t1.6-copilot/agency-v6-frozen-first/retry')


def test_verified_attempt_cannot_reauthorize_the_obsolete_launcher(monkeypatch, tmp_path):
    policy(monkeypatch, tmp_path, state='verified_hero_authorized')
    spending.assert_paid_allowed('pipeline-t1.6-copilot/hero-final/verified/first-provider')
    spending.assert_paid_allowed('pipeline-t1.6-copilot/shot10/verified/retake')
    with pytest.raises(spending.SpendingPaused):
        spending.assert_paid_allowed('pipeline-t1.6-copilot/hero-final/old-loaded-agent')


@pytest.mark.parametrize('contents', ['{', '[]', '{}'])
def test_corrupt_or_missing_policy_fails_closed(monkeypatch, tmp_path, contents):
    path = policy(monkeypatch, tmp_path)
    path.write_text(contents)
    with pytest.raises(spending.SpendingPaused):
        spending.assert_paid_allowed('pipeline-t1.6-copilot/hero-final/negotiation')
    path.unlink()
    with pytest.raises(spending.SpendingPaused):
        spending.assert_paid_allowed('pipeline-t1.6-copilot/hero-final/negotiation')


@pytest.mark.parametrize('wrapper', ['legacy', 'agency', 'reader'])
def test_every_paid_wrapper_stops_before_provider_or_ledger(monkeypatch, tmp_path, wrapper):
    from experiments.mvp.pipeline.copilot.live import LiveClient
    from experiments.mvp.pipeline.copilot.agency_evaluate import Model
    from experiments.mvp.pipeline.copilot.real_demo import _Session
    policy(monkeypatch, tmp_path, state='awaiting_leader_hero')
    provider = Mock(side_effect=AssertionError('Provider must not run while paused'))
    ledger = Mock(side_effect=AssertionError('Ledger must not open while paused'))
    client = SimpleNamespace(complete=provider, ledger=ledger)
    with pytest.raises(spending.SpendingPaused):
        if wrapper == 'legacy':
            LiveClient(client, 'hero-final/test').complete('prompt')
        elif wrapper == 'agency':
            Model(client, 'pipeline-t1.6-copilot/hero-final/test', 0).complete('prompt')
        else:
            _Session(client).complete('prompt', purpose='pipeline-t1.6-copilot/shot10/test',
                                      max_output_tokens=16, reasoning_effort='low')
    provider.assert_not_called()
    assert not ledger.mock_calls
