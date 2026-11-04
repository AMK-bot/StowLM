"""Candidate identity is part of the activated mask's physical probe cache."""
from __future__ import annotations

import numpy as np
import pytest

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.feasibility._port_screen import port_actions_allowed
from stowage.model.yard import YardStack
from stowage.rules.catalogue import rule_freeze_v1


@pytest.mark.parametrize("reverse", [False, True], ids=["accept-first", "reject-first"])
def test_port_column_cache_is_box_specific_for_heterogeneous_candidates(monkeypatch, reverse):
    """A candidate-sensitive shared probe must never reuse another box's result.

    The real engine, source preparation and port batching run unchanged. The
    narrow can_expand spy models a box-sensitive physical rule using actual
    distinct gross masses; this intentionally tests the cache contract without
    relying on which current physical rule happens to inspect that attribute.
    Both orders detect reuse of a successful and an unsuccessful probe.
    """
    with rule_freeze_v1(enabled=True):
        instance = tiny_instance(41, n_load=2, n_other=0, p40=0, max_tiers=5)
        light, heavy = instance.containers
        light.gross_mass_kg, heavy.gross_mass_kg = 9000, 18000
        instance.port.max_crane_height_tiers = 8
        instance.yard.stacks = [
            YardStack(key="A-001-01", length_ft=20, boxes=[light.id]),
            YardStack(key="A-002-01", length_ft=20, boxes=[heavy.id]),
        ]
        engine = make_feasibility_engine(instance, get_action_space("boxslot"),
                                         config=FeasibilityConfig(extra={"global_screen": 0}))
        tables, state = engine.T, engine._cur
        order = [heavy, light] if reverse else [light, heavy]
        boxes = np.array([tables.box_ids.index(box.id) for box in order])
        slots = np.full(2, tables.slot_ids.index("0010182"))
        probes = []

        def candidate_sensitive_probe(self, action, *, pending_load_targets=None):
            assert isinstance(action, Place)
            probes.append(action.box)
            return self.boxes[action.box].gross_mass_kg < 10000

        monkeypatch.setattr(Expander, "can_expand", candidate_sensitive_probe)
        expected = [box is light for box in order]
        assert port_actions_allowed(instance, tables, state, boxes, slots).tolist() == expected
        assert probes == [box.id for box in order]
        # Repeating the exact candidates may reuse their own cached outcomes.
        assert port_actions_allowed(instance, tables, state, boxes, slots).tolist() == expected
        assert probes == [box.id for box in order]
