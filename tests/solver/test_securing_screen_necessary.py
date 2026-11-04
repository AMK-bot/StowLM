"""Minimum-acceleration mask bounds, uncertainty, prefix and checker agreement."""
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from stowage.actions.space import Place
from stowage.checker import lashing,visibility
from stowage.model import PartReport,ShipState,Placement
from stowage.solver.securing_screen import DeckSecuringScreen,NecessaryDeckSecuringScreen

# Reuse the existing synthetic construction, not its tests or model equations.
_helpers = runpy.run_path(str(Path(__file__).with_name('test_securing_screen.py')))
synthetic_stack = _helpers['synthetic_stack']


@pytest.fixture(scope='module')
def template():
    return synthetic_stack([30000.]*4,40)


def expander_for(screen, placements):
    return SimpleNamespace(where={p.container_id:('ship',p.slot) for p in placements},
        stacks=screen.stacks,occ={c:p.container_id for p in placements for c in visibility.cells_of_slot(p.slot)}
        if hasattr(visibility,'cells_of_slot') else {},buffer=[])


def reference(screen,placements,gm,draft,monkeypatch):
    condition = SimpleNamespace(gm_m=gm,t_aft_m=draft,t_fwd_m=draft,geometry=screen.condition.geometry)
    monkeypatch.setattr(visibility,'_loading_condition',lambda *_:condition)
    return lashing.check_lashing(screen.instance,
        ShipState(vessel_id=screen.instance.vessel.id,placements=placements),call_index=0)


def test_necessary_bound_uses_permitted_draft_not_scantling(template):
    instance,_ = template
    hull = instance.vessel.hull.model_copy(update={'max_draught_m':instance.vessel.hull.scantling_draught_m+1.})
    instance = instance.model_copy(update={'vessel':instance.vessel.model_copy(update={'hull':hull})})
    screen = NecessaryDeckSecuringScreen(instance)
    assert screen.bounds_available
    assert screen.condition.gm_m == .15
    assert screen.condition.t_aft_m == hull.max_draught_m
    assert screen.condition.t_fwd_m == hull.max_draught_m


def test_necessary_rejection_implies_full_irs_failure_on_sampled_actual_conditions(template,monkeypatch):
    instance,placements = template
    screen = NecessaryDeckSecuringScreen(instance)
    result = screen.evaluate(placements)
    assert not result.passed, 'must exercise a genuine necessary rejection'
    for gm in (.15,.3,1.,screen.design_gm):
        for fraction in (.25,.75,1.):
            report = reference(screen,placements,gm,screen.condition.t_aft_m*fraction,monkeypatch)
            assert report.ran,report.error
            assert report.violations
    assert screen.metrics.get('rejections:force',0)>0
    assert not any(k.startswith('exceptions:') for k in screen.metrics)


def test_single_tier_prefix_exempt_but_terminal_judged(template):
    instance,placements = template
    box = instance.containers[0].model_copy(update={'height_m':12.})
    instance = instance.model_copy(update={'containers':[box],'load_list':[box.id]})
    screen = NecessaryDeckSecuringScreen(instance)
    action = Place(box.id,placements[0].slot)
    ex = expander_for(screen,[])
    assert screen.check_placement(ex,action).passed
    assert screen.metrics['prefix_single_chain_exemptions']==1
    assert not screen.evaluate([placements[0]]).passed


def test_unknown_bounds_and_geometry_never_prove_infeasibility(template):
    instance,placements = template
    vessel = instance.vessel.model_copy(update={'hull':None})
    screen = NecessaryDeckSecuringScreen(instance.model_copy(update={'vessel':vessel}))
    assert not screen.bounds_available
    assert screen.check_placement(SimpleNamespace(),Place(placements[0].container_id,placements[0].slot)).passed
    assert screen.check_state(SimpleNamespace()).passed
    assert screen.evaluate(placements).passed
    screen = NecessaryDeckSecuringScreen(instance)
    assert screen.evaluate([Placement(container_id=placements[0].container_id,slot='0990182')]).passed
    assert screen.metrics['fail_open:exception:KeyError']==1


@pytest.mark.parametrize('kind',['design','necessary'])
def test_injection_condition_guard_and_exception_diagnostics(template,monkeypatch,kind):
    instance,placements = template
    screen = (DeckSecuringScreen if kind=='design' else NecessaryDeckSecuringScreen)(instance)
    # Simulate a future checker refactor bypassing both injected dependencies.
    def refactored(instance,ship,*,call_index):
        return PartReport(name='lashing',metrics={'gm_m':screen.condition.gm_m})
    monkeypatch.setattr(lashing,'_check_lashing',refactored)
    verdict = screen.evaluate(placements)
    assert verdict.passed == (kind=='necessary')
    assert screen.metrics['exceptions:ValueError']==1
    screen.cache.clear()
    screen.condition.t_fwd_m += 1.
    verdict = screen.evaluate(placements)
    assert verdict.passed == (kind=='necessary')
    assert screen.metrics['exceptions:ValueError']==2


def test_rating_rejections_are_distinguished(template):
    instance,placements = template
    boxes = [b.model_copy(update={'gross_mass_kg':40000.}) for b in instance.containers]
    instance = instance.model_copy(update={'containers':boxes})
    screen = NecessaryDeckSecuringScreen(instance)
    assert not screen.evaluate(placements).passed
    assert screen.metrics['rejections:rating']==1


def test_pending_discharge_and_future_hold_access_defer_prefix_only(template):
    instance,placements = template
    # The stack fails at the minimum envelope; a pending discharge makes its
    # future contents uncertain, so a prefix must not be declared impossible.
    instance = instance.model_copy(update={'discharge_list':[placements[0].container_id]})
    screen = NecessaryDeckSecuringScreen(instance)
    ex = expander_for(screen,placements[:-1])
    assert screen.check_placement(ex,Place(placements[-1].container_id,placements[-1].slot)).passed
    assert screen.metrics['prefix_future_repair_exemptions']==1
    assert not screen.evaluate(placements).passed
    # Broad hold-access uncertainty deliberately ignores whether the remaining
    # box actually fits: weakening the necessary screen is safe.
    screen.instance = instance.model_copy(update={'discharge_list':[],'load_list':['pending-other-load']})
    ex.stacks = {**ex.stacks,'free-hold':SimpleNamespace(on_deck=False,cell_ids=['free-hold-cell'])}
    assert screen.check_placement(ex,Place(placements[-1].container_id,placements[-1].slot)).passed
    assert screen.metrics['prefix_future_repair_exemptions']==2


@pytest.mark.slow
def test_saved_plans_condition_grid_and_prefix_extensions(monkeypatch):
    # Two real synthetic solver plans: regress both opposite envelopes against
    # whole-ship IRS and test no rejected multi-tier prefix later recovers.
    load_case = _helpers['load_case']
    for case in _helpers['CASES'][2:]:
        instance,ship = load_case(case)
        necessary = NecessaryDeckSecuringScreen(instance)
        design = DeckSecuringScreen(instance)
        rows = sorted({int(p.slot[3:5]) for p in ship.placements if int(p.slot[-2:])>=80})
        for row in rows:
            placements = [p for p in ship.placements if int(p.slot[-2:])>=80 and int(p.slot[3:5])==row]
            low = necessary.evaluate(placements)
            high = design.evaluate(placements)
            for gm in (.15,.3,1.,design.design_gm):
                for f in (.25,.75,1.):
                    report = reference(necessary,placements,gm,necessary.condition.t_aft_m*f,monkeypatch)
                    assert report.ran,report.error
                    if not low.passed:
                        assert report.violations
                    if high.passed:
                        assert not report.violations
            rejected = False
            for tier in sorted({int(p.slot[-2:]) for p in placements}):
                prefix = [p for p in placements if int(p.slot[-2:])<=tier]
                result = necessary.evaluate(prefix,_prefix=True)
                if rejected:
                    assert not result.passed
                rejected |= not result.passed
        assert not any(k.startswith('exceptions:') for k in necessary.metrics)
        assert not any(k.startswith('exceptions:') for k in design.metrics)


def test_mixed_support_inner_single_tier_chain_defers_whole_prefix():
    instance,placements = synthetic_stack([30000.,30000.],40)
    from stowage.model.fixtures import build_instance
    left = instance.containers[0].model_copy(update={'length_ft':20,'height_m':12.})
    right = left.model_copy(update={'id':build_instance().containers[2].id})
    instance = instance.model_copy(update={'containers':[left,right,instance.containers[1]],
                                         'load_list':[left.id,right.id,instance.containers[1].id]})
    placements = [Placement(container_id=left.id,slot='0010182'),
                  Placement(container_id=right.id,slot='0030182'),placements[1]]
    screen = NecessaryDeckSecuringScreen(instance)
    ex = expander_for(screen,placements[:-1])
    action = Place(placements[-1].container_id,placements[-1].slot)
    assert screen.check_placement(ex,action).passed
    assert screen.metrics['prefix_single_chain_exemptions']==1
    # Both physical columns have two occupied tiers, but each 20-foot box's
    # inner end-frame is still a one-tier chain under the long box.
    assert not screen.check_state(expander_for(screen,placements)).passed
    assert screen.metrics['prefix_single_chain_exemptions']==1
