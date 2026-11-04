"""Rules reach real page, assignment and peeled feasibility tables unchanged."""
from __future__ import annotations

import hashlib
import importlib
import json
import multiprocessing
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS

import numpy as np
import pytest

from llm.instructions.schema import CloseBay, DgKeepAway, MaxTiers
from experiments.mvp.reader.constraints import compile_one_port_per_hatch
from experiments.mvp.reader.occupancy import apply_reader_constraint
from stowage.model import Instance, MovePlan
from stowage.model.fixtures import build_instance
from experiments.mvp.pipeline.binding.test_process_wait import (
    ResultFile, receive_result, stop_worker, subprocess_guard,
)


def fixture_instance():
    """Small physical fixture; permissions are real, no optimisation is stubbed."""
    raw = build_instance(with_dg_doc=True).model_dump()
    for box in raw['containers']:
        box.update(length_ft=20, height_m=2.591, iso_type='22G1',
                   gross_mass_kg=8000., tare_kg=2200., reefer=False,
                   reefer_setpoint_c=None, oog=None, special_stowage=[])
    for stack in raw['yard']['stacks']:
        stack['length_ft'] = 20
    return Instance.model_validate(raw)


def compiled_rule(instance, rule_type):
    if rule_type == 'one_port_per_hatch':
        covers = instance.vessel.hatch_covers
        ports = sorted({str(b.pod) for b in instance.containers})
        return compile_one_port_per_hatch(instance, {h.id: ports[i % len(ports)]
                                                    for i, h in enumerate(covers)})
    answer = {
        'close_bay': CloseBay(type='close_bay', bay=1),
        'max_tiers': MaxTiers(type='max_tiers', zone='deck', max_tiers=1),
        'dg_keep_away': DgKeepAway(type='dg_keep_away', imdg_class='3', min_bays=1,
                                 away_from={'kind': 'bay', 'bay': 1}),
    }[rule_type]
    return apply_reader_constraint(instance, answer)


def _forbidden(T, compiled):
    return np.array([[bool(compiled.forbids(box, slot)) for slot in T.slot_ids]
                     for box in T.box_ids], dtype=bool)


@pytest.mark.parametrize('rule_type', ['close_bay', 'max_tiers', 'dg_keep_away', 'one_port_per_hatch'])
@pytest.mark.parametrize('lane', ['page', 'lp', 'ladder'])
def test_every_rule_removes_real_lane_pairs_and_survives_peeling(rule_type, lane):
    import alns
    from experiments.mvp.pipeline.binding.hooks import binding_scope
    from engine.assignment.gap import Assignment
    from engine.assignment.pages_sight import Model as LPModel
    from engine.scout.pages_fast import Model as PageModel
    from engine.peel_ladder.loader import isolated

    instance = fixture_instance()
    compiled = compiled_rule(instance, rule_type)
    ordinary = alns.Problem(instance)
    forbidden = _forbidden(ordinary.engine.T, compiled)
    # A rule that already agrees with every physical static pair proves nothing.
    assert np.any(ordinary.engine.T.static & forbidden), (rule_type, lane)
    if lane == 'ladder':
        from stowage.rules.catalogue import rule_freeze_v1
        with rule_freeze_v1(enabled=False), binding_scope(instance, compiled) as audit:
            with isolated():
                import masks
                import alns as private_alns
                # L5 drops every permitted soft tier; the instruction is never peeled.
                with masks.peeled_masks(5):
                    problem = private_alns.Problem(instance)
                    T = problem.engine.T
                    assert not np.any(T.static & _forbidden(T, compiled))
    else:
        with binding_scope(instance, compiled) as audit:
            problem = alns.Problem(instance)
            model = (PageModel if lane == 'page' else LPModel)(problem)
            assert not np.any(model.static & _forbidden(model.T, compiled))
            if lane == 'lp':
                # Assignment builds x[b,s] only for pairs surviving the shared mask.
                solver = Assignment(model, motions=None)
                assert solver.pair
                assert all(not compiled.forbids(model.T.box_ids[b], model.T.slot_ids[s])
                           for b, s in solver.pair)
    assert audit['tables_bound'] >= 1
    assert audit['forbidden_pairs'] > 0
    # Scoped wrappers must not mutate a previously built table or a later call.
    assert np.any(ordinary.engine.T.static & forbidden)
    restored = alns.Problem(instance)
    assert np.array_equal(restored.engine.T.static, ordinary.engine.T.static)


def _worker_rollout(instance, output):
    """Top-level spawn target: no manual installation in the child's code."""
    import alns
    from experiments.mvp.pipeline.binding.hooks import binding_active
    problem = alns.Problem(instance)
    engine = problem.engine
    actions = []
    while not engine.is_complete():
        candidates = engine.legal_actions()
        if not candidates:
            output.put({'active': binding_active(), 'pid': os.getpid(), 'complete': False})
            return
        action = candidates[0]
        actions.append(action)
        engine.push(action)
    scored = problem.score(tuple(actions))
    output.put({'active': binding_active(), 'pid': os.getpid(), 'complete': scored is not None,
                'plan': scored.plan.model_dump(mode='json') if scored else None})


@subprocess_guard(timeout_s=60)
def test_spawn_worker_inherits_hook_and_returns_compliant_replay(tmp_path):
    from experiments.mvp.pipeline.binding.hooks import binding_scope, binding_active
    instance = fixture_instance()
    # Use dry cargo here so worker propagation is isolated from synthetic DG geometry.
    instance = instance.model_copy(update={'containers': [b.model_copy(update={'dg': None})
                                                         for b in instance.containers]})
    compiled = compiled_rule(instance, 'close_bay')
    context = multiprocessing.get_context('spawn')
    # A Queue's timeout covers its readiness poll, not a partially received
    # pickle frame. An atomic file has no feeder or framed receive to strand.
    output = ResultFile(tmp_path / 'worker-result.json')
    assert not binding_active()
    with binding_scope(instance, compiled) as audit:
        # pytest's importlib mode may collect this as ``binding.test_hooks``;
        # spawn requires the canonical importable target, as production workers do.
        target = importlib.import_module('experiments.mvp.pipeline.binding.test_hooks')._worker_rollout
        worker = context.Process(target=target, args=(instance, output))
        worker.start()
        try:
            message = receive_result(worker, output, timeout_s=45)
        finally:
            stop_worker(worker)  # Only the exact child created by this test.
    assert message['active'] and message['complete']
    assert message['pid'] != os.getpid()
    assert compiled.violations(MovePlan.model_validate(message['plan'])) == []
    events = audit['worker_events']
    assert any(e['event'] == 'worker_hook_active' and e['pid'] == message['pid'] for e in events)
    assert any(e['event'] == 'worker_table_bound' and e['pid'] == message['pid']
               and e['forbidden_pairs'] > 0 for e in events)
    assert not binding_active()


def test_pinned_engine_manifest_matches_every_source_file():
    root = Path(__file__).resolve().parents[2] / 'frontier' / 'engine'
    manifest = json.loads((root / 'source-manifest-0.13.0-review-fixes.json').read_text())
    mismatches = [name for name, digest in manifest['source_files'].items()
                  if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest]
    assert mismatches == []


def test_checked_observer_restores_late_import_alias_and_retains_verdict_on_callback_error(monkeypatch):
    import stowage.checker as checker
    from experiments.mvp.pipeline.binding.hooks import binding_scope
    instance = fixture_instance()
    report = NS(passed=True, count=NS(weighted_nonproductive=7.))
    def native_check(*args, **kwargs):
        return report
    monkeypatch.setattr(checker, 'check', native_check)
    late = ModuleType('_pipeline_test_late_checker_import')
    monkeypatch.setitem(sys.modules, late.__name__, late)
    callbacks = []
    def broken_callback(plan, verdict):
        callbacks.append(verdict)
        raise RuntimeError('consumer callback failed')
    with binding_scope(instance, on_checked=broken_callback) as audit:
        # This import occurs after wrapper installation, as lazy lane imports do.
        exec('from stowage.checker import check', late.__dict__)
        assert late.check is not native_check
        assert late.check(instance, object()) is report
        assert late.check(instance, object()) is report  # Equal cost is not improved.
    assert late.check is native_check and checker.check is native_check
    assert callbacks == [report]
    assert audit['checked_incumbents'] == 1
    assert audit['callback_errors'] == ['RuntimeError: consumer callback failed']
