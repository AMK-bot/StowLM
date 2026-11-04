"""9.3 prep: the instruction filter around the production StowLM mask (CPU, tokenizer only, no model, no network).

Replays the scalable solver's plan for a tiny instance through the production ``MaskedDecoder`` twice, plain and
over the instruction-filtered engine, and checks that exactly the forbidden placements are masked out (at action
and token level) while every other allowed action and token is unchanged.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from llm.instructions import CloseBay, apply
from llm.instructions.mask_filter import instruction_decoder_factory, wrap_engine
from llm.stowlm.format import load_tokenizer
from llm.stowlm.mask import MaskedDecoder, random_masked_plan
from llm.stowlm.planner import codec_for
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig, IllegalActionError, make_feasibility_engine
from stowage.io import load_instance

ROOT = Path(__file__).resolve().parents[3]
TINY = ROOT / "experiments/mvp/corpus/test/tiny/feeder_500/random_yard/seed-008000000.json"
TINY3 = ROOT / "experiments/mvp/corpus/test/tiny/feeder_500/simulated_yard/seed-008000003.json"
CACHE = ROOT / "experiments/mvp/instruct/cache"
D79 = FeasibilityConfig(extra={"global_screen": 0})


@pytest.fixture(scope="module")
def tokenizer():
    return load_tokenizer()


def setup(path, tokenizer):
    instance = load_instance(path)
    engine = make_feasibility_engine(instance, get_action_space(), config=D79)
    return instance, engine, codec_for(instance, tokenizer)


def admissible(engine, codec, tokens) -> bool:
    """Can the production mask write this whole token sequence from ``engine``'s state?"""
    dec = MaskedDecoder(engine.copy(), codec)
    for t in tokens:
        if t not in dec.allowed_token_ids():
            return False
        dec.advance(t)
    return True


def solver_actions(instance):
    plan = json.loads((CACHE / f"{instance.id}.baseline.json").read_text())["plan"]
    return [Place(a["box"], a["slot"]) for a in plan["actions"]]


def test_solver_plan_replay_masks_exactly_the_forbidden_slots(tokenizer):
    instance, engine, codec = setup(TINY, tokenizer)
    constrained = apply(instance, CloseBay(type="close_bay", bay=1))
    plain, wrapped = engine.copy(), wrap_engine(engine.copy(), constrained)
    masked_total = 0
    for step, action in enumerate(solver_actions(instance)):
        legal = set(plain.legal_actions())
        allowed = set(wrapped.legal_actions())
        forbidden = {a for a in legal if isinstance(a, Place) and constrained.forbids(a.box, a.slot)}
        assert allowed == legal - forbidden, step              # the rest is unchanged
        assert not any(a.slot[:3] == "001" for a in allowed if isinstance(a, Place))
        masked_total += len(forbidden)
        # Token level, through the production MaskedDecoder: forbidden actions can't be written, the rest can.
        for a in legal:
            assert admissible(wrapped, codec, codec.encode_action(a)) == (a not in forbidden), (step, a)
        first_plain = set(MaskedDecoder(plain.copy(), codec).allowed_token_ids())
        first_wrapped = set(MaskedDecoder(wrapped.copy(), codec).allowed_token_ids())
        assert first_wrapped <= first_plain
        # The solver's own action: forbidden ones are masked; replay continues on the real state either way.
        assert wrapped.is_legal(action) == (action not in forbidden)
        if action in forbidden:
            with pytest.raises(IllegalActionError):
                wrapped.copy().push(action)
        plain.push(action)
        wrapped.inner.push(action)
    assert masked_total > 0  # the solver's unconstrained plan did use bay 01, so the filter really bit


def test_masked_random_plans_never_use_a_closed_bay(tokenizer):
    instance, engine, codec = setup(TINY3, tokenizer)
    constrained = apply(instance, CloseBay(type="close_bay", bay=1))
    for seed in range(5):
        dec = random_masked_plan(wrap_engine(engine, constrained), codec, random.Random(seed))
        assert dec.status in ("complete", "dead_end")
        assert not [a for a in dec.actions if isinstance(a, Place) and a.slot[:3] == "001"]


def test_decoder_factory_matches_the_planner_seam(tokenizer):
    instance, engine, codec = setup(TINY3, tokenizer)
    constrained = apply(instance, CloseBay(type="close_bay", bay=1))
    decoders = instruction_decoder_factory(constrained)(instance, engine, codec, None, 3)
    assert len(decoders) == 3 and all(isinstance(d, MaskedDecoder) for d in decoders)
    assert len({id(d.engine.inner) for d in decoders}) == 3  # each sample owns its engine


def test_instruction_that_closes_everything_is_a_dead_end(tokenizer):
    instance, engine, codec = setup(TINY3, tokenizer)
    constrained = apply(instance, CloseBay(type="close_bay", bay=2))  # 40-ft bay 02 = both odd bays
    wrapped = wrap_engine(engine, constrained)
    assert not wrapped.legal_actions() and "instruction" in wrapped.dead_end().reason
    assert MaskedDecoder(wrapped.copy(), codec).allowed_token_ids() == ()


def test_wrong_instance_is_refused(tokenizer):
    instance, engine, _ = setup(TINY3, tokenizer)
    other = apply(load_instance(TINY), CloseBay(type="close_bay", bay=1))
    with pytest.raises(ValueError, match="different instances"):
        wrap_engine(engine, other)
