"""Expander policy is fixed; unrelated caller contexts keep their own lifetime."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.frozen import FROZEN, active_stubs, stub_override
from stowage.rules.catalogue import catalogue_snapshot, rule_freeze_v1, rule_freeze_v1_enabled


def _case():
    instance = tiny_instance(1, n_load=1, n_other=0, p40=0)
    return instance, Place(instance.load_list[0], '0010182')


def test_expander_does_not_retain_expired_move_stub():
    instance, action = _case()
    calls = []

    def temporary(*args, **kwargs):
        calls.append(True)
        return FROZEN['moves.apply_move'].func(*args, **kwargs)

    with rule_freeze_v1():
        with stub_override('moves.apply_move', temporary, reason='temporary regression audit'):
            ex = Expander(instance)
        assert not active_stubs()
        ex.step(action)
    assert calls == []


def test_expander_preserves_current_callers_move_stub():
    instance, action = _case()
    calls = []

    def current(*args, **kwargs):
        calls.append(True)
        return FROZEN['moves.apply_move'].func(*args, **kwargs)

    with rule_freeze_v1():
        ex = Expander(instance)
        with stub_override('moves.apply_move', current, reason='current regression audit'):
            ex.step(action)
    assert calls == [True]


@pytest.mark.parametrize('enabled', [False, True])
def test_expander_context_preserves_complete_snapshot_and_caller_policy(enabled):
    with rule_freeze_v1(enabled=enabled):
        instance, _ = _case()
        expected = catalogue_snapshot().content_sha256
        ex = Expander(instance)
        assert catalogue_snapshot().content_sha256 == expected
    with rule_freeze_v1(enabled=not enabled):
        current = catalogue_snapshot().content_sha256
        with ThreadPoolExecutor(max_workers=1) as worker:
            def probe():
                return ex._catalogue_context.run(lambda: (
                    rule_freeze_v1_enabled(), catalogue_snapshot().content_sha256))
            assert worker.submit(probe).result() == (enabled, expected)
        assert catalogue_snapshot().content_sha256 == current
