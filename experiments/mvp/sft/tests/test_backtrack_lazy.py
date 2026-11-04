"""rev-v4-h P2 regression (from experiments/review/v4/repro_lazy_backtrack.py): with the lazy box-head trie, banning
one placement must not remove the same box's other legal slot."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace as NS


def test_banning_one_slot_keeps_the_box_head(monkeypatch):
    from llm.stowlm.mask import MaskConfig, MaskedDecoder
    from stowage.actions.space import Place
    from stowage.rules.catalogue import rule_freeze_v1

    if "llm.stowlm.backtrack" not in sys.modules:  # no MLX needed for the filter
        fake = ModuleType("llm.stowlm.mask_mlx")
        fake.SequenceResult, fake.prefill = object, (lambda *a: None)
        monkeypatch.setitem(sys.modules, "llm.stowlm.mask_mlx", fake)
    from llm.stowlm.backtrack import ForbiddingDecoder

    class Engine:
        instance = NS(stage="A", discharge_list=[])
        index_slots = ("slot-a", "slot-b")
        depth = 0
        def is_complete(self): return False
        def actions(self): return []
        def legal_box_ids(self): return ("box-a",)
        def legal_slots_for_box(self, box): return self.index_slots

    class Cache:
        codec = NS(end_plan_tokens=(9,))
        def encode(self, action):
            return ((1, 2, 3, 4 if action.slot == "slot-a" else 5), 3)

    with rule_freeze_v1():
        inner = MaskedDecoder(Engine(), Cache.codec, MaskConfig(lookahead=0), token_cache=Cache())
        d = ForbiddingDecoder(inner)
        assert d.allowed_token_ids() == (1,) and inner._stack[-1].lazy
        d.forbidden[()] = {Place("box-a", "slot-a")}
        assert d.allowed_token_ids() == (1,)  # slot-b is still legal under the shared head
        for t in (1, 2, 3):
            d.advance(t)
        assert d.allowed_token_ids() == (5,)  # at the slot: only the banned slot-a token is removed
        d.forbidden[()] = {Place("box-a", "slot-a"), Place("box-a", "slot-b")}
        d2 = ForbiddingDecoder(MaskedDecoder(Engine(), Cache.codec, MaskConfig(lookahead=0), token_cache=Cache()))
        d2.forbidden[()] = {Place("box-a", "slot-a"), Place("box-a", "slot-b")}
        assert d2.allowed_token_ids() == ()  # every action banned: the state is exhausted
