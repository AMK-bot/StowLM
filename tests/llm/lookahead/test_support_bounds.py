"""Production-copy support policy: isolated math plus actual dispatch contracts."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from llm.lookahead import LookaheadConfig,LookaheadEngine,lookahead_decoder_factory
from llm.lookahead.bounds import BoundResult,OptimisticCapacity
from llm.lookahead.support_bounds import JointCapacity,SupportCapacity
from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
from stowage.rules.catalogue import catalogue_snapshot
from tests.llm.lookahead.test_bounds import box,geometry,state
from tests.llm.securing.test_mask_filter import SelectiveScreen,codec_for
from llm.stowlm.mask import MaskConfig


def fixture(lengths,masses,*,heights=None,rooms=(5.8,),tiers=(82,84),plugs=(84,),
            tolerance=0,e1=19000.,limits=(60000.,60000.,60000.)):
    heights = heights or [2.4]*(len(lengths)-1)+[2.9]
    boxes = [box(f'b{i}',height,length=length,reef=i == len(lengths)-1)
             for i,(length,height) in enumerate(zip(lengths,heights))]
    for item,mass in zip(boxes,masses):
        item.gross_mass_kg = mass
    inst = geometry(boxes,[(2*j+1,True,list(tiers),room,list(plugs))
                          for j,room in enumerate(rooms)])
    labels = list(catalogue_snapshot().catalogue.weight_classes.parameters['labels'].value)
    inst.rule_overrides = {'expert.e2.weight_order':dict(medium_min_kg=12000.,
        heavy_min_kg=24000.,tolerance=tolerance,order_mode='weight_class',class_order=labels),
        'expert.e1.heavy_bottom':dict(heavy_threshold=e1)}
    for stack in inst.vessel.stacks:
        stack.max_mass_kg,stack.max_mass_20_kg,stack.max_mass_40_kg = limits
    caps = {stack.id:stack.max_height_m for stack in inst.vessel.stacks}
    return inst,dict(visibility_caps=caps,long_matching=False)


@pytest.mark.parametrize('masses',[(28000.,),(8000.,28000.)])
def test_upper_target_excludes_self_and_ineligible_short_support(masses):
    inst,kw = fixture([20]*len(masses),masses)
    assert OptimisticCapacity(inst,visibility_caps=kw['visibility_caps']).check(state(),native_contract=True).status == 'admit'
    assert JointCapacity(inst,**kw).check(state(),native_contract=True).status == 'admit'
    rejected = SupportCapacity(inst,**kw).check(state(),native_contract=True)
    assert rejected.status == 'reject' and rejected.reason == 'future_domain_empty'
    assert rejected.certificate['support_domain_entries_removed'] > 0


@pytest.mark.parametrize('lengths,masses,rooms',[
    ([20,20],[28000.,28000.],(5.8,)),
    ([40,40],[28000.,28000.],(5.8,5.8)),
    ([20,20],[18000.,28000.],(5.8,)),
])
def test_eligible_other_support_short_height_and_long_two_units_are_retained(lengths,masses,rooms):
    inst,kw = fixture(lengths,masses,rooms=rooms,tolerance=1,e1=30000.)
    result = SupportCapacity(inst,**kw).check(state(),native_contract=True)
    assert result.status == 'admit'


def test_long_cannot_supply_lower_units_for_twenty_foot_target():
    inst,kw = fixture([40,20],[28000.,28000.],rooms=(5.8,5.8))
    assert SupportCapacity(inst,**kw).check(state(),native_contract=True).status == 'reject'


def test_unknown_other_mass_rank_widens_eligibility():
    inst,kw = fixture([20,20],[28000.,28000.])
    cap = SupportCapacity(inst,**kw)
    inst.containers[0].gross_mass_kg = None
    cap.rank.pop('b0')
    assert cap.check(state(),native_contract=True).status == 'admit'


def test_same_class_kg_order_and_long_mass_shares_are_not_misread():
    inst,kw = fixture([40,40],[25000.,29000.],rooms=(5.8,5.8),limits=(30000.,30000.,30000.))
    result = SupportCapacity(inst,**kw).check(state({'b0':('ship','0020182')}),native_contract=True)
    assert result.status == 'admit'  # 12.5t +14.5t per column, same H class


def test_bottom_target_needs_no_other_support_and_no_tolerance_ratchet():
    inst,kw = fixture([20],[28000.],plugs=(82,))
    assert SupportCapacity(inst,**kw).check(state(),native_contract=True).status == 'admit'
    inst,kw = fixture([20,20,20],[8000.,18000.,28000.],heights=[2.4]*3,
                       rooms=(7.2,),tiers=(82,84,86),plugs=(86,),tolerance=1,e1=30000.)
    assert SupportCapacity(inst,**kw).check(state({'b0':('ship','0010182'),
        'b1':('ship','0010184')}),native_contract=True).status == 'reject'


def test_default_dispatch_support_ablation_and_explicit_override(monkeypatch):
    import llm.lookahead.mask_filter as mask
    import llm.lookahead.support_bounds as support
    instance = tiny_instance(761,n_load=2,n_other=0,p40=0)
    native = make_feasibility_engine(instance,get_action_space(),config=FeasibilityConfig(extra={'global_screen':0}))
    calls = []
    def constructor(inst,config,**kwargs):
        calls.append(kwargs)
        return NS(instance=inst,check=lambda *args,**kwargs:BoundResult('admit'))
    monkeypatch.setattr(support,'SupportCapacity',constructor)
    default = LookaheadEngine(native.copy())
    assert LookaheadConfig().support_conditioned is True
    assert LookaheadConfig(9,8,7,6).max_remaining_boxes == 9
    assert calls == [{'long_matching':False}]
    diagnostic = default.screen_diagnostics()['lookahead']
    assert diagnostic['capacity_policy'] == 'SimpleNamespace'
    assert diagnostic['config']['support_conditioned'] is True
    assert diagnostic['long_matching'] is False
    sentinel = NS(instance=instance)
    monkeypatch.setattr(mask,'OptimisticCapacity',lambda *args:sentinel)
    assert LookaheadEngine(native.copy(),config=LookaheadConfig(support_conditioned=False)).capacity is sentinel
    assert LookaheadEngine(native.copy(),capacity=sentinel).capacity is sentinel
    assert len(calls) == 1
    factory = lookahead_decoder_factory(securing_screen=SelectiveScreen())
    decoders = factory(instance,native.copy(),codec_for(native),MaskConfig(),2)
    assert decoders[0].engine.inner.capacity is decoders[1].engine.inner.capacity
    assert decoders[0].engine.legal_actions()
    assert calls[-1] == {'long_matching':False}


def test_promoted_policy_class_logic_matches_measured_prototype():
    """Compare policy AST, excluding documentation; imports/dispatch stay separate."""
    root = Path(__file__).resolve().parents[3]
    prototype = root/'experiments/mvp/lookahead/prototype/joint_bounds.py'
    if not prototype.exists():
        pytest.skip('local experiment provenance is not part of a packaged install')
    production = root/'src/llm/lookahead/support_bounds.py'
    def policies(path):
        tree = ast.parse(path.read_text())
        result = {}
        for node in tree.body:
            if isinstance(node,(ast.ClassDef,ast.FunctionDef)):
                for child in ast.walk(node):
                    body = getattr(child,'body',None)
                    if isinstance(body,list) and body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str):
                        body.pop(0)
                result[node.name] = ast.dump(node,include_attributes=False)
        return result
    assert policies(prototype) == policies(production)
