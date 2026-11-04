"""Policy/intersection tests with production tiny executor and synthetic bounds."""
from types import SimpleNamespace

import numpy as np
import pytest

from llm.instructions.mask_filter import InstructionEngine
from llm.lookahead import LookaheadConfig,LookaheadEngine,lookahead_decoder_factory
from llm.lookahead.bounds import BoundResult
from llm.securing import SecuringEngine
from llm.stowlm.mask import MaskConfig,MaskedDecoder,MaskError
from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig,IllegalActionError,make_feasibility_engine
from tests.llm.securing.test_mask_filter import SelectiveScreen,codec_for


@pytest.fixture
def engine():
    instance = tiny_instance(719,n_load=2,n_other=0,p40=0)
    return make_feasibility_engine(instance,get_action_space(),
        config=FeasibilityConfig(extra={'global_screen':0}))


class SyntheticCapacity:
    """A selective test policy, not a physical impossibility claim."""
    def __init__(self,rejected=(),*,reject_root=False,unknown=False):
        self.rejected,self.reject_root,self.unknown = set(rejected),reject_root,unknown
        self.calls = 0
    def check(self,ex,*,native_contract):
        self.calls += 1
        if self.unknown:
            return BoundResult('unknown','synthetic_unavailable')
        rejected = self.reject_root or any(ex.where.get(a.box) == ('ship',a.slot) for a in self.rejected)
        return BoundResult('reject' if rejected else 'admit','synthetic_capacity_deficit' if rejected else None)


@pytest.mark.parametrize('indexed',[False,True])
def test_all_interfaces_tokens_and_push_share_filtered_set(engine,indexed):
    actions = engine.legal_actions()
    denied = actions[0]
    wrapper = LookaheadEngine(engine,capacity=SyntheticCapacity([denied]))
    expected = set(actions)-{denied}
    assert set(wrapper.legal_actions()) == expected
    assert {wrapper.decode_index(int(flat)) for flat in wrapper.legal_index()} == expected
    rows,cols = wrapper.legal_pairs()
    assert {Place(wrapper.index_boxes[int(b)],wrapper.index_slots[int(s)]) for b,s in zip(rows,cols)} == expected
    assert set(wrapper.legal_candidates(actions)) == expected
    assert set(wrapper.iter_legal_actions()) == expected
    assert denied.slot not in wrapper.legal_slots_for_box(denied.box)
    assert not wrapper.is_legal(denied)
    assert not wrapper.legal_index().flags.writeable
    decoder = MaskedDecoder(wrapper,codec_for(wrapper),MaskConfig(index_arrays=indexed))
    for token in codec_for(wrapper).encode_action(denied):
        if token not in decoder.allowed_token_ids():
            with pytest.raises(MaskError):
                decoder.advance(token)
            break
        decoder.advance(token)
    else:
        pytest.fail('capacity-rejected action remained token-decodable')
    with pytest.raises(IllegalActionError):
        wrapper.push(denied)
    assert engine.depth == 0


def test_unknown_and_zero_probe_budget_preserve_existing_mask(engine):
    legal = set(engine.legal_actions())
    unknown = LookaheadEngine(engine.copy(),capacity=SyntheticCapacity(unknown=True))
    zero = LookaheadEngine(engine.copy(),config=LookaheadConfig(max_action_probes_per_state=0),
                           capacity=SyntheticCapacity(legal))
    assert set(unknown.legal_actions()) == set(zero.legal_actions()) == legal
    assert unknown.screen_diagnostics()['lookahead']['candidate_probes'] == 0
    assert zero.screen_diagnostics()['lookahead']['candidate_probes'] == 0


def test_all_filtered_and_root_deficit_are_explicit_dead_ends_with_no_eos(engine):
    for policy in (SyntheticCapacity(engine.legal_actions()),SyntheticCapacity(reject_root=True)):
        wrapper = LookaheadEngine(engine.copy(),capacity=policy)
        assert wrapper.legal_actions() == [] and not wrapper.is_complete()
        assert wrapper.dead_end() is not None
        assert wrapper.dead_end().rule_ids == ('lookahead.necessary_future_capacity',)
        decoder = MaskedDecoder(wrapper,codec_for(wrapper),MaskConfig())
        assert decoder.allowed_token_ids() == () and decoder.status == 'dead_end'
        with pytest.raises(MaskError):
            decoder.advance(codec_for(wrapper).end_plan_tokens[0])


def test_instructions_securing_and_lookahead_intersect_without_secondary_bypass(engine):
    actions = engine.legal_actions()
    denied = actions[:3]
    constrained = SimpleNamespace(instance=engine.instance,
        forbids=lambda box,slot:'closed' if Place(box,slot) == denied[0] else None)
    inner = SecuringEngine(InstructionEngine(engine,constrained),screen=SelectiveScreen([denied[1]]))
    wrapper = LookaheadEngine(inner,capacity=SyntheticCapacity([denied[2]]))
    expected = set(actions)-set(denied)
    assert set(wrapper.legal_actions()) == expected
    assert set(wrapper.legal_candidates(actions)) == expected
    assert wrapper.native_contract
    for action in denied:
        assert not wrapper.is_legal(action)


def test_copy_pop_siblings_cache_and_external_mutation_are_independent(engine):
    policy = SyntheticCapacity()
    wrapper = LookaheadEngine(engine,capacity=policy)
    initial = wrapper.legal_actions()
    calls = policy.calls
    assert wrapper.legal_actions() == initial and policy.calls == calls
    child = wrapper.copy()
    child.push(initial[0])
    assert child.depth == 1 and wrapper.depth == 0
    child.pop()
    assert child.legal_actions() == initial
    engine.push(initial[1])
    diagnostic = wrapper.screen_diagnostics()['lookahead']
    assert not diagnostic['evaluated_current_state']
    assert initial[1] not in wrapper.legal_actions()


def test_failed_inner_push_does_not_commit_trial(engine,monkeypatch):
    wrapper = LookaheadEngine(engine,capacity=SyntheticCapacity())
    action = wrapper.legal_actions()[0]
    before = dict(wrapper.expander.where)
    def rejected(action):
        raise IllegalActionError('synthetic inner rejection')
    monkeypatch.setattr(engine,'push',rejected)
    with pytest.raises(IllegalActionError):
        wrapper.push(action)
    assert wrapper.expander.where == before and wrapper.depth == 0


def test_factory_and_complete_eos_keep_samples_separate(engine,monkeypatch):
    import llm.lookahead.mask_filter as module
    monkeypatch.setattr(module,'OptimisticCapacity',lambda *args:SyntheticCapacity())
    factory = lookahead_decoder_factory(securing=False,config=LookaheadConfig(support_conditioned=False))
    decoders = factory(engine.instance,engine,codec_for(engine),MaskConfig(),2)
    assert decoders[0].engine.capacity is decoders[1].engine.capacity
    while not decoders[0].engine.is_complete():
        action = decoders[0].engine.legal_actions()[0]
        decoders[0].engine.push(action)
    assert decoders[1].engine.depth == 0
    decoder = MaskedDecoder(decoders[0].engine,codec_for(engine),MaskConfig())
    for token in codec_for(engine).end_plan_tokens:
        assert decoder.allowed_token_ids() == (token,)
        decoder.advance(token)
    assert decoder.status == 'complete'


def test_policy_instance_identity_is_validated(engine):
    policy = SyntheticCapacity()
    policy.instance = engine.instance.model_copy()
    with pytest.raises(ValueError,match='share'):
        LookaheadEngine(engine,capacity=policy)


def test_real_capacity_uses_native_contract_but_unknown_proxy_fails_open(engine):
    wrapped = LookaheadEngine(engine)
    assert wrapped.native_contract
    assert wrapped.legal_actions()
    class UnknownProxy:
        def __getattr__(self,name):
            return getattr(engine,name)
    unknown = LookaheadEngine(UnknownProxy())
    assert not unknown.native_contract
    assert set(unknown.legal_actions()) == set(engine.legal_actions())


def test_real_bound_removes_production_action_that_strands_joint_tall_capacity():
    """Native C1 uses global 2.4m height; this selected pool needs 3m each.

    Synthetic geometry isolates a completion impossibility, not a physical
    ship approval. Actual native mask/Expander and actual new bound are used.
    """
    instance = tiny_instance(746,n_load=4,n_other=0,p40=0)
    boxes = [box.model_copy(update={'height_m':3. if i < 3 else 2.4,
                                   'gross_mass_kg':9000.})
             for i,box in enumerate(instance.containers)]
    instance.containers = boxes
    original_cells = {cell.id:cell for cell in instance.vessel.cells}
    original_stacks = {stack.id:stack for stack in instance.vessel.stacks}
    cells,stacks = [],[]
    for ids,roof in ((['0010182','0010184','0010186'],8.),
                     (['0030182'],2.9),(['0010282'],2.9)):
        template = original_cells[ids[0]]
        stack = original_stacks[template.stack_id].model_copy(update={
            'cell_ids':ids,'max_height_m':roof,'hatch_cover_id':None})
        stacks.append(stack)
        for index,cid in enumerate(ids):
            cells.append(template.model_copy(update={'id':cid,
                'vcg_base_m':stack.base_z_m+index*2.4}))
    instance.vessel = instance.vessel.model_copy(update={
        'cells':cells,'stacks':stacks,'hatch_covers':[]})
    instance.port.quay_crane.max_on_deck_tiers = 3
    for call in instance.rotation.calls:
        call.max_on_deck_tiers = 3
    inner = make_feasibility_engine(instance,get_action_space(),
        config=FeasibilityConfig(extra={'global_screen':0}))
    underlying = inner.legal_actions()
    assert underlying, 'fixture must demonstrate a gap beyond the production completion screen'
    wrapper = LookaheadEngine(inner)
    assert wrapper.legal_actions() == []
    assert wrapper.dead_end().rule_ids == ('lookahead.necessary_future_capacity',)
    certificate = wrapper.screen_diagnostics()['lookahead']['last_certificate']
    assert certificate['pool_units'] == 3 and certificate['pool_capacity_units'] == 2
    assert set(underlying) - set(wrapper.legal_actions())


def test_default_factory_capacity_precedes_securing_and_preserves_intersections(engine,monkeypatch):
    import llm.lookahead.mask_filter as module
    actions = engine.legal_actions()
    instruction_denied,securing_denied,capacity_denied = actions[:3]
    constrained = SimpleNamespace(instance=engine.instance,forbids=lambda box,slot:
        'closed' if Place(box,slot) == instruction_denied else None)
    policy = SyntheticCapacity([capacity_denied])
    screen = SelectiveScreen([securing_denied])
    monkeypatch.setattr(module,'OptimisticCapacity',lambda *args:policy)
    factory = lookahead_decoder_factory(
        engine_wrapper=lambda inner:InstructionEngine(inner,constrained),
        securing_screen=screen,config=LookaheadConfig(support_conditioned=False))
    decoders = factory(engine.instance,engine,codec_for(engine),MaskConfig(),2)
    wrapper = decoders[0].engine
    assert isinstance(wrapper,SecuringEngine)
    assert isinstance(wrapper.inner,LookaheadEngine)
    assert isinstance(wrapper.inner.inner,InstructionEngine)
    expected = set(actions)-{instruction_denied,securing_denied,capacity_denied}
    assert set(wrapper.legal_actions()) == expected
    assert all(action != capacity_denied for _,action in screen.queries)
    assert all(action != instruction_denied for _,action in screen.queries)
    assert wrapper.screen is decoders[1].engine.screen is screen
    wrapper.push(wrapper.legal_actions()[0])
    assert wrapper.depth == 1 and decoders[1].engine.depth == 0
    wrapper.pop()
    assert set(wrapper.legal_actions()) == expected


@pytest.mark.parametrize('reason',['capacity','securing','terminal_securing'])
def test_default_order_eos_and_dead_end_remain_intersections(engine,monkeypatch,reason):
    import llm.lookahead.mask_filter as module
    if reason == 'terminal_securing':
        while not engine.is_complete():
            engine.push(engine.legal_actions()[0])
    capacity = SyntheticCapacity(reject_root=reason == 'capacity')
    monkeypatch.setattr(module,'OptimisticCapacity',lambda *args:capacity)
    screen = SelectiveScreen(engine.legal_actions() if reason == 'securing' else (),
                             reject_complete=reason == 'terminal_securing')
    decoder = lookahead_decoder_factory(securing_screen=screen,config=LookaheadConfig(support_conditioned=False))(
        engine.instance,engine,codec_for(engine),MaskConfig(),1)[0]
    assert not decoder.engine.is_complete()
    assert decoder.engine.dead_end() is not None
    assert decoder.allowed_token_ids() == () and decoder.status == 'dead_end'


def test_default_order_successful_completion_exposes_only_canonical_eos(engine,monkeypatch):
    import llm.lookahead.mask_filter as module
    monkeypatch.setattr(module,'OptimisticCapacity',lambda *args:SyntheticCapacity())
    wrapper = lookahead_decoder_factory(securing_screen=SelectiveScreen(),
        config=LookaheadConfig(support_conditioned=False))(
        engine.instance,engine,codec_for(engine),MaskConfig(),1)[0].engine
    while not wrapper.is_complete():
        wrapper.push(wrapper.legal_actions()[0])
    decoder = MaskedDecoder(wrapper,codec_for(engine),MaskConfig())
    for token in codec_for(engine).end_plan_tokens:
        assert decoder.allowed_token_ids() == (token,)
        decoder.advance(token)
    assert decoder.status == 'complete'
