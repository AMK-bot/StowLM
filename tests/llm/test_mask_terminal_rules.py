"""Actual engine/codec witnesses for terminal screen attribution.

A lighter loaded box blocks a heavier final box by exact gross-mass order.
Completion screens are disabled deliberately so that this locally legal prefix
can reach its terminal state; the dead state still runs the real local screens.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.mask import MaskConfig, MaskedDecoder
from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.rules.catalogue import rule_freeze_v1


RULE = "expert.e2.weight_order"
LABEL = "rules active at the dead state"


def _weight_dead_state(tokenizer, *, index_arrays=True):
    instance = tiny_instance(37, n_load=2, n_other=0, p40=0)
    stack = next(s for s in instance.vessel.stacks if s.id == "00101D")
    selected = set(stack.cell_ids)
    instance.vessel = instance.vessel.model_copy(update={
        "cells": [c for c in instance.vessel.cells if c.id in selected],
        "stacks": [stack], "hatch_covers": [], "hull": None,
        "fixed_loads": [], "bridge": None})
    instance.containers[0].gross_mass_kg = 9000
    instance.containers[1].gross_mass_kg = 18000
    engine = make_feasibility_engine(instance, get_action_space("boxslot"), config=FeasibilityConfig(
        extra={"global_screen": 0, "completion_screens": 0}))
    codec = ActionCodec(tokenizer, [b.id for b in instance.containers])
    decoder = MaskedDecoder(engine, codec, MaskConfig(index_arrays=index_arrays))
    action = Place(instance.containers[0].id, stack.cell_ids[0])
    for token in codec.encode_action(action):
        decoder.advance(token)
    assert not decoder.engine.is_complete()
    return decoder


@pytest.fixture(scope="module")
def tokenizer():
    return load_tokenizer()


@pytest.mark.parametrize("index_arrays", [True, False], ids=["lazy", "reference"])
def test_terminal_dead_end_attributes_local_weight_screen(tokenizer, index_arrays):
    with rule_freeze_v1(enabled=True):
        decoder = _weight_dead_state(tokenizer, index_arrays=index_arrays)
        assert decoder.allowed_token_ids() == ()
        assert decoder.status == "dead_end"
        assert "no box fits any open slot" in decoder.dead_end.reason
        assert decoder.engine.screen_diagnostics()["rejected"][RULE] > 0
        assert decoder.dead_end.rule_ids == (RULE,)
        diagnostics = decoder.diagnostics()
        assert diagnostics["dead_end"]["rule_ids"] == [RULE]
        assert diagnostics["dead_end"]["rule_ids_label"] == LABEL
        assert diagnostics["dead_ends_by_rule"] == {RULE: 1}
        engine_screens = decoder.engine.screen_diagnostics()
        assert diagnostics["screens"]["rejected_count_label"] == engine_screens["rejected_count_label"]
        assert diagnostics["dead_end"]["rule_ids_label"] == engine_screens["dead_end_rule_ids_label"]


def test_legacy_terminal_attribution_preserves_engine_verdict(tokenizer):
    with rule_freeze_v1(enabled=False):
        decoder = _weight_dead_state(tokenizer)
        assert decoder.allowed_token_ids() == ()
        assert decoder.dead_end == decoder.engine.dead_end()
        assert decoder.dead_end.rule_ids == ()
        assert "rule_ids_label" not in decoder.diagnostics()["dead_end"]


def test_weight_rejection_count_is_once_per_box_slot_across_lazy_and_eager_queries(tokenizer):
    """One actual failed pair stays one after all query paths."""
    with rule_freeze_v1(enabled=True):
        decoder = _weight_dead_state(tokenizer)
        engine = decoder.engine
        heavy = engine.instance.containers[1].id
        assert engine.legal_slots_for_box(heavy) == ()
        first = engine.screen_diagnostics()["rejected"][RULE]
        assert first == 1  # one box and one supported open slot
        assert engine.legal_box_ids() == ()
        assert engine.legal_index().size == 0
        assert engine.screen_diagnostics()["rejected"][RULE] == first


@pytest.fixture
def mask_mlx_cpu(monkeypatch):
    mx = ModuleType("mlx.core")
    for name in ("array", "float32", "where", "take_along_axis", "argmax", "arange", "repeat"):
        setattr(mx, name, getattr(np, name))
    mx.eval = mx.async_eval = lambda *args: None
    mx.random = SimpleNamespace(key=lambda seed: seed, split=lambda key: (key + 1, key + 2))
    parent = ModuleType("mlx")
    parent.core = mx
    monkeypatch.setitem(sys.modules, "mlx", parent)
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    name = "_mask_terminal_numpy"
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[2] /
                                                "src/llm/stowlm/mask_mlx.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_actual_terminal_screen_rule_reaches_decode_trace(tokenizer, mask_mlx_cpu):
    class Model:
        def make_cache(self):
            return []

        def __call__(self, ids, cache):
            return np.zeros((*ids.shape, tokenizer.get_vocab_size()), dtype=np.float32)

    with rule_freeze_v1(enabled=True):
        decoder = _weight_dead_state(tokenizer)
        results, stats = mask_mlx_cpu.generate_masked(Model(), [1], [decoder], prefetch=False)
    result, = results
    assert result.status == "dead_end"
    terminal, = result.screen_trace
    assert terminal["dead_end"]["rule_ids"] == [RULE]
    assert terminal["dead_end"]["rule_ids_label"] == LABEL
    assert terminal["rule_ids_label"] == LABEL
    assert terminal["screens"]["rejected_count_label"] == decoder.engine.screen_diagnostics()["rejected_count_label"]
    assert stats.summary()["dead_ends_by_rule"] == {RULE: 1}
    assert stats.summary()["dead_ends_unattributed"] == 0


def test_mask_recovers_omitted_engine_terminal_rule_ids(tokenizer, monkeypatch):
    """A provider omitting optional terminal IDs must retain its real screen cause."""
    from dataclasses import replace

    with rule_freeze_v1(enabled=True):
        decoder = _weight_dead_state(tokenizer)
        engine_dead_end = decoder.engine.dead_end
        # Preserve the actual engine's legality and diagnostic computations;
        # emulate only a provider leaving DeadEnd's optional rule_ids empty.
        monkeypatch.setattr(decoder.engine, "dead_end", lambda: replace(engine_dead_end(), rule_ids=()))
        assert decoder.allowed_token_ids() == ()
        assert decoder.engine.screen_diagnostics()["rejected"][RULE] > 0
        assert decoder.dead_end.rule_ids == (RULE,)


@pytest.mark.parametrize("enabled", [False, True], ids=["legacy", "activated"])
def test_custom_decoder_fallback_labels_real_engine_rules(tokenizer, mask_mlx_cpu, enabled):
    with rule_freeze_v1(enabled=enabled):
        decoder = _weight_dead_state(tokenizer)
        assert decoder.allowed_token_ids() == ()
        proxy = SimpleNamespace(status=decoder.status, engine=decoder.engine, dead_end=decoder.dead_end)
        diagnostics = mask_mlx_cpu._decoder_diagnostics(proxy)
        assert diagnostics["dead_end"]["rule_ids"] == list(decoder.dead_end.rule_ids)
        assert (diagnostics["dead_end"].get("rule_ids_label") == LABEL) is enabled
        if enabled:
            assert diagnostics["screens"]["rejected_count_label"] == decoder.engine.screen_diagnostics()["rejected_count_label"]
