"""Actual targeted CP domains obey rules, without running numerical search."""
import sys
from types import ModuleType

import numpy as np
import pytest

from experiments.mvp.pipeline.binding.hooks import binding_scope
from experiments.mvp.pipeline.binding.test_hooks import compiled_rule, fixture_instance


@pytest.mark.parametrize('rule_type', ['close_bay', 'max_tiers', 'dg_keep_away', 'one_port_per_hatch'])
def test_targeted_plain_and_constructive_cp_models_exclude_forbidden_variables(rule_type):
    from engine.bounds.exact import bound
    from engine.bounds.exact.constructor import build_constructor_model
    from stowage.rules.catalogue import rule_freeze_v1

    instance = fixture_instance()
    constrained = compiled_rule(instance, rule_type)
    with rule_freeze_v1(enabled=False):
        ordinary = bound.build_model(instance, budget_s=1.)
        assert isinstance(ordinary, dict) and ordinary['chosen']
        assert any(constrained.forbids(ordinary['T'].box_ids[b], ordinary['T'].slot_ids[s])
                   for b, s in ordinary['chosen'])
        with binding_scope(instance, constrained):
            # Targeted construction first builds this model without supplying
            # problem.engine.T, then passes the resulting table to its richer
            # constructive model. Both rebuild optimistic DG unary rows.
            plain = bound.build_model(instance, budget_s=1.)
            constructive = build_constructor_model(instance, budget_s=1., tables=plain['T'])
            for context in (plain, constructive):
                assert isinstance(context, dict) and context['chosen']
                T = context['T']
                assert all(not constrained.forbids(T.box_ids[b], T.slot_ids[s])
                           for b, s in context['chosen'])


def test_direct_builder_and_unary_existing_and_late_aliases_restore(monkeypatch):
    import stowage.feasibility._engine as native
    import stowage.feasibility.tables as tables
    from engine.bounds.exact import bound, bound_joint, strengthening

    instance = fixture_instance()
    constrained = compiled_rule(instance, 'dg_keep_away')
    original_public, original_native = tables.build_tables, native.build_tables
    original_bound, original_joint = bound.build_tables, bound_joint.build_tables
    original_unary = strengthening.safe_unary_mask
    original_table = original_public(instance)
    original_static = original_table.static.copy()
    late = ModuleType('pipeline_binding_late_constructor_alias')
    monkeypatch.setitem(sys.modules, late.__name__, late)
    with binding_scope(instance, constrained):
        assert tables.build_tables is not original_public
        assert bound.build_tables is not original_bound
        assert bound_joint.build_tables is not original_joint
        late.builder = tables.build_tables
        late.unary = strengthening.safe_unary_mask
        table = late.builder(instance)
        static, _ = late.unary(instance, table)
        assert not any(static[b, s] and constrained.forbids(box, slot)
                       for b, box in enumerate(table.box_ids)
                       for s, slot in enumerate(table.slot_ids))
    assert tables.build_tables is original_public
    assert native.build_tables is original_native
    assert bound.build_tables is original_bound
    assert bound_joint.build_tables is original_joint
    assert late.builder is original_public
    assert late.unary is original_unary
    assert np.array_equal(original_table.static, original_static)


def test_native_wrapper_calling_public_builder_applies_mask_once(monkeypatch):
    import stowage.feasibility._engine as native
    import stowage.feasibility.tables as tables

    instance = fixture_instance()
    constrained = compiled_rule(instance, 'close_bay')
    original = native.build_tables
    def ambient(inst):
        return tables.build_tables(inst)
    monkeypatch.setattr(native, 'build_tables', ambient)
    with binding_scope(instance, constrained) as audit:
        table = native.build_tables(instance)
        assert getattr(table, '_pipeline_forbidden_pairs') > 0
        assert audit['tables_bound'] == 1
    assert native.build_tables is ambient
    monkeypatch.setattr(native, 'build_tables', original)
