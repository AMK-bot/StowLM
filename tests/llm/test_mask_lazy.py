"""Activated box/slot decoding must not eagerly enumerate unchosen boxes."""
import pytest

from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.mask import MaskConfig, MaskedDecoder
from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.rules.catalogue import rule_freeze_v1


@pytest.mark.parametrize('lookahead', [0, 8])
def test_activated_lazy_domains_equal_reference_without_eager_calls(monkeypatch, lookahead):
    with rule_freeze_v1():
        inst = tiny_instance(23, n_load=4)
        def make_engine():
            return make_feasibility_engine(inst, get_action_space('boxslot'),
                                           config=FeasibilityConfig(extra={'global_screen': 0}))
        engine = make_engine()
        codec = ActionCodec(load_tokenizer(), [box.id for box in inst.containers])
        expected = make_engine().legal_actions()
        if not expected:
            pytest.fail('fixture must have legal choices')
        decoder = MaskedDecoder(engine, codec, MaskConfig(lookahead=lookahead))
        # Lookahead intentionally needs the complete action population.
        if lookahead == 0:
            def eager_forbidden(*args, **kwargs):
                raise AssertionError('eager action enumeration during box/slot decoding')
            monkeypatch.setattr(type(engine), 'legal_index', eager_forbidden)
            monkeypatch.setattr(type(engine), 'legal_actions', eager_forbidden)
            monkeypatch.setattr(type(engine), 'legal_discharges', eager_forbidden)
        action = expected[0]
        trie = codec.trie(expected)
        prefix = []
        for token in codec.encode_action(action):
            assert decoder.allowed_token_ids() == tuple(sorted(trie.allowed_next_token_ids(prefix)))
            decoder.advance(token)
            prefix.append(token)
        assert decoder.actions == [action]


def test_legacy_never_queries_activated_lazy_domains(monkeypatch):
    with rule_freeze_v1(enabled=False):
        inst = tiny_instance(23, n_load=4)
        engine = make_feasibility_engine(inst, get_action_space('boxslot'),
                                        config=FeasibilityConfig(extra={'global_screen': 0}))
        def lazy_forbidden(*args, **kwargs):
            raise AssertionError('activated API used under legacy switch')
        monkeypatch.setattr(type(engine), 'legal_box_ids', lazy_forbidden)
        monkeypatch.setattr(type(engine), 'legal_slots_for_box', lazy_forbidden)
        codec = ActionCodec(load_tokenizer(), [box.id for box in inst.containers])
        decoder = MaskedDecoder(engine, codec)
        expected = codec.trie(engine.legal_actions())
        prefix = []
        action = engine.legal_actions()[0]
        for token in codec.encode_action(action):
            assert decoder.allowed_token_ids() == tuple(sorted(expected.allowed_next_token_ids(prefix)))
            decoder.advance(token)
            prefix.append(token)


def test_diagnostics_is_defensive_and_does_not_force_eager_domains(monkeypatch):
    with rule_freeze_v1():
        inst = tiny_instance(23, n_load=4)
        engine = make_feasibility_engine(inst, get_action_space('boxslot'),
                                        config=FeasibilityConfig(extra={'global_screen': 0}))
        decoder = MaskedDecoder(engine, ActionCodec(load_tokenizer(), [box.id for box in inst.containers]))
        assert decoder.allowed_token_ids()
        before = decoder.diagnostics()
        assert before['screens']['complete_evaluation'] is False
        before['screens']['rejected']['made-up-rule'] = 1
        assert 'made-up-rule' not in decoder.diagnostics()['screens']['rejected']


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('semantic', ['soft_e6', 'exact_mass'])
def test_mask_delegates_e6_and_mass_order_to_the_activated_engine(enabled, semantic):
    """Decode actual rule-boundary actions, not just a synthetic engine stub."""
    import importlib.util
    from pathlib import Path
    from stowage.actions.space import Place

    source = Path(__file__).resolve().parents[1] / 'feasibility/test_w2_weight_oog.py'
    spec = importlib.util.spec_from_file_location('mask_weight_rule_fixture', source)
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    inst = (fixtures.case(lower=25000., upper=15000., onboard=False)
            if semantic == 'soft_e6' else fixtures.case(lower=12000., upper=13000.))
    action = (Place(inst.containers[0].id, '0010182') if semantic == 'soft_e6'
              else Place(inst.load_list[0], '0010184'))
    with rule_freeze_v1(enabled=enabled):
        engine = fixtures.engine(inst)
        codec = ActionCodec(load_tokenizer(), [box.id for box in inst.containers])
        decoder = MaskedDecoder(engine, codec)
        accepted = True
        for token in codec.encode_action(action):
            if token not in decoder.allowed_token_ids():
                accepted = False
                break
            decoder.advance(token)
        assert accepted is (enabled if semantic == 'soft_e6' else not enabled)


def test_activated_mask_preserves_custom_action_only_engine_compatibility():
    """The optional lazy API cannot make instance/index metadata mandatory."""
    from stowage.actions.space import Place

    inst = tiny_instance(23, n_load=1)
    action = Place(inst.load_list[0], '0010182')

    class ActionOnlyEngine:
        def __init__(self, depth=0):
            self.depth = depth

        def is_complete(self):
            return self.depth == 1

        def legal_actions(self):
            return () if self.is_complete() else (action,)

        def copy(self):
            return ActionOnlyEngine(self.depth)

        def push(self, chosen):
            assert chosen == action and not self.is_complete()
            self.depth += 1

        def actions(self):
            return [action] if self.is_complete() else []

    with rule_freeze_v1():
        codec = ActionCodec(load_tokenizer(), [box.id for box in inst.containers])
        decoder = MaskedDecoder(ActionOnlyEngine(), codec)
        for token in (*codec.encode_action(action), *codec.end_plan_tokens):
            assert token in decoder.allowed_token_ids()
            decoder.advance(token)
        assert decoder.status == 'complete' and decoder.actions == [action]
