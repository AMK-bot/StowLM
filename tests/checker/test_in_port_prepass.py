"""D74 chronological pass-1 chunks, identity, measurements and deadline."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from stowage.model import PartReport, Violation


def _report(index, failures=(), numerical=()):
    return PartReport(name='in_port', metrics={'state_index': float(index)},
        ran=index not in numerical, error='numerical failure' if index in numerical else None,
        violations=[Violation(rule_id='checker.internal_error' if index in numerical else
            'port.stability.cargo_operations',severity='hard',call_index=0,message=f'failure {index}')]
            if index in failures or index in numerical else [])


class FakeEvaluator:
    def __init__(self, workers=1, *, failures=(), numerical=(), durations=None):
        self.workers, self.warm_start = workers, False
        self.failures, self.numerical = failures, numerical
        self.durations = durations or {}
        self.calls, self.source_reports = [], []
    def evaluate(self, ships, *, stop_at_first_failure=False):
        self.calls.append((tuple(ships), stop_at_first_failure))
        reports = []
        for index in ships:
            report = _report(index, self.failures, self.numerical)
            reports.append(report)
            if stop_at_first_failure and (not report.ran or report.error or report.violations):
                break
        self.source_reports.extend(reports)
        return SimpleNamespace(enabled=True, reports=tuple(reports),
            state_times_s=tuple(self.durations.get(int(report.metrics['state_index']), .01) for report in reports))


def test_chronological_chunks_stop_at_smallest_failure_and_match_serial():
    from stowage.checker.in_port_prepass import arrival_prepass
    serial, parallel = FakeEvaluator(1, failures={11,14}), FakeEvaluator(3, failures={11,14})
    expected = arrival_prepass(serial, list(range(30)))
    result = arrival_prepass(parallel, list(range(30)))
    assert result.k_arr == result.failure_index == 11
    assert [report.model_dump_json() for report in result.reports] == [
        report.model_dump_json() for report in expected.reports]
    assert parallel.calls == [(tuple(range(12)), True)]
    assert serial.calls == [(tuple(range(i,i+4)),True) for i in (0,4,8)]
    assert result.total_states == 30
    assert len(result.reports) == 12


def test_no_failure_checks_all_states_and_median_only_first_eight():
    from stowage.checker.in_port_prepass import arrival_prepass
    durations = {index: index*.01+.01 if index < 8 else 999. for index in range(21)}
    evaluator = FakeEvaluator(2,durations=durations)
    result = arrival_prepass(evaluator,list(range(21)))
    assert result.k_arr == 21 and result.failure_index is None
    assert result.total_states == len(result.reports) == 21
    assert [len(ships) for ships,_ in evaluator.calls] == [8,8,5]
    assert result.measured_state_time_s == pytest.approx(.045)
    assert result.measured_state_count == 8
    assert result.state_times_s == tuple(durations.values())


def test_numerical_failure_is_retained_as_failed_state():
    from stowage.checker.in_port_prepass import arrival_prepass
    result = arrival_prepass(FakeEvaluator(2,numerical={2}),list(range(20)))
    assert result.k_arr == result.failure_index == 2
    assert not result.reports[-1].ran
    assert result.reports[-1].violations[0].rule_id == 'checker.internal_error'
    assert result.measured_state_count == 3


def test_results_are_immutable_and_reports_are_independent_snapshots():
    from stowage.checker.in_port_prepass import arrival_prepass
    evaluator = FakeEvaluator()
    result = arrival_prepass(evaluator,[0,1])
    evaluator.source_reports[0].metrics['state_index'] = 999.
    result.reports[1].metrics['state_index'] = 999.
    assert [report.metrics['state_index'] for report in result.reports] == [0.,1.]
    with pytest.raises(FrozenInstanceError): result.failure_index = 1
    assert isinstance(result.state_times_s,tuple)


def test_early_exit_preserves_smallest_index_despite_out_of_order_worker_completion(monkeypatch):
    from stowage.checker import in_port_evaluator as physics
    from stowage.checker.in_port_prepass import arrival_prepass
    from stowage.model.fixtures import build_instance
    completed = []
    class Future:
        value = None
        def __init__(self,pool,function,ship): self.pool,self.function,self.ship=pool,function,ship
        def result(self):
            for future in sorted(self.pool.pending,key=lambda f:f.ship,reverse=True):
                if future.value is None:
                    completed.append(future.ship)
                    future.value = future.function(future.ship)
            return self.value
        def cancel(self): return False
    class Pool:
        def __init__(self,**kwargs): self.pending=[]
        def submit(self,function,ship):
            future=Future(self,function,ship)
            self.pending.append(future)
            return future
        def shutdown(self,**kwargs): pass
    monkeypatch.setenv('OPENBLAS_NUM_THREADS','1')
    monkeypatch.setenv('OMP_NUM_THREADS','1')
    monkeypatch.setattr(physics,'ProcessPoolExecutor',Pool)
    monkeypatch.setattr(physics,'_worker_check',lambda ship:(_report(ship,failures={5,6}),.01))
    with physics.InPortEvaluator(build_instance(),SimpleNamespace(),workers=2,warm_start=False) as evaluator:
        result=arrival_prepass(evaluator,list(range(30)))
    assert completed[:2] == [1,0]
    assert result.k_arr == result.failure_index == 5
    assert [report.metrics['state_index'] for report in result.reports] == list(range(6))
    assert all(index < 8 for index in completed)


def test_calibrated_deadline_uses_first_eight_worker_times_and_actual_workers(monkeypatch):
    from stowage.checker import in_port_prepass as module
    clock=[0.]
    evaluator=FakeEvaluator(2,durations={i:.5 for i in range(100)})
    evaluate=evaluator.evaluate
    def advance(*args,**kwargs):
        result=evaluate(*args,**kwargs)
        clock[0]+=10.
        return result
    evaluator.evaluate=advance
    monkeypatch.setattr(module,'monotonic',lambda:clock[0])
    with pytest.raises(module.InPortPrepassTimeout) as caught:
        module.arrival_prepass(evaluator,list(range(100)),calibrated_deadline=True)
    error=caught.value
    assert error.cause == 'prepass_timeout'
    assert error.deadline_s == pytest.approx(50.)  # 2*.5*100/2 > 30s floor
    assert error.partial.measured_state_time_s == .5
    assert len(evaluator.calls) == 5
    assert len(error.partial.reports) == 40


def test_explicit_deadline_and_incomplete_output_fail_closed(monkeypatch):
    from stowage.checker import in_port_prepass as module
    times=iter([0.,0.,4.])
    monkeypatch.setattr(module,'monotonic',lambda:next(times))
    with pytest.raises(module.InPortPrepassTimeout):
        module.arrival_prepass(FakeEvaluator(),list(range(20)),deadline_s=3.)
    monkeypatch.setattr(module,'monotonic',lambda:0.)
    evaluator=FakeEvaluator()
    evaluator.evaluate=lambda *args,**kwargs:SimpleNamespace(enabled=True,reports=(),state_times_s=())
    with pytest.raises(RuntimeError,match='incomplete'):
        module.arrival_prepass(evaluator,[0])


def test_empty_input_and_warm_or_worker_mismatch_validation():
    from stowage.checker.in_port_prepass import arrival_prepass
    result=arrival_prepass(FakeEvaluator(),[])
    assert result.k_arr == 0 and result.reports == () and result.measured_state_count == 0
    evaluator=FakeEvaluator(2)
    evaluator.warm_start=True
    with pytest.raises(ValueError,match='cold'): arrival_prepass(evaluator,[0])
    evaluator.warm_start=False
    with pytest.raises(ValueError,match='worker'): arrival_prepass(evaluator,[0],workers=4)


def test_incomplete_timeout_cannot_claim_all_states_passed(monkeypatch):
    from stowage.checker import in_port_prepass as module
    times=iter((0.,0.,3.))
    monkeypatch.setattr(module,'monotonic',lambda:next(times))
    with pytest.raises(module.InPortPrepassTimeout) as caught:
        module.arrival_prepass(FakeEvaluator(),list(range(20)),deadline_s=2.)
    assert not caught.value.partial.complete
    assert caught.value.partial.k_arr is None
    assert caught.value.partial.evaluated_states == 4


def test_callback_and_legacy_result_supported_without_inventing_timings():
    from stowage.checker.in_port_prepass import arrival_prepass
    def callback(states, *, stop_at_first_failure):
        assert stop_at_first_failure
        return SimpleNamespace(reports=tuple(_report(index) for index in states))
    result=arrival_prepass(callback,range(3))
    assert result.complete and result.k_arr == result.n == 3
    assert result.state_times_s == () and result.measured_state_count == 0
    with pytest.raises(ValueError,match='measured worker'):
        arrival_prepass(callback,range(3),calibrated_deadline=True)


@pytest.mark.parametrize('duration',[float('nan'),float('inf'),-1.])
def test_invalid_worker_duration_is_not_used_for_a_budget(duration):
    from stowage.checker.in_port_prepass import arrival_prepass
    evaluator=FakeEvaluator(durations={0:duration})
    with pytest.raises(RuntimeError,match='durations'):
        arrival_prepass(evaluator,[0])


def test_disabled_or_nonfinite_report_cannot_establish_a_prefix():
    from stowage.checker.in_port_prepass import arrival_prepass
    evaluator=FakeEvaluator()
    evaluator.evaluate=lambda *args,**kwargs:SimpleNamespace(enabled=False,reports=())
    with pytest.raises(RuntimeError,match='disabled'):
        arrival_prepass(evaluator,[0])
    evaluator.evaluate=lambda *args,**kwargs:SimpleNamespace(reports=(
        PartReport(name='in_port',metrics={'gm_m':float('nan')}),))
    with pytest.raises(RuntimeError,match='nonfinite'):
        arrival_prepass(evaluator,[0])
