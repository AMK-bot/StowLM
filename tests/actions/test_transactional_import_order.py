"""Fresh imports must never mistake an installed move hook for native Replay."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize('consumer', ['expander', 'feasibility'])
@pytest.mark.parametrize('hook', ['forward', 'reject'])
@pytest.mark.parametrize('binding', ['public', 'public_and_owner'])
@pytest.mark.parametrize('metadata', ['plain', 'wrap_raw'])
def test_expander_replay_preserves_hooks_installed_before_import(consumer, hook, binding, metadata):
    script = textwrap.dedent('''
        import sys
        import numpy as np
        import stowage.moves as public
        import stowage.moves.engine as owner
        from stowage.model import Violation
        assert 'stowage.actions.expander' not in sys.modules
        assert 'stowage.feasibility._engine' not in sys.modules
        original = public.apply_move
        calls = []
        def injected(instance, state, move, **kwargs):
            calls.append(move.kind)
            if HOOK == 'reject':
                raise public.IllegalMoveError(Violation(
                    rule_id='expert.moves.top_access', severity='hard',
                    call_index=instance.call_index, message='pre-import rejection'))
            return original(instance, state, move, **kwargs)
        if METADATA == 'wrap_raw':
            from functools import wraps
            injected = wraps(original.__wrapped__)(injected)
        public.apply_move = injected
        if BINDING == 'public_and_owner':
            owner.apply_move = injected
        from stowage.actions.expander import Expander
        from stowage.actions.samples import tiny_instance
        from stowage.actions.space import Place
        from stowage.rules.catalogue import rule_freeze_v1
        with rule_freeze_v1():
            if CONSUMER == 'expander':
                instance = tiny_instance(61, n_load=1, n_other=0, p40=0)
                ex = Expander(instance)
                action = Place(instance.load_list[0], '0010182')
                before = ex.world_state()
                assert ex.can_expand(action) == (HOOK == 'forward')
                assert calls == ['load']
                calls.clear()
                if HOOK == 'reject':
                    try:
                        ex.step(action)
                    except ValueError as error:
                        assert 'pre-import rejection' in str(error)
                        assert isinstance(error.__cause__, public.IllegalMoveError)
                    else:
                        raise AssertionError('replacement rejection was bypassed')
                    assert ex.world_state() == before and not ex.moves
                else:
                    assert len(ex.step(action)) == 1
                assert calls == ['load']
            else:
                from stowage.actions import get_action_space
                from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
                from stowage.model.ship import Placement
                from stowage.model.yard import YardStack
                instance = tiny_instance(67, n_load=1, n_other=2, p40=0)
                ids = [box.id for box in instance.containers]
                instance.yard.stacks = [YardStack(key='A-001-01', length_ft=20, boxes=[ids[0]])]
                instance.onboard.placements = [Placement(container_id=ids[1], slot='0010182'),
                                               Placement(container_id=ids[2], slot='0010184')]
                engine = make_feasibility_engine(instance, get_action_space(),
                    config=FeasibilityConfig(extra={'global_screen': 0}))
                b, s = engine.T.box_index[ids[0]], engine.T.slot_index['0010102']
                def evaluate(state, *, allowed_boxes, allowed_slots, include_discharges):
                    state.mask = np.array([int(x)*len(engine.T.slot_ids)+int(y)
                                           for x in allowed_boxes for y in allowed_slots], dtype=np.int64)
                engine._evaluate = evaluate
                before = engine._cur.ex.world_state()
                calls.clear()
                result = engine._restow_places(engine._cur, np.array([b]), np.array([s]))
                assert result == ([(b, s)] if HOOK == 'forward' else [])
                assert calls == (['restow_off', 'restow_off', 'hatch_cover_off']
                                 if HOOK == 'forward' else ['restow_off'])
                assert engine._cur.ex.world_state() == before
    ''')
    configuration = f'CONSUMER={consumer!r}\nHOOK={hook!r}\nBINDING={binding!r}\nMETADATA={metadata!r}\n'
    result = subprocess.run([sys.executable, '-c', configuration + script],
                            cwd=Path(__file__).resolve().parents[2],
                            env=dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
                                     VECLIB_MAXIMUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1'),
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
