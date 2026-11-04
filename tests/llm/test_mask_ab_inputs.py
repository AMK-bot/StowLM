"""Archived GPU A/B inputs at the mask/move boundary, without a model or GPU.

All original fields, including the hull and fixed background, are retained.
Their versioned background audits are stale: these are physical action replay
tests, expressly not full-checker qualification or GPU-token-sequence replays.
The independent static/full-checker rejection is recorded in the mask fix log;
no hard checker guard is filtered, disabled, or reinterpreted here.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.mask import MaskConfig, MaskedDecoder
from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import Instance
from stowage.moves import apply_move, initial_state
from stowage.rules.catalogue import rule_freeze_v1


ROOT = Path(__file__).resolve().parents[2]
SPACE = get_action_space("boxslot")
NO_C2 = FeasibilityConfig(extra={"global_screen": 0})


def _agreement_module():
    spec = importlib.util.spec_from_file_location("ab_inputs_mask_agreement",
        ROOT / "tests/llm/test_mask_rule_freeze_agreement.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AGREEMENT = _agreement_module()


@pytest.fixture(scope="module")
def tokenizer():
    return load_tokenizer()


@pytest.mark.slow
@pytest.mark.parametrize("case_id", [120380002, 120380004, 120380005])
def test_original_ab_input_complete_mask_move_agreement(case_id, tokenizer, record_property):
    capture = json.loads((ROOT / f"tests/actions/fixtures/ab-clearance-{case_id}.json").read_text())
    source = ROOT / capture["source"]
    if not source.exists():
        pytest.skip(f"original A/B input unavailable: {source}")
    raw = source.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == capture["source_sha256"]
    counts = Counter(prefixes=1, offered_actions=0, expanded_moves=0, physical_false_accepts=0)
    with rule_freeze_v1(enabled=True):
        instance = Instance.model_validate_json(raw)
        assert instance.id == f"v3.simulated_yard.small.{case_id}"
        assert instance.vessel.hull and instance.vessel.fixed_loads
        original = instance.model_dump_json()
        expected = tuple(make_feasibility_engine(instance, SPACE, config=NO_C2).legal_actions())
        codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
        decoder = MaskedDecoder(make_feasibility_engine(instance, SPACE, config=NO_C2), codec,
                                MaskConfig(lookahead=0))
        # Exact equality at every branch proves every offered target is tested.
        AGREEMENT._assert_complete_token_domain(decoder, codec, expected)
        rejected = SPACE.decode(capture["action"])
        assert rejected not in expected
        ex, world = Expander(instance), initial_state(instance)
        assert not ex.can_expand(rejected)
        with pytest.raises(ValueError, match="no legal yard position to clear crossing stacks"):
            ex.copy().step(rejected)
        for action in expected:
            trial, state = ex.copy(), world
            assert trial.can_expand(action), (instance.id, action)
            moves = trial.step(action)
            for move in moves:
                state = apply_move(instance, state, move)
            assert state == trial.world_state()
            counts["offered_actions"] += 1
            counts["expanded_moves"] += len(moves)
        assert instance.model_dump_json() == original, "the archived input must remain unmodified"
    assert counts["offered_actions"] > 0
    record_property("original_ab_mask_move_agreement", dict(counts))
    print(instance.id, dict(counts))
