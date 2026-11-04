"""CPU-only securing policy regressions; no model or downloaded tokenizer."""
from types import SimpleNamespace

import numpy as np
import pytest

from llm.instructions.mask_filter import InstructionEngine
from llm.securing import SecuringEngine, securing_decoder_factory, wrap_engine
from llm.stowlm.format import ActionCodec
from llm.stowlm.mask import MaskConfig, MaskedDecoder, MaskError
from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig, IllegalActionError, make_feasibility_engine
from stowage.solver.securing_screen import SecuringScreenResult


class CharacterTokenizer:
    def encode(self, text, **kwargs):
        return SimpleNamespace(ids=[ord(c) for c in text])

    def decode(self, ids, **kwargs):
        return ''.join(chr(i) for i in ids)


class SelectiveScreen:
    def __init__(self, rejected=(), *, reject_complete=False):
        self.rejected = set(rejected)
        self.reject_complete = reject_complete
        self.metrics = {}
        self.queries = []

    def check_placement(self, expander, action):
        self.queries.append((tuple(sorted(expander.where.items())),action))
        return SecuringScreenResult(action not in self.rejected, 2. if action in self.rejected else .5, 'synthetic securing rejection')

    def check_state(self, expander):
        return SecuringScreenResult(not self.reject_complete, 2. if self.reject_complete else .5, 'synthetic securing terminal rejection')


@pytest.fixture
def engine():
    instance = tiny_instance(719, n_load=2, n_other=0, p40=0)
    return make_feasibility_engine(instance,get_action_space('boxslot'),
        config=FeasibilityConfig(extra={'global_screen':0}))


def codec_for(engine):
    return ActionCodec(CharacterTokenizer(),[b.id for b in engine.instance.containers])


@pytest.mark.parametrize('indexed',[False,True])
def test_no_legal_interface_or_token_path_bypasses_policy(engine,indexed):
    base = engine.legal_actions()
    assert len(base)>1
    denied = base[0]
    screen = SelectiveScreen([denied])
    wrapped = SecuringEngine(engine,screen=screen)
    expected = set(base)-{denied}
    assert set(wrapped.legal_actions()) == expected
    assert {wrapped.decode_index(int(i)) for i in wrapped.legal_index()} | set(wrapped.legal_discharges()) == expected
    assert not wrapped.is_legal(denied)
    assert denied.slot not in wrapped.legal_slots_for_box(denied.box)
    assert set(wrapped.legal_box_ids()) == {a.box for a in expected if isinstance(a,Place)}
    codec = codec_for(wrapped)
    dec = MaskedDecoder(wrapped,codec,MaskConfig(lookahead=0,index_arrays=indexed))
    # A shared opcode/box prefix may remain legal. At the first divergence,
    # the rejected action must have no continuation; it must never commit.
    rejected = False
    for token in codec.encode_action(denied):
        if token not in dec.allowed_token_ids():
            with pytest.raises(MaskError):
                dec.advance(token)
            rejected = True
            break
        assert dec.advance(token) is None
    assert rejected
    assert wrapped.depth == 0


def test_push_rejects_before_inner_state_changes(engine):
    denied = engine.legal_actions()[0]
    wrapped = wrap_engine(engine,screen=SelectiveScreen([denied]))
    before = tuple(wrapped.actions())
    with pytest.raises(IllegalActionError):
        wrapped.push(denied)
    assert tuple(wrapped.actions()) == before
    assert wrapped.depth == engine.depth == 0


def test_copy_state_and_cache_do_not_leak_legal_sets(engine):
    screen = SelectiveScreen()
    wrapped = SecuringEngine(engine,screen=screen)
    initial = tuple(wrapped.legal_actions())
    count = len(screen.queries)
    assert tuple(wrapped.legal_actions()) == initial
    assert len(screen.queries) == count
    child = wrapped.copy()
    child.push(initial[0])
    assert child.depth == 1 and wrapped.depth == 0
    assert tuple(wrapped.legal_actions()) == initial
    assert initial[0] not in child.legal_actions()
    assert wrapped.instance is child.instance


def test_empty_policy_set_reports_explicit_dead_end_and_masks_eos(engine):
    wrapped = SecuringEngine(engine,screen=SelectiveScreen(engine.legal_actions()))
    assert wrapped.legal_actions() == []
    assert not len(wrapped.legal_index())
    assert not wrapped.is_complete()
    dead = wrapped.dead_end()
    assert dead is not None and 'secur' in dead.reason.lower()
    codec = codec_for(wrapped)
    dec = MaskedDecoder(wrapped,codec,MaskConfig(lookahead=0))
    assert dec.allowed_token_ids() == ()
    assert dec.status == 'dead_end'
    with pytest.raises(MaskError):
        dec.advance(codec.end_plan_tokens[0])


def test_inner_complete_does_not_allow_eos_when_terminal_screen_rejects(engine):
    while not engine.is_complete():
        legal = engine.legal_actions()
        assert legal
        engine.push(legal[0])
    wrapped = SecuringEngine(engine,screen=SelectiveScreen(reject_complete=True))
    assert engine.is_complete() and not wrapped.is_complete()
    assert wrapped.dead_end() is not None
    codec = codec_for(wrapped)
    dec = MaskedDecoder(wrapped,codec,MaskConfig(lookahead=0))
    assert dec.allowed_token_ids() == ()
    assert dec.status == 'dead_end'
    with pytest.raises(MaskError):
        dec.advance(codec.end_plan_tokens[0])


def test_instruction_and_securing_filters_compose_in_both_orders(engine):
    actions = engine.legal_actions()
    denied_securing, denied_instruction = actions[:2]
    constraints = SimpleNamespace(instance=engine.instance,
        forbids=lambda box,slot: 'instruction' if Place(box,slot)==denied_instruction else None)
    expected = set(actions)-{denied_securing,denied_instruction}
    a = SecuringEngine(InstructionEngine(engine.copy(),constraints),screen=SelectiveScreen([denied_securing]))
    b = InstructionEngine(SecuringEngine(engine.copy(),screen=SelectiveScreen([denied_securing])),constraints)
    for wrapped in (a,b):
        assert set(wrapped.legal_actions()) == expected
        assert {wrapped.decode_index(int(i)) for i in wrapped.legal_index()} == expected
        assert not wrapped.is_legal(denied_securing)
        assert not wrapped.is_legal(denied_instruction)
        for denied in (denied_securing,denied_instruction):
            with pytest.raises(IllegalActionError):
                wrapped.push(denied)


def test_secondary_interfaces_pop_and_direct_inner_changes_remain_filtered(engine):
    denied = engine.legal_actions()[0]
    screen = SelectiveScreen([denied])
    wrapped = SecuringEngine(engine,screen=screen)
    allowed = wrapped.legal_actions()
    assert list(wrapped.legal_candidates([denied,*allowed])) == allowed
    assert set(wrapped.iter_legal_actions()) == set(allowed)
    rows,columns = wrapped.legal_pairs()
    assert {Place(wrapped.index_boxes[int(b)],wrapped.index_slots[int(s)]) for b,s in zip(rows,columns)} == set(allowed)
    assert not wrapped.legal_index().flags.writeable
    wrapped.push(allowed[0])
    wrapped.pop()
    assert set(wrapped.legal_actions()) == set(allowed)
    # Shared inner mutation must invalidate the wrapper's state cache too.
    engine.push(allowed[0])
    assert allowed[0] not in wrapped.legal_actions()
    assert wrapped.depth == 1


def test_factory_composes_instruction_filter_and_keeps_samples_independent(engine,monkeypatch):
    import llm.securing.mask_filter as module
    denied = engine.legal_actions()[0]
    constraints = SimpleNamespace(instance=engine.instance,
        forbids=lambda box,slot: 'instruction' if Place(box,slot)==denied else None)
    screen = SelectiveScreen()
    monkeypatch.setattr(module,'NecessaryDeckSecuringScreen',lambda instance:screen)
    factory = securing_decoder_factory(engine_wrapper=lambda e:InstructionEngine(e,constraints))
    decoders = factory(engine.instance,engine,codec_for(engine),MaskConfig(lookahead=0),2)
    assert len(decoders)==2
    assert all(not dec.engine.is_legal(denied) for dec in decoders)
    assert decoders[0].engine.screen is decoders[1].engine.screen
    action = decoders[0].engine.legal_actions()[0]
    for token in codec_for(engine).encode_action(action):
        assert token in decoders[0].allowed_token_ids()
        decoders[0].advance(token)
    assert decoders[0].engine.depth == 1
    assert decoders[1].engine.depth == 0


def test_successful_terminal_state_allows_only_canonical_eos(engine):
    while not engine.is_complete():
        engine.push(engine.legal_actions()[0])
    wrapped = SecuringEngine(engine,screen=SelectiveScreen())
    codec = codec_for(wrapped)
    dec = MaskedDecoder(wrapped,codec,MaskConfig(lookahead=0))
    for token in codec.end_plan_tokens:
        assert dec.allowed_token_ids() == (token,)
        dec.advance(token)
    assert dec.status == 'complete' and dec.dead_end is None


def test_nonindexed_engine_fallback_preserves_securing_policy(engine):
    class NonIndexed:
        def __init__(self,inner):
            self.inner = inner

        def __getattr__(self,name):
            if name in ('legal_index','index_slots','index_boxes'):
                raise AttributeError(name)
            return getattr(self.inner,name)

        def copy(self):
            return NonIndexed(self.inner.copy())

    denied = engine.legal_actions()[0]
    wrapped = SecuringEngine(NonIndexed(engine),screen=SelectiveScreen([denied]))
    assert not wrapped.is_legal(denied)
    assert denied not in wrapped.legal_actions()
    assert {wrapped.decode_index(int(f)) for f in wrapped.legal_index()} == set(engine.legal_actions())-{denied}
    codec = codec_for(wrapped)
    decoder = MaskedDecoder(wrapped,codec,MaskConfig(lookahead=0))
    for token in codec.encode_action(denied):
        if token not in decoder.allowed_token_ids():
            break
        decoder.advance(token)
    else:
        pytest.fail('nonindexed fallback allowed rejected action tokens')


def test_failed_inner_push_does_not_commit_expander_trial(engine,monkeypatch):
    wrapped = SecuringEngine(engine,screen=SelectiveScreen())
    action = wrapped.legal_actions()[0]
    before = dict(wrapped.expander.where)

    def fail(action):
        raise IllegalActionError('injected inner rejection')

    monkeypatch.setattr(engine,'push',fail)
    with pytest.raises(IllegalActionError):
        wrapped.push(action)
    assert wrapped.expander.where == before
    assert wrapped.depth == 0


def test_wrapping_advanced_engine_reconstructs_existing_prefix(engine):
    first = engine.legal_actions()[0]
    engine.push(first)
    screen = SelectiveScreen()
    wrapped = SecuringEngine(engine,screen=screen)
    assert wrapped.depth == 1
    assert wrapped.expander.where[first.box] == ('ship',first.slot)
    wrapped.legal_actions()
    assert screen.queries
    assert all(dict(state)[first.box] == ('ship',first.slot) for state,action in screen.queries)
