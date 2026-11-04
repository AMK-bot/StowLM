"""Scope audit for observational timing and disposable candidate workspaces."""
import pytest


def test_shared_workspace_never_reuses_a_candidate_or_skips_a_part():
    from stowage.checker.perf import current_cache
    from stowage.model import PartReport
    from .fast_perf import public_parts

    class Input:
        call_index = 2

    calls, workspaces = [], []

    def part(name):
        def run(instance, ship, *, call_index):
            calls.append((name, instance, ship, call_index))
            workspaces.append(current_cache())
            return PartReport(name=name, metrics={'input_nonce': ship})
        return run

    functions = [(name, part(name)) for name in ('rules', 'lashing', 'visibility')]
    left, right = Input(), Input()
    first, _ = public_parts(left, 1, shared=True, functions=functions)
    second, _ = public_parts(right, 2, shared=True, functions=functions)
    assert [row[0] for row in calls] == ['rules', 'lashing', 'visibility'] * 2
    assert all(row[1:] == (left, 1, 2) for row in calls[:3])
    assert all(row[1:] == (right, 2, 2) for row in calls[3:])
    assert all(cache is workspaces[0] for cache in workspaces[:3])
    assert all(cache is workspaces[3] for cache in workspaces[3:])
    assert workspaces[0] is not workspaces[3]
    assert first['lashing'].metrics['input_nonce'] == 1
    assert second['lashing'].metrics['input_nonce'] == 2
    assert current_cache() is None


def test_observational_wrappers_restore_on_exception_and_leave_canonical_providers():
    import stowage.checker
    from . import moves, physics, securing, visibility
    from .fast_perf import observe_calls
    canonical = (stowage.checker.check_static_rules, stowage.checker.check_lashing,
                 stowage.checker.check_visibility)
    originals = (moves.prepare_move, moves._counts, physics.fast_check,
                 physics.check_static_rules, securing.screen_securing,
                 securing.check_lashing, visibility.screen_visibility,
                 visibility.check_visibility)
    with pytest.raises(RuntimeError, match='observation failed'):
        with observe_calls():
            assert canonical == (stowage.checker.check_static_rules, stowage.checker.check_lashing,
                                 stowage.checker.check_visibility)
            raise RuntimeError('observation failed')
    assert originals == (moves.prepare_move, moves._counts, physics.fast_check,
                         physics.check_static_rules, securing.screen_securing,
                         securing.check_lashing, visibility.screen_visibility,
                         visibility.check_visibility)
    assert canonical == (stowage.checker.check_static_rules, stowage.checker.check_lashing,
                         stowage.checker.check_visibility)
