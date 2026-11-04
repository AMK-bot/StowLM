"""The last action checks all deck components before its tokens are offered."""
import pytest

from llm.securing import SecuringEngine
from llm.stowlm.mask import MaskConfig, MaskedDecoder
from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Discharge
from stowage.conventions import parse_slot
from stowage.feasibility import FeasibilityConfig, IllegalActionError, make_feasibility_engine
from stowage.solver.securing_screen import SecuringScreenResult
from tests.llm.securing.test_mask_filter import codec_for


class DeferredPrefixScreen:
    metrics = {}

    def __init__(self):
        self.terminal_calls = 0

    def check_placement(self, expander, action):
        return SecuringScreenResult(True, 0., 'prefix deferred')

    def check_state(self, expander):
        self.terminal_calls += 1
        return SecuringScreenResult(False, 2., 'remaining deck component fails')


def make_engine(instance):
    return make_feasibility_engine(instance, get_action_space(),
        config=FeasibilityConfig(extra={'global_screen':0}))


@pytest.mark.parametrize('indexed', [False, True])
def test_last_hold_place_is_removed_before_action_tokens(indexed):
    instance = tiny_instance(719, n_load=1, n_other=0, p40=0)
    inner = make_engine(instance)
    hold = next(a for a in inner.legal_actions() if not parse_slot(a.slot).on_deck)
    screen = DeferredPrefixScreen()
    wrapped = SecuringEngine(inner, screen=screen)
    assert not wrapped.is_legal(hold)
    assert wrapped.legal_actions() == []
    assert screen.terminal_calls > 0
    assert inner.depth == wrapped.depth == 0
    with pytest.raises(IllegalActionError):
        wrapped.push(hold)
    decoder = MaskedDecoder(wrapped, codec_for(wrapped),
                            MaskConfig(lookahead=0, index_arrays=indexed))
    assert decoder.allowed_token_ids() == ()
    assert decoder.status == 'dead_end'


def test_last_discharge_checks_untouched_deck_components():
    instance = tiny_instance(719, n_load=0, n_other=0, p40=0, layover=True)
    # Retain the required discharge only, so it leaves no through-box buffer.
    instance = instance.model_copy(update={'onboard': instance.onboard.model_copy(update={
        'placements': [p for p in instance.onboard.placements
                       if p.container_id in instance.discharge_list]})})
    inner = make_engine(instance)
    action = Discharge(instance.discharge_list[0])
    assert inner.is_legal(action)
    trial = inner.copy()
    trial.push(action)
    assert trial.is_complete()
    screen = DeferredPrefixScreen()
    wrapped = SecuringEngine(inner, screen=screen)
    assert not wrapped.is_legal(action)
    assert action not in wrapped.legal_discharges()
    assert screen.terminal_calls > 0
    assert inner.depth == 0


def test_nonfinal_place_keeps_prefix_exemption():
    instance = tiny_instance(719, n_load=2, n_other=0, p40=0)
    inner = make_engine(instance)
    screen = DeferredPrefixScreen()
    wrapped = SecuringEngine(inner, screen=screen)
    action = wrapped.legal_actions()[0]
    assert screen.terminal_calls == 0
    wrapped.push(action)
    assert inner.depth == 1
    assert not inner.is_complete()
