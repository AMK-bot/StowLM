"""Physical migration witnesses built on current production feeder slices.

Generation acceptance is only baseline provenance. The caller must execute both
members with the public full checker and default production mask; these builders
never turn generator screening or a schema exception into behavioural proof.
"""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
from hashlib import sha256
import json
import os
import importlib.util
from pathlib import Path

from eval.snapshot_io import read_snapshot

HERE = Path(__file__).resolve().parent
DEFAULT_BASELINE = HERE/'canary-inputs'/'qualified-feeder.json'
DEFAULT_EXTERNAL_PAIRS = HERE/'canary-inputs'/'external-pairs.json'
INPORT_ALLOWED_DIFFERENCES = ('id',)
CASES = {
    'kg_inversion_within_class': dict(expected_rule='expert.e2.weight_order', expectation='deny', expected_rules=['expert.e1.heavy_bottom', 'expert.e2.weight_order']),
    'ship_crossing_at_h': dict(expected_rule='port.e3.quay_crossing', expectation='deny'),
    'yard_crossing_at_h': dict(expected_rule='port.e3.yard_crossing', expectation='accept'),
    'yard_crossing_no_free_row': dict(expected_rule='port.e3.yard_crossing', expectation='deny'),
    'yard_self_blocking': dict(expected_rule='port.e3.quay_crossing', expectation='deny'),
    'class7_laem_pol': dict(expected_rule='port.dg.refused_class', expectation='deny'),
    'class7_later_pod': dict(expected_rule='port.dg.refused_class', expectation='deny'),
    'unflagged_class1_laem': dict(expected_rule='port.dg.direct_delivery', expectation='deny'),
    'flagged_nonclass1': dict(expected_rule='port.dg.direct_delivery', expectation='deny'),
    'inport_list_failure': dict(expected_rule='port.stability.cargo_operations', expectation='deny'),
    'e6_soft_heavy_on_deck': dict(expected_rule='expert.e6.heavy_under_deck', expectation='accept'),
    'kg_equal_mass': dict(expected_rule='expert.e2.weight_order', expectation='accept'),
    'oog_empty_killed_slots': dict(expected_rule='expert.e9.over_height', expectation='accept'),
    'oog_occupied_killed_slots': dict(expected_rule='expert.e9.over_height', expectation='deny'),
}
# A refusal can prevent the objective from loading its target. This fallout is
# optional, never a substitute for the primary violation or a mask reason.
_REFUSAL_CASES = {'ship_crossing_at_h', 'yard_crossing_no_free_row', 'yard_self_blocking',
                 'class7_laem_pol', 'class7_later_pod', 'unflagged_class1_laem', 'flagged_nonclass1'}
for _name, _spec in CASES.items():
    _spec.setdefault('expected_rules', [_spec['expected_rule']] if _spec['expectation']=='deny' else [])
    _spec['expected_secondary_rules'] = ['objective.all_loaded'] if _name in _REFUSAL_CASES else []
    _spec['expected_mask_reasons'] = list(_spec['expected_rules'])
CANARIES = {key: value['expected_rule'] for key, value in CASES.items()}


def _with_expectations(name, pair):
    """The registry, never a caller-supplied report, defines the verdicts."""
    spec=CASES[name]
    fields=('expected_rules', 'expected_secondary_rules', 'expected_mask_reasons')
    pair.update({key:list(spec[key]) for key in fields})
    for key in ('baseline','defect'):
        if key in pair:
            pair[key].update({field:list(spec[field]) if key=='defect' else [] for field in fields})
            pair[key]['expectation']='accept' if key=='baseline' else spec['expectation']
    return pair


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def verify_physical_projection(vessel, canonical):
    """A slice may crop decisions and vary background, never replace physics."""
    def data(value):
        if isinstance(value,list):
            return [data(item) for item in value]
        return value.model_dump(mode='json') if hasattr(value,'model_dump') else value
    for field in ('hull','tanks','tank_pairs','heat_sources','imdg_placement_geometry',
                  'exclusion_regions','bridge','voyage_policy'):
        if data(getattr(vessel,field)) != data(getattr(canonical,field)):
            raise ValueError('canary '+field+' differs from pinned canonical fleet physics')
    cells={item.id:item for item in [*canonical.cells,*canonical.fixed_load_cells]}
    stacks={item.id:item for item in [*canonical.stacks,*canonical.fixed_load_stacks]}
    for field, source in (('cells',cells),('fixed_load_cells',cells),('stacks',stacks),('fixed_load_stacks',stacks)):
        for record in getattr(vessel,field):
            if record.id not in source or data(record)!=data(source[record.id]):
                raise ValueError('canary '+field+' differs from pinned canonical fleet physics: '+record.id)
    decision_ids={item.id for item in vessel.stacks}
    fixed_ids={item.id for item in vessel.fixed_load_stacks}
    if decision_ids & fixed_ids or decision_ids | fixed_ids != set(stacks):
        raise ValueError('canary stack partition differs from pinned canonical fleet geometry')
    for field, selected in (('cells',decision_ids),('fixed_load_cells',fixed_ids)):
        expected={item.id for item in cells.values() if item.stack_id in selected}
        if {item.id for item in getattr(vessel,field)} != expected:
            raise ValueError('canary '+field+' is not a complete canonical stack projection')
    parent_covers={item.id:item for item in [*canonical.hatch_covers,*canonical.fixed_load_hatch_covers]}
    for field, selected in (('hatch_covers',decision_ids),('fixed_load_hatch_covers',fixed_ids)):
        expected=[]
        for item in parent_covers.values():
            hold=[sid for sid in item.hold_stack_ids if sid in selected]
            deck=[sid for sid in item.deck_stack_ids if sid in selected]
            if hold or deck:
                expected.append(data(item.model_copy(update={'hold_stack_ids':hold,'deck_stack_ids':deck})))
        actual=[data(item) for item in getattr(vessel,field)]
        if sorted(actual,key=lambda value:value['id']) != sorted(expected,key=lambda value:value['id']):
            raise ValueError('canary '+field+' differs from canonical cover projection')
    expected=[]
    for area in canonical.dg_doc_areas:
        retained=[sid for sid in area.stack_ids if sid in decision_ids]
        if retained:
            expected.append(data(area.model_copy(update={'stack_ids':retained})))
    if sorted(data(vessel.dg_doc_areas),key=lambda value:value['id']) != sorted(expected,key=lambda value:value['id']):
        raise ValueError('canary DG permissions differ from canonical fleet projection')


def _verified_canonical_physics(vessel, parent):
    """Read only the canonical full hull already in the immutable cache pin.

    No public builder fallback is called: an absent current content-keyed entry
    is a cache-warm dependency, never authority for attempt-local geometry.
    """
    from stowage.vessel.fleet import _parameter_bytes
    from stowage.vessel.fleet_cache import _cache_key
    from stowage.model import Vessel
    spec=importlib.util.spec_from_file_location('canary_geometry_pin_transport',HERE/'pin_transport.py')
    transport=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    pin=transport.read_pin()
    sources, files=pin.get('sources'),pin.get('files')
    if not isinstance(sources,dict) or not isinstance(files,dict):
        raise ValueError('canonical fleet numerical cache pin is missing')
    digest=sha256(json.dumps({'sources':sources,'files':files},sort_keys=True).encode()).hexdigest()
    if digest!=pin.get('sha256'):
        raise ValueError('canonical fleet numerical cache pin is corrupt')
    payload=_parameter_bytes(parent)
    key=_cache_key(parent,'full',True,payload)
    filename=f'{parent}.full.hull.{key}.json'
    expected=files.get('vessels/'+filename)
    if not isinstance(expected,str):
        raise ValueError('current canonical full hull is absent from frozen cache; cache_warm required')
    root=Path(sources.get('vessels','')).resolve()
    path=root/filename
    if path.is_symlink() or not path.is_relative_to(root):
        raise ValueError('canonical fleet cache path is invalid')
    snapshot=read_snapshot(path,expected_sha256=expected)
    # Vessel-cache payloads are unwrapped models without Instance's schema key.
    canonical=Vessel.model_validate(snapshot.json())
    if canonical.id!=parent or canonical.slice_of is not None:
        raise ValueError('canonical full fleet cache has a different root')
    verify_physical_projection(vessel,canonical)
    snapshot.require_fresh()
    return dict(path=str(path),sha256=snapshot.sha256,numerical_cache_sha256=pin['sha256'])


def inport_request():
    """Concrete candidate bookings/order for 4.1; qualification is still required."""
    from stowage.cache_warm import PRODUCTION_SHIPS
    from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id
    rows=[]
    for bay in (1,3):
        for tier in (82,84,86,88):
            for row in (5,6):
                index=len(rows)
                rows.append(dict(container_id=make_container_id(SYNTHETIC_OWNER_CODES[0],800000+index),
                    slot=f'{bay:03d}{row:02d}{tier:02d}',gross_mass_kg=30000.,max_gross_mass_kg=30500.,
                    length_ft=20,height_m=2.591,tare_kg=2200.,yard_bay=index+1,yard_row=1,yard_tier=1))
    return dict(contract='inport-list-v1',vessel=PRODUCTION_SHIPS[0],generator='random_yard',
        rung='small',seed=100100000,port='small_feeder',noise=0,c2_screened=True,
        cargo=rows,clean_order=[row['container_id'] for row in rows],
        defect_order=[row['container_id'] for side in (5,6) for row in rows if int(row['slot'][3:5])==side],
        yard_block='first operational block from the generated profile',
        expected_checker_rule='port.stability.cargo_operations',expected_mask_reason='port.stability.cargo_operations',
        qualification='candidate only; 4.1 must retain original hull/background/HEEL and report the first physical bad prefix')


def validate_baseline(instance):
    """Reject placeholder hulls, stale generator/condition provenance and absent H.

    No hull, fixed background, liquid or qualification data is rewritten here.
    Full numerical qualification is checked by the execution owner, not inferred.
    """
    from stowage.cache_warm import PRODUCTION_SHIPS
    from stowage.conventions import canonical_vessel_id
    from stowage.versions import component_versions
    vessel = instance.vessel
    parent = canonical_vessel_id(vessel.slice_of or vessel.id.split('.')[0])
    if parent not in PRODUCTION_SHIPS or 'synthetic_feeder' in vessel.id:
        raise ValueError('canary baseline must be a production feeder slice')
    if vessel.hull is None or not vessel.cells or not vessel.fixed_loads:
        raise ValueError('canary baseline lacks fleet hull, cells or fixed background')
    if instance.port.max_crane_height_tiers is None:
        raise ValueError('canary baseline lacks current operational H')
    current = component_versions()
    versions = instance.provenance.versions
    # Producer/condition changes must not be hidden by a fresh wrapper stamp.
    from stowage.versions import SIMULATED_INSTANCES_VERSION
    from stowage.generate.random_yard import effective_random_yard_version
    from stowage.generate.instances_v2_random import effective_random_v2_version
    from stowage.generate.cargo import effective_cargo_generator_version
    from stowage.yard.simulator import effective_yard_simulator_version
    from stowage.generate.instances_v3 import CAMPAIGN_VERSION
    from stowage.vessel.fleet_conditions import CONDITION_VERSION, slice_condition
    producer = instance.family.get('generator')
    producer_keys = {
        'random_yard': {'random_yard':effective_random_yard_version(), 'random_yard_v2':effective_random_v2_version()},
        'simulated_yard': {'instances':SIMULATED_INSTANCES_VERSION, 'yard_simulator':effective_yard_simulator_version()},
    }
    if producer not in producer_keys:
        raise ValueError('baseline lacks a recognized instance generator family')
    required_versions = {**current, **producer_keys[producer],
                         'cargo_generator':effective_cargo_generator_version(),
                         'instances_campaign':CAMPAIGN_VERSION}
    for key, expected in required_versions.items():
        if versions.get(key) != expected:
            raise ValueError('missing or stale canary baseline component: '+key)
    condition = slice_condition(vessel)
    if not condition or condition.get('version') != CONDITION_VERSION:
        raise ValueError('baseline lacks current slice condition version')
    family_ship = instance.family.get('vessel')
    if family_ship and canonical_vessel_id(family_ship) != parent:
        raise ValueError('canary baseline family/vessel mismatch')
    canonical_binding=_verified_canonical_physics(vessel,parent)
    return dict(parent_ship=parent, vessel_sha256=_digest(vessel.model_dump(mode='json')),
                baseline_sha256=_digest(instance.model_dump(mode='json')),
                generator_versions=dict(versions),canonical_physics=canonical_binding,
                numerical_qualification='requires_live_execution')


def generate_baseline(output=DEFAULT_BASELINE, *, rung='small', seed=100000000):
    """Explicit one-instance public generator hook; never called on admission.

    Run in the campaign cache-adapter worker after whole-fleet qualification.
    Its accepted result remains a candidate until the full paired execution.
    """
    from stowage.cache_warm import PRODUCTION_SHIPS
    from stowage.generate.instances_v3 import scenario_specs, generate
    spec = next(row for row in scenario_specs(pilot=True, seed=seed-100000000,
                profiles=[rung], generators=['random_yard'], c2_screened=True)
                if row['vessel'] == PRODUCTION_SHIPS[0])
    # Actual small-feeder profile H=4 permits ship-side crossing witnesses;
    # choosing a real operational profile never lowers a threshold by override.
    spec['port'] = 'small_feeder'
    instance, audit = generate(spec)
    validate_baseline(instance)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(instance.model_dump(mode='json'), sort_keys=True, indent=2)+'\n')
    output.with_suffix('.audit.json').write_text(json.dumps(audit, sort_keys=True, indent=2)+'\n')
    return instance


def acquire_baseline(baseline=None):
    from stowage.model import Instance
    if baseline is None:
        baseline = Path(os.environ.get('STOWAGE_CANARY_BASELINE', DEFAULT_BASELINE))
    if isinstance(baseline, (str, Path)):
        snap = read_snapshot(Path(baseline))
        instance = snap.model(Instance)
        snap.require_fresh()
    elif isinstance(baseline, dict):
        instance = Instance.model_validate(baseline)
    else:
        instance = Instance.model_validate(baseline.model_dump(mode='json'))
    return instance, validate_baseline(instance)


def _missing(name, code, reason='', **extra):
    return dict(status='missing', name=name, kind='reject' if CASES[name]['expectation']=='deny' else 'accept',
                expected_rule=CASES[name]['expected_rule'], missing=[code], reason=reason, **extra)


def _member(instance, actions, *, expectation='accept', required_metrics=None, plan=None):
    from stowage.actions import get_action_space
    from stowage.model import Instance
    space = get_action_space()
    instance = Instance.model_validate(instance.model_dump(mode='json'))
    if plan is None:
        plan = space.expand(instance, actions)
    encoded = [space.encode(action) if not isinstance(action, dict) else action for action in actions]
    return dict(status='ready', input=instance.model_dump(mode='json'), plan=plan.model_dump(mode='json'),
        action=encoded[-1], prefix_actions=encoded[:-1], expectation=expectation,
        required_metrics=required_metrics or {})


def validate_inport_pair(members):
    """Same physical problem and final box lifts; only their order may differ."""
    left, right = (deepcopy(members[key]['input']) for key in ('baseline', 'defect'))
    for value in (left, right):
        value.pop('id', None)
    if left != right:
        raise ValueError('in-port pair edits its common physical input; only lift order may differ')
    sequences = [[move for move in members[key]['plan'].get('moves', [])
                  if move.get('kind') not in {'hatch_cover_off', 'hatch_cover_on'}]
                 for key in ('baseline', 'defect')]
    if not sequences[0] or Counter(map(_digest,sequences[0])) != Counter(map(_digest,sequences[1])):
        raise ValueError('in-port pair must contain the same box lifts and final stow')
    if sequences[0] == sequences[1]:
        raise ValueError('in-port list pair must vary the loading prefix')


def _cargo(instance, count):
    from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id
    from stowage.model import YardStack
    template = instance.containers[0]
    boxes = []
    for index in range(count):
        box = template.model_copy(deep=True, update=dict(id=make_container_id(SYNTHETIC_OWNER_CODES[0], 800000+index),
            gross_mass_kg=12000., max_gross_mass_kg=30500., tare_kg=2200., length_ft=20, iso_type='22G1',
            height_m=2.591, reefer=False, reefer_setpoint_c=None, dg=None, oog=None,
            special_stowage=[], direct_delivery=False, empty=False, pol=instance.port.locode,
            pod=instance.rotation.calls[min(instance.call_index+1, len(instance.rotation.calls)-1)].locode))
        boxes.append(box)
    instance.containers = boxes
    instance.load_list = [box.id for box in boxes]
    instance.discharge_list = []
    instance.onboard.placements = []
    instance.yard.quay_buffer = []
    block = instance.port.yard.blocks[0]
    instance.yard.stacks = [YardStack(key=f'{block.id}-{i+1:03d}-01', length_ft=20, boxes=[box.id]) for i, box in enumerate(boxes)]
    return boxes


def _crossing_cargo(instance, count):
    """Use real empty 20-foot boxes: crossing binds on height, not payload.

    Keep declared tare, envelope and every fleet rating unchanged. Full checker
    execution must still establish securing/stability for each qualified pair.
    """
    boxes=_cargo(instance,count)
    for box in boxes:
        box.gross_mass_kg=box.tare_kg
        box.empty=True
    return boxes


def _external(name, path):
    """Read an owner-supplied pair; runtime must still execute both members.

    In-port control and defect must retain identical hull/background/liquids,
    port, voyage and rule policy. Only cargo disposition and move order vary.
    """
    path = Path(path or DEFAULT_EXTERNAL_PAIRS)
    if not path.exists():
        return _missing(name, 'external_pair_missing', str(path), contract='inport-list-v1' if name=='inport_list_failure' else 'qualified-pair-v1')
    snapshot = read_snapshot(path)
    payload = snapshot.json()
    pair = payload.get('pairs', {}).get(name)
    if not isinstance(pair, dict):
        return _missing(name, 'external_pair_missing', str(path), contract='inport-list-v1' if name=='inport_list_failure' else 'qualified-pair-v1')
    from stowage.model import Instance, MovePlan
    members = {}
    qualifications = []
    for key in ('baseline', 'defect'):
        member = pair.get(key, {})
        instance = Instance.model_validate(member.get('input'))
        plan = MovePlan.model_validate(member.get('plan'))
        qualifications.append(validate_baseline(instance))
        if plan.instance_id != instance.id or plan.call_index != instance.call_index:
            raise ValueError('external canary plan/input identity mismatch')
        if not isinstance(member.get('action'), dict):
            raise ValueError('external canary requires a canonical action')
        members[key] = dict(member, status='ready', expectation='accept' if key=='baseline' else CASES[name]['expectation'])
    if name == 'inport_list_failure':
        if payload.get('contracts', {}).get(name) != 'inport-list-v1':
            raise ValueError('in-port pair contract is absent or stale')
        validate_inport_pair(members)
    snapshot.require_fresh()
    return dict(status='ready', name=name, kind='reject' if CASES[name]['expectation']=='deny' else 'accept',
        expected_rule=CASES[name]['expected_rule'], qualification=qualifications,
        external_snapshot=dict(path=str(path.resolve()), sha256=snapshot.sha256), **members)


def build_pair(name, baseline=None, external_pairs=None, baseline_path=None):
    if name not in CASES:
        raise ValueError('unknown migration canary: '+name)
    # These demand owner-supplied physical geometry/shipments, never fabricated
    # DoC permissions, radiological evidence or weaker liquid authority.
    external = {'inport_list_failure'}
    if name in external:
        try:
            return _with_expectations(name, _external(name, external_pairs))
        except Exception as error:
            return _missing(name, 'external_pair_invalid', f'{type(error).__name__}: {error}')
    try:
        instance, qualification = acquire_baseline(baseline if baseline is not None else baseline_path)
        from stowage.actions.space import Place
        from stowage.model import OutOfGauge, Placement, YardStack
        from stowage.model.moves import Load
        instance.id = 'migration.'+name
        cells = {cell.id:cell for cell in instance.vessel.cells}
        stacks = sorted(instance.vessel.stacks, key=lambda stack:stack.id)
        deck = [stack for stack in stacks if stack.on_deck and stack.cell_ids]
        hold = [stack for stack in stacks if not stack.on_deck and len(stack.cell_ids)>1]
        pair = dict(status='ready', name=name, kind='reject' if CASES[name]['expectation']=='deny' else 'accept',
                    expected_rule=CASES[name]['expected_rule'], qualification=qualification)
        if name in {'kg_inversion_within_class', 'kg_equal_mass'}:
            boxes = _cargo(instance, 2)
            stack = hold[0]
            actions = [Place(boxes[0].id, stack.cell_ids[0]), Place(boxes[1].id, stack.cell_ids[1])]
            pair['baseline'] = _member(instance, actions)
            defect = instance.model_copy(deep=True)
            if name == 'kg_inversion_within_class':
                defect.containers[1].gross_mass_kg += 1
                pair['expected_rules'] = ['expert.e1.heavy_bottom', 'expert.e2.weight_order']
            pair['defect'] = _member(defect, actions, expectation=CASES[name]['expectation'],
                                      plan=__import__('stowage.model', fromlist=['MovePlan']).MovePlan.model_validate(pair['baseline']['plan']))
        elif name == 'e6_soft_heavy_on_deck':
            boxes = _cargo(instance, 1)
            actions = [Place(boxes[0].id, deck[0].cell_ids[0])]
            pair['baseline'] = _member(instance, actions)
            instance.containers[0].gross_mass_kg = 30000.
            pair['defect'] = _member(instance, actions, required_metrics={'heavy_on_deck_count':1})
        elif name in {'oog_empty_killed_slots', 'oog_occupied_killed_slots'}:
            boxes = _cargo(instance, 2 if name.endswith('occupied_killed_slots') else 1)
            # Explicit under-deck boundary: E9 top_only must be gone, while the
            # physically killed upper cell remains genuinely empty in accept.
            stack = hold[0]
            actions = [Place(boxes[0].id, stack.cell_ids[0])]
            if len(boxes)>1:
                actions.append(Place(boxes[1].id, stack.cell_ids[1]))
            pair['baseline'] = _member(instance, actions)
            instance.containers[0].oog = OutOfGauge(over_height_m=0.1)
            pair['defect'] = _member(instance, actions, expectation=CASES[name]['expectation'],
                    plan=__import__('stowage.model', fromlist=['MovePlan']).MovePlan.model_validate(pair['baseline']['plan']))
        elif name == 'ship_crossing_at_h':
            H = instance.port.max_crane_height_tiers
            columns = [stack for stack in deck if len(stack.cell_ids) >= H]
            near = max(columns, key=lambda stack:cells[stack.cell_ids[0]].tcg_m) if instance.port.berthing_side=='port' else min(columns,key=lambda stack:cells[stack.cell_ids[0]].tcg_m)
            far = next(stack for stack in deck if stack.bay==near.bay and stack.row!=near.row and
                       ((cells[stack.cell_ids[0]].tcg_m < cells[near.cell_ids[0]].tcg_m) == (instance.port.berthing_side=='port')))
            boxes = _crossing_cargo(instance, H+1)
            instance.stage = 'B'
            instance.load_list = [boxes[0].id]
            instance.yard.stacks = instance.yard.stacks[:1]
            instance.onboard.placements = [Placement(container_id=box.id,slot=slot) for box,slot in zip(boxes[1:],near.cell_ids)]
            actions = [Place(boxes[0].id, far.cell_ids[0])]
            clean = instance.model_copy(deep=True)
            clean.onboard.placements.pop()
            clean.containers = clean.containers[:-1]
            pair['baseline'] = _member(clean, actions)
            # Reuse only the complete clean move list; no illegal action is
            # passed through an expander expected to reject it before checking.
            from stowage.model import MovePlan
            pair['defect'] = _member(instance, actions, expectation='deny', plan=MovePlan.model_validate(pair['baseline']['plan']))
        elif name == 'yard_self_blocking':
            H = instance.port.max_crane_height_tiers
            columns = [stack for stack in deck if len(stack.cell_ids)>=H]
            near = (max if instance.port.berthing_side=='port' else min)(columns,key=lambda stack:cells[stack.cell_ids[0]].tcg_m)
            far = next(stack for stack in deck if stack.bay==near.bay and stack.row!=near.row and
                ((cells[stack.cell_ids[0]].tcg_m<cells[near.cell_ids[0]].tcg_m)==(instance.port.berthing_side=='port')))
            boxes = _crossing_cargo(instance,H+1)
            instance.stage='B'
            instance.load_list=[boxes[0].id,boxes[1].id]
            instance.yard.stacks=instance.yard.stacks[:2]
            instance.onboard.placements=[Placement(container_id=box.id,slot=slot) for box,slot in zip(boxes[2:],near.cell_ids)]
            far_action=Place(boxes[0].id,far.cell_ids[0])
            near_action=Place(boxes[1].id,near.cell_ids[H-1])
            pair['baseline']=_member(instance,[far_action,near_action])
            from stowage.model import MovePlan
            plan=MovePlan.model_validate(pair['baseline']['plan'])
            loads=[move for move in plan.moves if move.kind=='load']
            # Deck-only actions have no hatch-lift prefix. Same cargo, same
            # final positions, only the unsafe lift order differs.
            if len(loads)!=2 or len(plan.moves)!=2:
                raise ValueError('self-blocking pair requires two isolated deck loads')
            plan.moves=list(reversed(plan.moves))
            pair['defect']=_member(instance,[near_action,far_action],expectation='deny',plan=plan)
        elif name in {'yard_crossing_at_h', 'yard_crossing_no_free_row'}:
            from stowage.ports import load_port, yard_blocking_tiers, effective_yard_tiers
            from stowage.model import PortCall
            from stowage.generate.random_voyage import build_voyage_spec
            # A8: use the actual Laem yard5/quay8 profile, never lower either
            # limit. Historical case names retain "at_h" for journal identity.
            instance.port = load_port('laem_chabang_like')
            instance.yard.yard_id = instance.port.yard.id
            instance.family = {**instance.family, 'port':instance.port.archetype}
            instance.rotation.calls[instance.call_index] = PortCall(index=instance.call_index,
                locode=instance.port.locode,archetype=instance.port.archetype)
            instance.voyage = build_voyage_spec(instance.rotation,instance.call_index,instance.port,seed=instance.seed)
            block = next(block for block in instance.port.yard.blocks if block.n_rows >= 3)
            yard_height = yard_blocking_tiers(instance.port,block)
            boxes = _cargo(instance, yard_height+1)
            target = boxes[0]
            lane, far = (1, block.n_rows) if block.truck_lane_side=='low' else (block.n_rows, 1)
            instance.load_list = [target.id]
            near = f'{block.id}-001-{lane:02d}'
            far_key = f'{block.id}-001-{far:02d}'
            instance.yard.stacks = [YardStack(key=far_key,length_ft=20,boxes=[target.id]),
                                   YardStack(key=near,length_ft=20,boxes=[b.id for b in boxes[1:]])]
            actions = [Place(target.id, deck[0].cell_ids[0])]
            clean = instance.model_copy(deep=True)
            clean.yard.stacks[1].boxes.pop()
            removed = boxes[-1].id
            clean.containers = [box for box in clean.containers if box.id != removed]
            pair['baseline'] = _member(clean, actions)
            if name == 'yard_crossing_at_h':
                pair['defect'] = _member(instance, actions, required_metrics={'relocations':1})
            else:
                # A physically full real yard has no legal relocation address.
                # Populate its actual addresses; do not truncate the layout or
                # reduce equipment capacity to manufacture this condition.
                from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id
                from stowage.model import MovePlan
                template = boxes[-1]
                occupied = {stack.key for stack in instance.yard.stacks}
                serial = 900000
                for yard_block in instance.port.yard.blocks:
                    cap = effective_yard_tiers(instance.port,yard_block)
                    for bay in range(1,yard_block.n_bays+1):
                        for row in range(1,yard_block.n_rows+1):
                            key = f'{yard_block.id}-{bay:03d}-{row:02d}'
                            if key in occupied:
                                continue
                            ids = []
                            for _ in range(cap):
                                box = template.model_copy(deep=True,update={'id':make_container_id(SYNTHETIC_OWNER_CODES[0],serial)})
                                serial += 1
                                instance.containers.append(box)
                                ids.append(box.id)
                            instance.yard.stacks.append(YardStack(key=key,length_ft=20,boxes=ids))
                pair['defect'] = _member(instance, actions, expectation='deny',plan=MovePlan.model_validate(pair['baseline']['plan']))
        elif name in {'class7_laem_pol', 'class7_later_pod', 'unflagged_class1_laem'}:
            from stowage.model import DangerousGoods, PortCall, MovePlan
            from stowage.ports import load_port
            port = load_port('singapore_like' if name=='class7_later_pod' else 'laem_chabang_like')
            instance.port = port
            instance.yard.yard_id = port.yard.id
            instance.rotation.calls[instance.call_index] = PortCall(index=instance.call_index,locode=port.locode,archetype=port.archetype)
            if name=='class7_later_pod':
                instance.rotation.calls[instance.call_index+1] = PortCall(index=instance.call_index+1,locode='THLCH',archetype='laem_chabang_like')
            from stowage.generate.random_voyage import build_voyage_spec
            instance.voyage=build_voyage_spec(instance.rotation,instance.call_index,port,seed=instance.seed)
            boxes = _cargo(instance,1)
            target = boxes[0]
            target.pod = instance.rotation.calls[instance.call_index+1].locode
            # Preserve actual per-ship voyage declarations. A port-density or
            # rotation mismatch is a construction failure, never waived.
            actions = [Place(target.id,deck[0].cell_ids[0])]
            pair['baseline'] = _member(instance,actions)
            target.dg = DangerousGoods(un_number='2910' if name.startswith('class7') else '0004',
                imdg_class='7' if name.startswith('class7') else '1.1D',stowage_category='A' if name.startswith('class7') else '01',
                packing_form='packaged_goods_in_closed_freight_containers',ctu_closed=True)
            target.direct_delivery = port.requires_direct_delivery(target.dg.imdg_class)
            plan = MovePlan.model_validate(pair['baseline']['plan'])
            if target.direct_delivery:
                instance.yard.stacks=[]
                # Invalid handling lives in the plan; never forge an invalid
                # free direct_delivery marker in the instance (D66).
                pair['construction_note']='derived class1 flag true; defective plan retains yard endpoint'
            pair['defect'] = _member(instance,actions,expectation='deny',plan=plan)
            pair['shipment_qualification_note']='Actual fleet DoC and shipment assessments must allow the DG; unrelated DoC/radiological failures remain missing, never proof.'
        elif name == 'flagged_nonclass1':
            boxes = _cargo(instance, 1)
            actions = [Place(boxes[0].id, deck[0].cell_ids[0])]
            pair['baseline'] = _member(instance, actions)
            plan = __import__('stowage.model', fromlist=['MovePlan']).MovePlan.model_validate(pair['baseline']['plan'])
            for move in plan.moves:
                if move.kind=='load':
                    move.src='truck'
            # Do not freely set direct_delivery. The derived fact remains false;
            # only the illegal truck endpoint changes at plan level (D66).
            pair['defect'] = _member(instance, actions, expectation='deny', plan=plan)
        return _with_expectations(name, pair)
    except Exception as error:
        return _missing(name, 'qualified_pair_unavailable', f'{type(error).__name__}: {error}')


def build_case(name, **kwargs):
    pair = build_pair(name, **kwargs)
    return pair.get('defect', pair)
