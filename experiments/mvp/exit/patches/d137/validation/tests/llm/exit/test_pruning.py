"""CPU-only D109 safety, accounting and actual sampler KV-row-drop contracts."""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from llm.exit.pruning import (
    Incumbent, PRUNED_STATUS, PruningController, committed_lift_lower_bound,
    exclude_from_preferences,
)


def instance(**overrides):
    weights = dict(relocate=1., restow=2., hatch_cover=3., future_restow=4., future_hatch_cover=5.)
    weights.update(overrides)
    return NS(objective=NS(**weights))


class Decoder:
    def __init__(self, moves=(), *, terminal_after=None):
        self.status = 'running'
        self.dead_end = None
        self.engine = NS(expander=NS(moves=list(moves)), depth=0)
        self.terminal_after = terminal_after
        self.tokens = []
        self.config = NS()

    def allowed_token_ids(self):
        return (0,) if self.status == 'running' else ()

    def advance(self, token):
        self.tokens.append(token)
        self.engine.expander.moves.append(NS(kind='relocate'))
        self.engine.depth += 1
        if self.terminal_after == len(self.tokens):
            self.status = 'complete'
        return token

    def prefetch(self):
        return True

    def diagnostics(self):
        return dict(status=self.status, dead_end=None, dead_ends_by_rule={})


def test_only_irrevocable_lifts_count_and_nonmonotone_projection_is_excluded():
    moves = [NS(kind=k) for k in ['load','discharge','relocate','restow_off','restow_on',
                                  'restow_shift','hatch_cover_off','hatch_cover_on']]
    assert committed_lift_lower_bound(instance(), moves) == 13
    # Future weight changes cannot change the committed bound.
    assert committed_lift_lower_bound(instance(future_restow=0, future_hatch_cover=999), moves) == 13
    assert committed_lift_lower_bound(instance(), moves[:3]) <= committed_lift_lower_bound(instance(), moves)


@pytest.mark.parametrize('field', ['relocate','restow','hatch_cover','future_restow','future_hatch_cover'])
@pytest.mark.parametrize('value', [-1, float('nan'), float('inf'), True])
def test_monotonicity_guard_disables_negative_nonfinite_or_unknown_weights(field, value):
    assert committed_lift_lower_bound(instance(**{field:value}), [NS(kind='relocate')]) is None


def test_unknown_and_simulator_moves_disable_bound():
    for kind in ['gate_in','gate_out','new_move',None]:
        assert committed_lift_lower_bound(instance(), [NS(kind=kind)]) is None


def test_incumbent_requires_complete_verified_measured_cost():
    for kwargs in [{'verified':False},{'complete':False},{'weighted_moves':-1},{'weighted_moves':float('nan')}]:
        with pytest.raises(ValueError):
            Incumbent(**({'weighted_moves':1} | kwargs))
    with pytest.raises(TypeError):
        PruningController(instance()).update(0)


def test_strictly_greater_only_then_separate_accounting_and_no_preferences():
    controller = PruningController(instance(), Incumbent(1, 'gold'))
    decoder = controller.wrap_decoder(Decoder(), sample_id=7)
    decoder.advance(0)
    assert decoder.status == 'running'  # tied incumbent: keep it
    decoder.advance(0)
    assert decoder.status == PRUNED_STATUS
    assert decoder.allowed_token_ids() == () and decoder.prefetch() is False
    assert decoder.dead_end is None
    assert decoder.diagnostics()['dead_ends_by_rule'] == {}
    decoder.allowed_token_ids()
    summary = controller.summary()
    assert summary['pruned_samples'] == 1
    assert summary['dead_ends_added'] == summary['dpo_rejected_examples_added'] == 0
    assert summary['gpu_time_saved_s'] is None  # no fabricated performance
    assert summary['events'][0]['sample_id'] == 7
    assert exclude_from_preferences({'metadata':{'status':decoder.status}})
    assert exclude_from_preferences({'status':decoder.status})
    assert not exclude_from_preferences({'status':'dead_end'})


def test_dynamic_checked_incumbent_update_prunes_existing_samples():
    controller = PruningController(instance())
    decoder = controller.wrap_decoder(Decoder([NS(kind='relocate')]))
    assert decoder.allowed_token_ids()
    assert controller.update(Incumbent(0, 'checked_model'))
    assert not controller.update(Incumbent(5, 'gold'))
    assert decoder.allowed_token_ids() == ()
    assert decoder.status == PRUNED_STATUS


@pytest.mark.parametrize('kind', ['complete','dead_end'])
def test_existing_terminal_statuses_preserved(kind):
    controller = PruningController(instance(), Incumbent(0))
    raw = Decoder([NS(kind='relocate')])
    raw.status = kind
    decoder = controller.wrap_decoder(raw)
    assert decoder.allowed_token_ids() == () and decoder.status == kind
    assert controller.summary()['pruned_samples'] == 0


@pytest.mark.parametrize('kwargs', [{'append_only':False},{'exempt':True}])
def test_greedy_fitness_or_nonappend_only_sample_is_exempt(kwargs):
    controller = PruningController(instance(), Incumbent(0))
    decoder = controller.wrap_decoder(Decoder([NS(kind='relocate')]), **kwargs)
    assert decoder.allowed_token_ids() and decoder.status == 'running'


def test_backtracking_flags_and_observed_prefix_rewrite_disable_pruning():
    controller = PruningController(instance(), Incumbent(5))
    raw = Decoder([NS(kind='relocate')])
    raw.config.backtrack_budget = 3
    wrapped = controller.wrap_decoder(raw)
    assert not wrapped.enabled and wrapped.allowed_token_ids()
    raw.config.backtrack_budget = 0
    wrapped = controller.wrap_decoder(raw)
    assert wrapped.allowed_token_ids()
    raw.engine.expander.moves.clear()
    assert wrapped.allowed_token_ids()
    assert not wrapped.enabled and wrapped.disabled_reason == 'nonmonotone_prefix_rewritten'


def test_unavailable_engine_cost_state_disables_pruning():
    raw = Decoder()
    raw.engine = NS(depth=0)
    decoder = PruningController(instance(), Incumbent(0)).wrap_decoder(raw)
    assert decoder.allowed_token_ids()
    assert decoder.disabled_reason == 'committed_moves_unavailable'


def test_production_engine_private_expander_supported():
    raw = Decoder([NS(kind='relocate')])
    raw.engine = NS(_cur=NS(ex=raw.engine.expander), depth=1)
    decoder = PruningController(instance(), Incumbent(0)).wrap_decoder(raw)
    assert decoder.allowed_token_ids() == () and decoder.status == PRUNED_STATUS


def test_real_generate_masked_drops_pruned_kv_rows_without_mlx_or_gpu():
    """Execute the *actual* sampler functions with NumPy backend substitutions.

    This checks both token batch and cache-row shrink, rather than simulating a
    different batch algorithm or loading MLX (which could initialize Metal).
    """
    import threading
    import time
    from contextvars import copy_context
    from dataclasses import dataclass, field
    from typing import Any
    path = Path(importlib.util.find_spec('llm.stowlm.mask').origin).with_name('mask_mlx.py')
    source = ast.parse(path.read_text())
    names = {'SequenceResult','GenerationStats','_keep','generate_masked'}
    body = [node for node in source.body if isinstance(node, (ast.FunctionDef,ast.ClassDef)) and node.name in names]
    mx = NS(array=np.array, async_eval=lambda *a:None, eval=lambda *a:None,
            random=NS(key=lambda s:s, split=lambda k:(k+1,k+2)))
    cache = [NS(keys=np.zeros((2,1,1,1)),values=np.zeros((2,1,1,1)))]
    sizes = []
    def model(ids, *, cache):
        sizes.append((len(ids),len(cache[0].keys)))
        return np.zeros((len(ids),1,1))
    def sample(logits,idx,valid,temperature,key):
        return np.zeros(len(logits),dtype=int),np.zeros(len(logits))
    env = dict(mx=mx, math=__import__('math'), threading=threading,time=time,
               dataclass=dataclass,field=field,Any=Any,copy_context=copy_context,
               rule_freeze_v1_enabled=lambda:False,
               prefill=lambda *a:cache, _tile=lambda *a:None,
               _index_matrix=lambda sets:(None,None),masked_sample=sample)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*body],type_ignores=[])),str(path),'exec'),env)
    controller = PruningController(instance(),Incumbent(0,'gold'))
    # Row 0 becomes too costly after its first action; exempt row 1 finishes in 2.
    decoders = [controller.wrap_decoder(Decoder(),sample_id=0),
                controller.wrap_decoder(Decoder(terminal_after=2),sample_id=1,exempt=True)]
    results, stats = env['generate_masked'](model,[0],decoders,max_tokens=5,prefetch=False)
    assert [result.status for result in results] == [PRUNED_STATUS,'complete']
    assert sizes == [(2,2),(1,1)]  # both token rows AND real _keep KV rows shrink
    assert stats.batch_sizes == [2,1]
    assert controller.summary()['pruned_samples'] == 1


def test_provider_updates_incumbent_and_failure_never_fabricates_one():
    controller = PruningController(instance(), incumbent_provider=lambda:Incumbent(0,'checked_future'))
    decoder = controller.wrap_decoder(Decoder([NS(kind='relocate')]))
    assert decoder.allowed_token_ids() == () and decoder.status == PRUNED_STATUS
    assert controller.incumbent.source == 'checked_future'
    bad = PruningController(instance(), incumbent_provider=lambda:1)
    raw = bad.wrap_decoder(Decoder([NS(kind='relocate')]))
    assert raw.allowed_token_ids() and raw.status == 'running'
    assert bad.summary()['provider_error'] == 'TypeError'
    assert bad.incumbent is None


def test_grouped_float_multiplication_keeps_exact_move_engine_ties():
    moves = [NS(kind='relocate') for _ in range(10)]
    assert committed_lift_lower_bound(instance(relocate=.1),moves) == .1 * 10


def test_explicit_pop_disables_pruning_before_delegate_rewinds():
    raw = Decoder([NS(kind='relocate')])
    raw.pop_action = lambda:'popped'
    wrapped = PruningController(instance(),Incumbent(0)).wrap_decoder(raw)
    assert wrapped.pop_action() == 'popped'
    assert wrapped.allowed_token_ids() and not wrapped.enabled


def test_sampler_identity_guard_rejects_unknown_and_future_backtracking():
    from llm.exit.pruning import sampler_is_append_only
    def custom_sampler(*args,**kwargs):
        pass
    def backtracking_sampler(decoder):
        decoder.pop_action()
    assert not sampler_is_append_only(custom_sampler)
    assert not sampler_is_append_only(backtracking_sampler)
    assert not sampler_is_append_only(object())
