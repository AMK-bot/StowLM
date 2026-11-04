"""A real worker rebuilds and fully checks a known legal development witness.

The fixture is test-only cargo and ballast evidence from a completed public C6
development measurement. It is never a runtime planner start or benchmark input.
The child must reconstruct its moves through native Problem.score under the
inherited instruction mask; merely echoing the saved plan cannot satisfy this test.
"""
from __future__ import annotations

import hashlib
import faulthandler
import importlib
import json
import multiprocessing
import os
from pathlib import Path
import time
import traceback

import pytest

from experiments.mvp.pipeline.binding.admission import wait_for_capacity
from experiments.mvp.pipeline.binding.hooks import binding_scope
from experiments.mvp.reader.constraints import compile_one_port_per_hatch
from stowage.frozen import active_stubs, require_clean
from stowage.io import load_instance
from stowage.model import CheckReport, MovePlan
from experiments.mvp.pipeline.binding.test_process_wait import (
    ResultFile, receive_result, stop_worker, subprocess_guard,
)


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FIXTURE = HERE / 'fixtures/medium_one_port_per_hatch_worker.json'


def _checked_worker(instance, witness, output):
    """Spawn target intentionally contains no hook installation of its own."""
    stack_path = HERE / f'worker-verification-{os.getpid()}.stack.log'
    stack_log = stack_path.open('w')
    # Fatal diagnostics need no periodic watchdog whose cancellation can hang.
    faulthandler.enable(file=stack_log)
    try:
        import alns
        from stowage.actions import get_action_space
        from stowage.checker import check
        from experiments.mvp.pipeline.binding.hooks import binding_active, bound_forbidden_pairs
        from experiments.mvp.reader.constraints import compile_one_port_per_hatch
        begun = time.monotonic()
        assert binding_active(), 'spawn target did not inherit the binding scope'
        compiled = compile_one_port_per_hatch(instance, witness['cover_ports'])
        seed = MovePlan.model_validate(witness['plan'])
        space = get_action_space('boxslot')
        actions = tuple(space.from_plan(instance, seed))
        problem = alns.Problem(instance)
        table = problem.engine.T
        forbidden_pairs = bound_forbidden_pairs(problem.engine)
        assert forbidden_pairs > 0
        assert all(not table.static[b, s]
                   for b, box in enumerate(table.box_ids)
                   for s, slot in enumerate(table.slot_ids)
                   if compiled.forbids(box, slot))
        # The same original instance had a checked 109-lift no-rule plan. It is
        # otherwise legal measured evidence, but violates this confirmed rule.
        # Prove the worker's actual score gate rejects that concrete counterexample.
        baseline = MovePlan.model_validate(witness['baseline']['plan'])
        baseline_actions = tuple(space.from_plan(instance, baseline))
        assert compiled.violations(baseline)
        assert problem.score(baseline_actions) is None
        # This call performs the native move expansion/replay and objective score.
        # Its instruction-gated score method was installed by the spawn trampoline.
        scored = problem.score(actions)
        assert scored is not None
        assert scored.plan is not seed
        assert len(scored.actions) == len(actions) and len(actions) > 0
        reconstructed = scored.plan.model_copy(update={'ballast': seed.ballast})
        assert compiled.violations(reconstructed) == []
        report = check(instance, reconstructed)
        require_clean(instance, reconstructed, report, what='real worker binding regression')
        assert not active_stubs()
        output.put(dict(pid=os.getpid(), active=binding_active(), forbidden_pairs=forbidden_pairs,
                        stack_log=stack_path.name,
                        checked_baseline_rejected=True,
                        actions_scored=len(actions), native_objective=float(scored.objective),
                        plan=reconstructed.model_dump(mode='json'),
                        checker=report.model_dump(mode='json'), wall_s=time.monotonic()-begun))
    except BaseException:
        output.put(dict(pid=os.getpid(), error=traceback.format_exc()))
    finally:
        faulthandler.disable()
        stack_log.close()


@pytest.mark.slow  # Measured 11.2s native check, above the repository's 10s default policy.
@subprocess_guard(timeout_s=240)
def test_real_spawn_worker_rebuilds_hatch_bound_plan_and_passes_native_full_checker(tmp_path):
    witness = json.loads(FIXTURE.read_text())
    map_path = ROOT / witness['map']
    # Pin the exact public development input, without enumerating other map sets.
    assert hashlib.sha256(map_path.read_bytes()).hexdigest() == witness['input_sha256']
    admission = wait_for_capacity()
    assert admission['enabled_cores'] >= 8 and admission['load1'] <= 45
    instance = load_instance(map_path)
    require_clean(instance, what='real worker original development input')
    compiled = compile_one_port_per_hatch(instance, witness['cover_ports'])
    baseline = witness['baseline']
    assert baseline['checked_passed'] and baseline['engine_version'] == '0.13.0'
    assert baseline['lifts'] == 109.
    assert compiled.violations(MovePlan.model_validate(baseline['plan']))
    context = multiprocessing.get_context('spawn')
    output = ResultFile(tmp_path/'worker-result.json')
    target = importlib.import_module(
        'experiments.mvp.pipeline.binding.test_real_worker')._checked_worker
    with binding_scope(instance, compiled) as audit:
        worker = context.Process(target=target, args=(instance, witness, output))
        worker.start()
        try:
            message = receive_result(worker, output, timeout_s=180)
        finally:
            stop_worker(worker)
    output.close()
    assert 'error' not in message, message.get('error')
    assert message['active'] and message['pid'] != os.getpid()
    assert message['checked_baseline_rejected']
    assert message['forbidden_pairs'] > 0 and message['actions_scored'] > 0
    plan = MovePlan.model_validate(message['plan'])
    report = CheckReport.model_validate(message['checker'])
    require_clean(instance, plan, report, what='parent inspection of real worker checker result')
    assert not active_stubs() and report.passed and report.count is not None
    assert float(report.count.weighted_nonproductive) == witness['expected_lifts'] == 71.
    assert sorted(report.parts) == witness['expected_checker_parts']
    assert len(report.parts) == 23
    assert all(part.ran and not part.error for part in report.parts.values())
    assert not [v for v in report.violations if v.severity == 'hard']
    # Parent independently audits the reconstructed cargo against the same rule.
    assert compiled.violations(plan) == []
    child_events = [e for e in audit['worker_events'] if e.get('pid') == message['pid']]
    assert any(e['event'] == 'worker_hook_active' for e in child_events)
    assert any(e['event'] == 'worker_table_bound' and e['forbidden_pairs'] > 0
               for e in child_events)
    # Persist evidence only after every parent-side release/proof assertion.
    # This is test verification, never a runtime plan input or success cache.
    evidence = dict(schema_version='stowlm.binding-worker-verification/1', passed=True,
        verified_at=time.time(), parent_pid=os.getpid(), worker_pid=message['pid'],
        engine_version=baseline['engine_version'], map=witness['map'],
        input_sha256=witness['input_sha256'], fixture=str(FIXTURE.relative_to(ROOT)),
        fixture_sha256=hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        source_measurement=witness['source_measurement'], admission=admission,
        rule=witness['rule'], cover_ports=witness['cover_ports'],
        baseline_checked_passed=baseline['checked_passed'], baseline_lifts=baseline['lifts'],
        baseline_rejected_in_worker=message['checked_baseline_rejected'],
        worker_hook_active=message['active'], forbidden_pairs=message['forbidden_pairs'],
        actions_scored=message['actions_scored'], worker_wall_s=message['wall_s'],
        lifts=float(report.count.weighted_nonproductive), checker_parts=len(report.parts),
        instruction_violations=[], provenance_clean=True, stack_log=message['stack_log'],
        checker=message['checker'], plan=message['plan'], binding_audit=audit)
    temporary = HERE / f'.verification-worker-{os.getpid()}.json.tmp'
    try:
        temporary.write_text(json.dumps(evidence, sort_keys=True, indent=2)+'\n')
        os.replace(temporary, HERE / 'verification-worker.json')
    finally:
        temporary.unlink(missing_ok=True)
