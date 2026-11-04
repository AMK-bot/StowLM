"""Optional local accepted-plan replay through the real securing mask, CPU only."""
import json
from pathlib import Path

import pytest

from llm.securing import SecuringEngine
from llm.stowlm.format import ActionCodec
from llm.stowlm.mask import MaskConfig, MaskedDecoder
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import Instance,MovePlan
from types import SimpleNamespace


class CharacterTokenizer:
    def encode(self,text,**kwargs):
        return SimpleNamespace(ids=[ord(c) for c in text])

    def decode(self,ids,**kwargs):
        return ''.join(chr(i) for i in ids)


def test_accepted_solver_on_pilot_replays_under_real_securing_mask():
    root = Path(__file__).resolve().parents[3]
    directory = root/'experiments/mvp/solve47/securing/pilot/tiny-feeder_500-random_yard-00-on'
    if not (directory/'checker.json').exists():
        pytest.skip('local accepted ON pilot artifacts unavailable')
    report = json.loads((directory/'checker.json').read_text())
    assert report['passed'], 'fixture must be checker-accepted'
    instance = Instance.model_validate_json((directory/'instance.json').read_text())
    plan = MovePlan.model_validate_json((directory/'plan.json').read_text())
    space = get_action_space()
    engine = SecuringEngine(make_feasibility_engine(instance,space,
        config=FeasibilityConfig(extra={'global_screen':0})))
    codec = ActionCodec(CharacterTokenizer(),[c.id for c in instance.containers])
    actions = space.from_plan(instance,plan)
    decoder = MaskedDecoder(engine,codec,MaskConfig(lookahead=0))
    for token in codec.encode_plan(actions):
        assert token in decoder.allowed_token_ids(), (decoder.status,decoder.dead_end)
        decoder.advance(token)
    assert decoder.status == 'complete'
    assert decoder.actions == actions
