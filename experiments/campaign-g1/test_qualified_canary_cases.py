"""Qualified migration witnesses never inherit the placeholder fixture ship."""
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def module():
    spec = importlib.util.spec_from_file_location('tested_qualified_cases', HERE/'qualified_canary_cases.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_required_accept_boundaries_and_d69_direction():
    m = module()
    for name in ('e6_soft_heavy_on_deck', 'kg_equal_mass', 'oog_empty_killed_slots', 'yard_crossing_at_h'):
        assert m.CASES[name]['expectation'] == 'accept'
    assert m.CASES['yard_crossing_no_free_row']['expectation'] == 'deny'
    assert m.CASES['inport_list_failure']['expected_rule'] == 'port.stability.cargo_operations'


@pytest.fixture
def crossing_structure(monkeypatch):
    """Metadata-only branch probe; bypassed qualification never produces evidence."""
    from stowage.model.fixtures import build_instance
    from stowage.model import MovePlan
    from stowage.ports import load_port
    from stowage.rules.catalogue import rule_freeze_v1
    m=module();instance=build_instance()
    monkeypatch.setattr(m,'acquire_baseline',lambda *args:(instance,{'metadata_only':True}))
    def member(value,actions,**kwargs):
        from stowage.model.moves import Load
        sources={box:stack.key+'-01' for stack in value.yard.stacks for box in stack.boxes}
        plan=kwargs.get('plan') or MovePlan(id='metadata-only',instance_id=value.id,call_index=value.call_index,
            moves=[Load(call_index=value.call_index,container_id=a.box,
                        src=sources[a.box],dst=a.slot) for a in actions])
        return dict(input=value.model_dump(mode='json'),plan=plan.model_dump(mode='json'),
                    required_metrics=kwargs.get('required_metrics',{}))
    monkeypatch.setattr(m,'_member',member)
    with rule_freeze_v1(enabled=True):
        instance.port=load_port('laem_chabang_like')
        yield m,instance


@pytest.mark.parametrize('name',['yard_crossing_at_h','yard_crossing_no_free_row'])
def test_a8_yard_pairs_block_at_real_yard_height_five_not_quay_h_eight(crossing_structure,name):
    m,instance=crossing_structure
    pair=m.build_pair(name)
    assert pair['status']=='ready',pair
    defect=pair['defect']['input'];clean=pair['baseline']['input']
    assert defect['port']['max_crane_height_tiers']==8
    assert defect['port']['yard_max_tiers']==5
    near=defect['yard']['stacks'][1]
    assert len(near['boxes'])==5
    assert len(clean['yard']['stacks'][1]['boxes'])==4
    if name=='yard_crossing_at_h':
        assert pair['defect']['required_metrics']=={'relocations':1}
    else:
        assert all(len(stack['boxes'])==5 for stack in defect['yard']['stacks'][2:])


def test_a8_ship_crossing_keeps_quay_h_not_yard_height(crossing_structure):
    m,instance=crossing_structure
    from stowage.conventions import format_slot
    # Extend this structural fixture only; no checker, mask or qualification is claimed.
    by_id={cell.id:cell for cell in instance.vessel.cells}
    for stack in instance.vessel.stacks:
        if not stack.on_deck:continue
        bottom=by_id[stack.cell_ids[0]]
        for level in range(2,8):
            cid=format_slot(stack.bay,stack.row,82+2*level)
            instance.vessel.cells.append(bottom.model_copy(update={'id':cid,'vcg_base_m':bottom.vcg_base_m+2.591*level}))
            stack.cell_ids.append(cid)
    pair=m.build_pair('ship_crossing_at_h')
    assert pair['status']=='ready',pair
    assert len(pair['defect']['input']['onboard']['placements'])==8
    assert len(pair['baseline']['input']['onboard']['placements'])==7


@pytest.mark.parametrize('name,target',[
    ('ship_crossing_at_h','port.e3.quay_crossing'),
    ('yard_crossing_at_h','port.e3.yard_crossing'),
    ('yard_crossing_no_free_row','port.e3.yard_crossing'),
    ('yard_self_blocking','port.e3.quay_crossing'),
])
def test_crossing_targets_use_emitted_canonical_ids(name,target):
    assert module().CASES[name]['expected_rule']==target


def test_every_exercise_declares_independent_mask_and_checker_expectations():
    m=module()
    refusal_cases={'ship_crossing_at_h','yard_crossing_no_free_row','yard_self_blocking',
                   'class7_laem_pol','class7_later_pod','unflagged_class1_laem','flagged_nonclass1'}
    for name,spec in m.CASES.items():
        expected=spec.get('expected_rules',[spec['expected_rule']]) if spec['expectation']=='deny' else []
        assert spec['expected_mask_reasons']==expected
        assert spec['expected_secondary_rules']==(['objective.all_loaded'] if name in refusal_cases else [])
        assert not set(spec['expected_mask_reasons'])&set(spec['expected_secondary_rules'])


@pytest.mark.parametrize('name',['ship_crossing_at_h','yard_self_blocking'])
def test_crossing_pairs_use_empty_boxes_without_changing_physics(crossing_structure,name):
    """Builder regression only; physical checker qualification awaits D74/D75."""
    from stowage.conventions import format_slot
    from stowage.ports import load_port
    from stowage.model import PortCall
    m,instance=crossing_structure
    instance.port=load_port('small_feeder')
    instance.yard.yard_id=instance.port.yard.id
    instance.rotation.calls[instance.call_index]=PortCall(index=instance.call_index,
        locode=instance.port.locode,archetype=instance.port.archetype)
    instance.rotation.calls[instance.call_index+1]=PortCall(index=instance.call_index+1,
        locode='SGSIN',archetype='singapore_like')
    # This contract-only hull has no voyage policy; no numerical claim is made.
    instance.voyage=None
    by_id={cell.id:cell for cell in instance.vessel.cells}
    for stack in instance.vessel.stacks:
        if not stack.on_deck:continue
        bottom=by_id[stack.cell_ids[0]]
        for level in range(2,4):
            cid=format_slot(stack.bay,stack.row,82+2*level)
            instance.vessel.cells.append(bottom.model_copy(update={'id':cid,'vcg_base_m':bottom.vcg_base_m+2.591*level}))
            stack.cell_ids.append(cid)
        stack.max_mass_20_kg=30000.
        stack.max_height_m=4*2.591
    original=instance.vessel.model_dump(mode='json')
    pair=m.build_pair(name)
    assert pair['status']=='ready',pair
    for variant in ('baseline','defect'):
        item=pair[variant];data=item['input']
        assert data['vessel']==original
        cargo={box['id']:box for box in data['containers']}
        assert all(box['empty'] and box['gross_mass_kg']==box['tare_kg'] for box in cargo.values())
        occupied={p['container_id']:p['slot'] for p in data['onboard']['placements']}
        occupied.update({move['container_id']:move['dst'] for move in item['plan']['moves'] if move['kind']=='load'})
        for stack in data['vessel']['stacks']:
            mass=sum(cargo[bid]['gross_mass_kg'] for bid,slot in occupied.items() if slot in stack['cell_ids'])
            assert mass<=(stack['max_mass_20_kg'] or stack['max_mass_kg'])
        if name=='yard_self_blocking':
            assert len(item['plan']['moves'])==2
    if name=='yard_self_blocking':
        assert pair['baseline']['input']==pair['defect']['input']
        assert pair['baseline']['plan']['moves']==list(reversed(pair['defect']['plan']['moves']))
    from stowage.model import Instance,MovePlan
    from stowage.moves import apply_plan
    clean=apply_plan(Instance.model_validate(pair['baseline']['input']),MovePlan.model_validate(pair['baseline']['plan']))
    defect=apply_plan(Instance.model_validate(pair['defect']['input']),MovePlan.model_validate(pair['defect']['plan']))
    assert clean.legal,clean.violations
    assert {v.rule_id for v in defect.violations}=={'port.e3.quay_crossing'}


def test_pair_carries_expectations_to_its_exercise_and_clean_control(crossing_structure):
    m,instance=crossing_structure
    pair=m.build_pair('kg_inversion_within_class')
    assert pair['status']=='ready',pair
    assert pair['defect']['expected_rules']==m.CASES['kg_inversion_within_class']['expected_rules']
    assert pair['defect']['expected_mask_reasons']==pair['defect']['expected_rules']
    assert pair['baseline']['expected_rules']==[]
    assert pair['baseline']['expected_secondary_rules']==[]
    assert pair['baseline']['expected_mask_reasons']==[]


def test_placeholder_ship_never_qualifies():
    m = module()
    from stowage.model.fixtures import build_instance
    with pytest.raises(ValueError, match='production feeder'):
        m.validate_baseline(build_instance())


def test_inport_case_has_an_external_completion_contract(tmp_path):
    m = module()
    result = m.build_pair('inport_list_failure', external_pairs=tmp_path/'absent.json')
    assert result['status'] == 'missing'
    assert result['missing'] == ['external_pair_missing']
    assert result['contract'] == 'inport-list-v1'
    assert result['expected_rule'] == 'port.stability.cargo_operations'


def test_unknown_case_rejected_before_numerical_acquisition():
    m = module()
    with pytest.raises(ValueError, match='unknown'):
        m.build_pair('invented')


def test_external_inport_pair_cannot_edit_hull_or_liquids(tmp_path):
    m = module()
    assert set(m.INPORT_ALLOWED_DIFFERENCES) == {'id'}
    assert 'vessel' not in m.INPORT_ALLOWED_DIFFERENCES


def test_inport_pair_cannot_change_final_box_lifts():
    m=module()
    pair={key:{'input':{'id':'i','vessel':{'id':'real'}},'plan':{'moves':[
        {'kind':'load','container_id':'A','src':'yard','dst':'one'},
        {'kind':'load','container_id':'B','src':'yard','dst':'two'}]},
        'prefix_actions':[], 'action':{'op':'P','box':'A','slot':'one'}} for key in ('baseline','defect')}
    pair['defect']['plan']['moves'][0]['dst']='another'
    with pytest.raises(ValueError,match='same box lifts'):
        m.validate_inport_pair(pair)


def test_inport_pair_can_only_reorder_the_same_complete_input():
    m=module()
    from copy import deepcopy
    left={'input':{'id':'i','vessel':{'id':'real'}},'plan':{'moves':[
        {'kind':'load','container_id':'A','src':'yard','dst':'one'},
        {'kind':'load','container_id':'B','src':'yard','dst':'two'}]},'prefix_actions':[], 'action':{}}
    pair={'baseline':left,'defect':deepcopy(left)}
    pair['defect']['plan']['moves'].reverse()
    m.validate_inport_pair(pair)
    pair['defect']['input']['vessel']['id']='replacement'
    with pytest.raises(ValueError,match='common physical input'):
        m.validate_inport_pair(pair)


@pytest.fixture
def versioned_baseline(monkeypatch):
    """Metadata-only stand-in: no hull/checker qualification is asserted."""
    from types import SimpleNamespace
    from stowage.cache_warm import PRODUCTION_SHIPS
    from stowage.versions import component_versions, RANDOM_YARD_VERSION, CARGO_GENERATOR_VERSION
    from stowage.generate.instances_v2_random import RANDOM_V2_VERSION
    from stowage.generate.instances_v3 import CAMPAIGN_VERSION
    import stowage.vessel.fleet_conditions as conditions
    monkeypatch.setattr(conditions,'slice_condition',lambda vessel:{'version':conditions.CONDITION_VERSION})
    ship=PRODUCTION_SHIPS[0]
    vessel=SimpleNamespace(id=ship,slice_of=ship,hull=object(),cells=[object()],fixed_loads=[object()],model_dump=lambda **kw:{'id':ship})
    versions={**component_versions(),'random_yard':RANDOM_YARD_VERSION,'random_yard_v2':RANDOM_V2_VERSION,
              'cargo_generator':CARGO_GENERATOR_VERSION,'instances_campaign':CAMPAIGN_VERSION}
    m=module()
    monkeypatch.setattr(m,'_verified_canonical_physics',lambda vessel,parent:{'unit_test_metadata_only':True})
    return SimpleNamespace(_test_module=m,vessel=vessel,port=SimpleNamespace(max_crane_height_tiers=4),
        provenance=SimpleNamespace(versions=versions),family={'vessel':ship,'generator':'random_yard'},
        model_dump=lambda **kw:{'metadata_only_test':True})


def test_baseline_rejects_stale_cargo_generator_even_with_current_wrapper(versioned_baseline):
    m=versioned_baseline._test_module
    versioned_baseline.provenance.versions['cargo_generator']='STALE'
    with pytest.raises(ValueError,match='cargo_generator'):
        m.validate_baseline(versioned_baseline)


def test_baseline_selects_the_declared_producer_not_any_current_other_one(versioned_baseline):
    m=versioned_baseline._test_module
    versioned_baseline.family['generator']='simulated_yard'
    versioned_baseline.provenance.versions['instances']='STALE'
    with pytest.raises(ValueError,match='instances'):
        m.validate_baseline(versioned_baseline)


def test_current_simulated_producer_uses_actual_instances_provenance_key(versioned_baseline):
    from stowage.versions import SIMULATED_INSTANCES_VERSION, YARD_SIMULATOR_VERSION
    m=versioned_baseline._test_module
    versioned_baseline.family['generator']='simulated_yard'
    versions=versioned_baseline.provenance.versions
    versions.pop('random_yard');versions.pop('random_yard_v2')
    versions.update(instances=SIMULATED_INSTANCES_VERSION,yard_simulator=YARD_SIMULATOR_VERSION)
    assert m.validate_baseline(versioned_baseline)['parent_ship']==versioned_baseline.vessel.id


def test_baseline_rejects_stale_checker_under_current_generator(versioned_baseline):
    m=versioned_baseline._test_module
    versioned_baseline.provenance.versions['checker']='STALE'
    with pytest.raises(ValueError,match='checker'):
        m.validate_baseline(versioned_baseline)


@pytest.mark.parametrize('producer',['random_yard','simulated_yard'])
def test_switch_on_baseline_uses_producers_effective_current_versions(versioned_baseline,producer):
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.versions import component_versions,SIMULATED_INSTANCES_VERSION
    from stowage.generate.random_yard import effective_random_yard_version
    from stowage.generate.instances_v2_random import effective_random_v2_version
    from stowage.generate.cargo import effective_cargo_generator_version
    from stowage.yard.simulator import effective_yard_simulator_version
    with rule_freeze_v1(enabled=True):
        versioned_baseline.family['generator']=producer
        versioned_baseline.provenance.versions.update(component_versions(),
            random_yard=effective_random_yard_version(),random_yard_v2=effective_random_v2_version(),
            instances=SIMULATED_INSTANCES_VERSION,yard_simulator=effective_yard_simulator_version(),
            cargo_generator=effective_cargo_generator_version())
        assert versioned_baseline._test_module.validate_baseline(versioned_baseline)['parent_ship']


@pytest.mark.parametrize('field', ['hull', 'tanks', 'cells', 'stacks', 'bridge'])
def test_canonical_projection_refuses_replacement_physics_named_like_parent(field):
    m=module()
    from stowage.model.fixtures import build_instance
    parent=build_instance().vessel
    candidate=parent.model_copy(deep=True)
    if field=='hull': candidate.hull.max_list_rad*=2
    elif field=='tanks': candidate.tanks[0].capacity_m3*=2
    elif field=='cells': candidate.cells[0].tcg_m+=0.5
    elif field=='stacks': candidate.stacks[0].max_mass_kg*=2
    elif field=='bridge': candidate.bridge.eye_z_m+=1
    with pytest.raises(ValueError,match='canonical'):
        m.verify_physical_projection(candidate,parent)


def test_identical_canonical_physics_is_a_projection_not_numerical_evidence():
    m=module()
    from stowage.model.fixtures import build_instance
    parent=build_instance().vessel
    assert m.verify_physical_projection(parent.model_copy(deep=True),parent) is None


@pytest.fixture
def pinned_canonical(tmp_path,monkeypatch):
    import hashlib,json,os
    from stowage.cache_warm import PRODUCTION_SHIPS
    from stowage.model.fixtures import build_instance
    from stowage.vessel import fleet,fleet_cache
    m=module()
    parent=PRODUCTION_SHIPS[0]
    vessel=build_instance().vessel.model_copy(update={'id':parent,'slice_of':None})
    original=tmp_path/'immutable';original.mkdir()
    attempt=tmp_path/'attempt';attempt.mkdir()
    filename=parent+'.full.hull.unit-current.json'
    path=original/filename
    path.write_text(vessel.model_dump_json())
    replacement=vessel.model_copy(deep=True);replacement.hull.max_list_rad*=2
    (attempt/filename).write_text(replacement.model_dump_json())
    sources={'vessels':str(original),'stability':str(tmp_path/'stability'),'linear':str(tmp_path/'linear')}
    files={'vessels/'+filename:hashlib.sha256(path.read_bytes()).hexdigest()}
    pin={'schema_version':1,'sources':sources,'files':files}
    pin['sha256']=hashlib.sha256(json.dumps({'sources':sources,'files':files},sort_keys=True).encode()).hexdigest()
    pin_path=tmp_path/'pin.json';pin_path.write_text(json.dumps(pin))
    monkeypatch.setenv('CAMPAIGN_NUMERICAL_CACHE_PIN_PATH',str(pin_path))
    monkeypatch.setenv('CAMPAIGN_NUMERICAL_CACHE_PIN_SHA256',hashlib.sha256(pin_path.read_bytes()).hexdigest())
    monkeypatch.delenv('CAMPAIGN_NUMERICAL_CACHE_PIN',raising=False)
    monkeypatch.setenv('STOWAGE_VESSEL_CACHE_DIR',str(attempt))
    monkeypatch.setattr(fleet,'_parameter_bytes',lambda name:b'unit-parameter-bytes')
    monkeypatch.setattr(fleet_cache,'_cache_key',lambda *args:'unit-current')
    monkeypatch.setattr(fleet,'load_vessel',lambda *args,**kwargs:pytest.fail('canonical builder fallback is forbidden'))
    return m,vessel,parent,path


def test_canonical_loader_uses_original_pinned_bytes_not_mutable_attempt(pinned_canonical):
    m,vessel,parent,path=pinned_canonical
    result=m._verified_canonical_physics(vessel,parent)
    assert result['path']==str(path)


def test_missing_original_canonical_refuses_even_when_attempt_has_one(pinned_canonical):
    m,vessel,parent,path=pinned_canonical
    path.unlink()
    with pytest.raises(FileNotFoundError):
        m._verified_canonical_physics(vessel,parent)


def test_replaced_canonical_bytes_refuse_against_frozen_hash(pinned_canonical):
    m,vessel,parent,path=pinned_canonical
    changed=vessel.model_copy(deep=True);changed.hull.max_list_rad*=2
    path.write_text(changed.model_dump_json())
    with pytest.raises(ValueError,match='checksum'):
        m._verified_canonical_physics(vessel,parent)
