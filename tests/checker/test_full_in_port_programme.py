"""D74 end-to-end replay wiring; algebra and physical bands have own tests."""
import runpy
from types import SimpleNamespace

import pytest

from stowage.model import PartReport, TankPair, Violation
from stowage.rules.catalogue import rule_freeze_v1
from stowage.checker.in_port_programme import ProgrammeParameters


def _equilibrium_metrics(ship):
    """Give each actual state, ballast endpoint and HEEL split distinct evidence."""
    masses = ship.tank_masses_kg
    i, phase, split = masses['state'], masses['phase'], masses['WB-P']
    return dict(draught_aft_m=4.+i*.01+phase*.1+split*.001,
        draught_fwd_m=3.+i*.01+phase*.2-split*.001,
        list_rad=(split-50.)*.0001+phase*.001)


def _rate_harness(monkeypatch, *, arrival_failure=None, changed_mass=0.,
                  window_failure=None, ideals=None, exact_failure=None,
                  departure_ideals=None, bands_by_phase=None):
    harness = runpy.run_path('tests/checker/test_full_in_port.py')['_harness']
    module,instance,plan,ships,_,_,_ = harness(monkeypatch)
    pair = TankPair(id='HEEL',port_tank='WB-P',starboard_tank='WB-S')
    instance.vessel.tank_pairs = [pair]
    center = next(tank for tank in instance.vessel.tanks if tank.adjustable).model_copy(
        update={'id':'CENTER'})
    instance.vessel.tanks.append(center)
    instance.onboard.tank_masses_kg = {'WB-P':50.,'WB-S':50.,'CENTER':0.}
    departure = ships[-1].model_copy(deep=True)
    departure.tank_masses_kg.update({'WB-P':50.,'WB-S':50.,'CENTER':changed_mass})
    ideals = ideals or [50.]*len(ships)
    departure_ideals = departure_ideals or ideals
    observations = []
    class Context:
        def __init__(self, phase='arrival'):
            self.instance,self.pair,self.departure_ship = instance,pair,departure
            self.phase = phase
        def for_phase(self, phase): return Context(phase)
        def transferred_ship(self, ship, *, phase):
            index = int(ship.tank_masses_kg['state'])
            split = (departure_ideals if phase=='departure' else ideals)[index]
            masses = dict(ship.tank_masses_kg, phase=float(phase=='departure'),
                          **{'WB-P':split,'WB-S':100.-split,
                             'CENTER':changed_mass if phase=='departure' else 0.})
            return ship.model_copy(update={'tank_masses_kg':masses}), {}
    class Evaluator:
        def __init__(self, instance, context, *, workers=1, warm_start=False,
                     heel_intervals=False):
            self.context,self.workers,self.warm_start = context,workers,warm_start
            self.intervals = heel_intervals
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def evaluate(self, states, *, stop_at_first_failure=False):
            reports=[]
            for ship in states:
                i,phase = int(ship.tank_masses_kg['state']),int(ship.tank_masses_kg['phase'])
                ideal = (departure_ideals if phase else ideals)[i]
                chosen = ship.tank_masses_kg['WB-P'] != ideal
                band = bands_by_phase[phase][i] if bands_by_phase else (20.,80.)
                observations.append((i,phase,self.context.phase,self.intervals,chosen))
                failed = ((not phase and i==arrival_failure)
                    or (self.intervals and (i,phase)==window_failure)
                    or (chosen and i==exact_failure))
                report = PartReport(name='in_port',
                    violations=[Violation(rule_id='port.stability.cargo_operations',severity='hard',
                        call_index=0,message='list_rad exceeds limit')] if failed else [],
                    metrics={'heel_lo_kg':band[0],'heel_hi_kg':band[1],
                             'heel_ideal_split_kg':ideal,
                             'heel_interval_feasible':float(not failed),
                             **_equilibrium_metrics(ship)},
                    margins={'list_rad':-.1 if failed else .1})
                reports.append(report)
                if failed and stop_at_first_failure: break
            return SimpleNamespace(reports=reports,state_times_s=tuple(.1 for _ in reports),
                measured_state_time_s=.1,measured_state_count=min(8,len(reports)))
        def evaluate_intervals(self, states, reports):
            return SimpleNamespace(reports=[report.model_copy(deep=True) for report in reports])
    parameters = ProgrammeParameters(1000.,3.6,1,1,3600.,0.,0.,1000.)
    monkeypatch.setattr(module,'prepare_context',lambda *_:Context())
    monkeypatch.setattr(module,'InPortEvaluator',Evaluator)
    monkeypatch.setattr(module,'check_strength',lambda instance,ship,**kw:
        PartReport(name='strength',metrics=_equilibrium_metrics(ship)))
    from stowage.checker import in_port_rates
    monkeypatch.setattr(in_port_rates,'programme_parameters',lambda _:parameters)
    return module,instance,plan,departure,observations


def test_d74_named_programme_survives_full_report_serialization(monkeypatch):
    from stowage.checker.in_port_report import InPortCheckReport
    module,instance,plan,departure,_ = _rate_harness(monkeypatch)
    with rule_freeze_v1(enabled=True):
        part=module.replay(instance,plan,departure)
    assert part.ran and not part.violations
    programme=part.model_dump()['ballast_programme']
    assert programme['rate_check_rule']=='port.ballast.transfer_rate'
    assert programme['changeover_k']==6
    assert programme['heel_path_feasible'] is True
    report=InPortCheckReport(passed=True,parts={'in_port':part})
    restored=InPortCheckReport.model_validate_json(report.model_dump_json())
    assert restored.model_dump()==report.model_dump()


def test_d74_reuses_one_evaluator_owner_across_all_programme_phases(monkeypatch):
    module,instance,plan,departure,_ = _rate_harness(monkeypatch,changed_mass=1000.)
    original=module.InPortEvaluator
    owners,views=[],[]
    class Shared(original):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            owners.append(self)
        def for_context(self,context,*,heel_intervals=False):
            views.append((context.phase,heel_intervals))
            return original(instance,context,workers=self.workers,
                warm_start=self.warm_start,heel_intervals=heel_intervals)
    monkeypatch.setattr(module,'InPortEvaluator',Shared)
    with rule_freeze_v1(enabled=True):
        part=module.replay(instance,plan,departure)
    assert part.ran and not part.violations
    assert len(owners)==1
    assert ('arrival',False) in views and ('departure',True) in views


def test_programme_attachment_can_replace_an_existing_block(monkeypatch):
    from stowage.checker.in_port_report import attach_programme
    module,instance,plan,departure,_ = _rate_harness(monkeypatch)
    with rule_freeze_v1(enabled=True):
        part=module.replay(instance,plan,departure)
    replaced=attach_programme(part,part.ballast_programme.model_dump())
    assert replaced.model_dump()==part.model_dump()


def test_t15_window_departure_with_worked_fsm_rejects_before_unchanged_k(monkeypatch):
    module,instance,plan,departure,observed = _rate_harness(monkeypatch,
        arrival_failure=4,changed_mass=1000.,window_failure=(2,1))
    with rule_freeze_v1(enabled=True):
        result=module.replay(instance,plan,departure)
    assert result.metrics['k']==4
    assert any(v.rule_id=='port.ballast.transfer_rate' and 'window.departure' in v.message
               and v.move_index==1 for v in result.violations)
    assert (2,0,'departure',True,False) in observed
    assert (2,1,'departure',True,False) in observed


def test_t13_ballast_delay_is_reported_without_rate_rejection(monkeypatch):
    module,instance,plan,departure,observed = _rate_harness(monkeypatch,changed_mass=3000.)
    with rule_freeze_v1(enabled=True):
        result=module.replay(instance,plan,departure)
    assert result.ran and not result.error and not result.violations
    assert result.ballast_programme.ballast_delay_h == pytest.approx(4.8/3600.)
    assert result.ballast_programme.heel_path_feasible is True
    assert not any(key.endswith('pump_time_h') for key in result.margins)
    for i in range(6):
        assert (i,0,'departure',True,False) in observed
        assert (i,1,'departure',True,False) in observed


def test_delayed_changeover_keeps_window_stability_hard(monkeypatch):
    module,instance,plan,departure,observed = _rate_harness(monkeypatch,
        changed_mass=3000.,window_failure=(0,1))
    with rule_freeze_v1(enabled=True):
        result=module.replay(instance,plan,departure)
    assert result.ran and result.error is None
    assert result.ballast_programme.ballast_delay_h > 0.
    assert any(v.rule_id=='port.ballast.transfer_rate' and 'window.departure' in v.message
               for v in result.violations)
    assert (0,1,'departure',True,False) in observed


@pytest.mark.parametrize('phase', ['window', 'heel_path'])
def test_programme_numerical_errors_keep_checker_rule_id(monkeypatch, phase):
    options = dict(changed_mass=3000.) if phase=='window' else dict(
        ideals=[50.,70.,30.,70.,30.,50.])
    module,instance,plan,departure,_ = _rate_harness(monkeypatch,**options)
    original = module.InPortEvaluator
    class Faulty(original):
        def evaluate(self, states, **kwargs):
            batch = super().evaluate(states, **kwargs)
            for ship,report in zip(states,batch.reports):
                i = int(ship.tank_masses_kg['state'])
                chosen = ship.tank_masses_kg['WB-P'] != (50.,70.,30.,70.,30.,50.)[i]
                if ((phase=='window' and self.intervals) or
                        (phase=='heel_path' and not self.intervals and chosen)):
                    report.ran = False
                    report.error = 'nonfinite programme endpoint'
                    report.violations = [Violation(rule_id='checker.internal_error',severity='hard',
                        call_index=0,message=report.error)]
            return batch
    monkeypatch.setattr(module,'InPortEvaluator',Faulty)
    with rule_freeze_v1(enabled=True):
        result=module.replay(instance,plan,departure)
    assert not result.ran and result.error
    assert any(v.rule_id=='checker.internal_error' for v in result.violations)
    assert not any(v.rule_id=='port.ballast.transfer_rate' for v in result.violations)


def test_d74_acceptance_sidecar_uses_pass2_and_window_count(monkeypatch):
    module,instance,plan,departure,_ = _rate_harness(monkeypatch,
        arrival_failure=4,changed_mass=1000.)
    telemetry={}
    with rule_freeze_v1(enabled=True):
        result=module.replay(instance,plan,departure,telemetry=telemetry)
    assert result.ran and not result.violations
    assert telemetry['t_hat_accept_source']=='pass2'
    assert telemetry['t_hat_accept_s']==.1
    assert telemetry['acceptance_state_count']==6
    assert result.ballast_programme.pump_window_lifts==4


def test_window_rechecks_other_endpoint_when_selected_split_is_unchanged(monkeypatch):
    module,instance,plan,departure,observed = _rate_harness(monkeypatch,
        arrival_failure=4,changed_mass=1000.,departure_ideals=[70.,70.,70.,70.,50.,50.])
    with rule_freeze_v1(enabled=True):
        result=module.replay(instance,plan,departure)
    assert result.ran and not result.violations
    for i in range(4):
        assert (i,1,'departure',False,True) in observed
        assert (i,0,'departure',False,True) not in observed


def test_window_intersects_both_endpoint_heel_bands(monkeypatch):
    module,instance,plan,departure,_ = _rate_harness(monkeypatch,
        arrival_failure=4,changed_mass=1000.,ideals=[30.]*6,
        departure_ideals=[70.]*6,
        bands_by_phase=([(20.,40.)]*6,[(60.,80.)]*6))
    with rule_freeze_v1(enabled=True):
        result=module.replay(instance,plan,departure)
    assert any(v.rule_id=='port.ballast.transfer_rate' and 'heel_step' in v.message
               for v in result.violations)
    assert result.ballast_programme.heel_path_feasible is False


def test_sampled_strength_receives_reported_chosen_heel_mass(monkeypatch):
    module,instance,plan,departure,_ = _rate_harness(monkeypatch,
        ideals=[50.,70.,30.,70.,30.,50.])
    seen=[]
    def strength(instance,ship,**kwargs):
        seen.append((int(ship.tank_masses_kg['state']),ship.tank_masses_kg['WB-P']))
        return PartReport(name='strength',metrics=_equilibrium_metrics(ship))
    monkeypatch.setattr(module,'check_strength',strength)
    with rule_freeze_v1(enabled=True):
        result=module.replay(instance,plan,departure)
    assert result.ran and not result.violations
    assert seen
    for i,mass in seen:
        assert mass==result.metrics[f'state:{i}.heel_path.port_mass_kg']


def test_prepass_internal_error_is_not_rate_infeasibility(monkeypatch):
    module,instance,plan,departure,_ = _rate_harness(monkeypatch,changed_mass=1000.)
    evaluator_type=module.InPortEvaluator
    class Faulty(evaluator_type):
        def evaluate(self,states,**kwargs):
            batch=super().evaluate(states,**kwargs)
            for ship,report in zip(states,batch.reports):
                if ship.tank_masses_kg['state']==1 and not ship.tank_masses_kg['phase']:
                    report.ran=False
                    report.error='nonfinite prepass root'
                    report.violations=[Violation(rule_id='checker.internal_error',severity='hard',
                        call_index=0,message=report.error)]
            return batch
    monkeypatch.setattr(module,'InPortEvaluator',Faulty)
    with rule_freeze_v1(enabled=True):
        result=module.replay(instance,plan,departure)
    assert not result.ran
    assert any(v.rule_id=='checker.internal_error' for v in result.violations)
    assert not any(v.rule_id=='port.ballast.transfer_rate' for v in result.violations)


@pytest.mark.parametrize('exact_failure',[None,1])
def test_t16_anticipated_path_reevaluates_each_deviation(monkeypatch,exact_failure):
    module,instance,plan,departure,observed = _rate_harness(monkeypatch,
        ideals=[50.,70.,30.,70.,30.,50.],exact_failure=exact_failure)
    telemetry={}
    with rule_freeze_v1(enabled=True):
        result=module.replay(instance,plan,departure,telemetry=telemetry)
    assert any(row[-1] for row in observed)
    assert result.ballast_programme.heel_max_step_t <= .001
    assert result.ballast_programme.heel_max_ideal_step_t == .04
    if exact_failure is None:
        assert result.ran and not result.violations
    else:
        assert any(v.rule_id=='port.ballast.transfer_rate' and 'heel_path' in v.message
                   for v in result.violations)
    assert 'programme_total_s' in telemetry
    assert not any('programme_total_s' in key for key in result.metrics)


@pytest.mark.parametrize('changed_mass,arrival_failure,ideals,departure_ideals', [
    (0., None, [50.,70.,30.,70.,30.,50.], None),
    (1000., 4, [50.]*6, [70.,70.,70.,70.,50.,50.]),
    (1000., 4, [50.,70.,30.,70.,30.,50.], [70.]*6),
])
def test_consistency_uses_exact_chosen_split_and_actual_window_endpoint(
        monkeypatch, changed_mass, arrival_failure, ideals, departure_ideals):
    module,instance,plan,departure,_ = _rate_harness(monkeypatch,
        changed_mass=changed_mass,arrival_failure=arrival_failure,
        ideals=ideals,departure_ideals=departure_ideals)
    # Every state is sampled, including changed chosen splits and both sides k.
    monkeypatch.setattr(module,'_sample_indices',lambda instance,moves,states,context:
        list(range(len(states))))
    with rule_freeze_v1(enabled=True):
        result=module.replay(instance,plan,departure)
    assert result.ran and not result.violations, result.model_dump()
    for i in range(6):
        phase='arrival' if i<result.metrics['k'] else 'departure'
        for axis in ('ta','tf','heel'):
            assert result.metrics[f'state:{i}.{phase}.strength.strength_vs_stability_delta_{axis}']==0.


def test_consistency_rejects_actual_sampled_equilibrium_disagreement(monkeypatch):
    module,instance,plan,departure,_ = _rate_harness(monkeypatch)
    def strength(instance,ship,**kwargs):
        metrics=_equilibrium_metrics(ship)
        metrics['draught_aft_m'] += .002
        return PartReport(name='strength',metrics=metrics)
    monkeypatch.setattr(module,'check_strength',strength)
    with rule_freeze_v1(enabled=True):
        result=module.replay(instance,plan,departure)
    assert not result.ran and result.error
    assert any(v.rule_id=='checker.internal_error' and 'disagree' in v.message
               for v in result.violations)


def test_consistency_is_gated_off_with_rule_freeze(monkeypatch):
    harness=runpy.run_path('tests/checker/test_full_in_port.py')['_harness']
    module,instance,plan,ships,_,_,_=harness(monkeypatch)
    monkeypatch.setattr(module,'check_strength',lambda *args,**kwargs:
        PartReport(name='strength',metrics={'draught_aft_m':999.}))
    with rule_freeze_v1(enabled=False):
        result=module.replay(instance,plan,ships[-1])
    assert result.ran and not result.violations
    assert not any('strength_vs_stability_delta_' in key for key in result.metrics)
