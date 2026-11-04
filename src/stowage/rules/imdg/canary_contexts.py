"""Authored DG canary contexts for the qualified-slice preparation owner.

These are synthetic fixture constructors, not clearance or qualification APIs.
They leave vessel physics/service policy unchanged, author decision bookings,
and register evidence only after the actual instance/entry/voyage identities
exist. A campaign owner must condition/qualify the resulting slice, construct
real plans and canonical actions, and run the full checker/default C2 mask.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from stowage.conventions import cells_of_slot, parse_slot
from stowage.model import Instance
from stowage.model.cargo import DangerousGoods
from stowage.model.ship import Placement
from stowage.rules.imdg.dgl import lookup_dgl
from stowage.rules.imdg.scenarios import context_for, provenance_matches


_CONTEXT_ROOT = Path(__file__).resolve().parents[4] / 'experiments/campaign-g1/canary-inputs/dg-contexts'
_STOWAGE = 'imdg.stowage.3_2'
_SHIP = 'imdg.segregation.7_4_3'
_IDS = {'SW1', 'SW2', 'H1', 'SW19', 'SG17', 'SG25_2_1', 'SG25_3'}
_ASSESSMENTS = {'h1', 'sw19', 'sunlight'}


def load_context_catalog(path=None) -> dict:
    """Load JSON requests, keeping candidate inputs distinct from external pairs."""
    manifest_path = Path(path) if path is not None else _CONTEXT_ROOT / 'requests.json'
    payload = manifest_path.read_bytes()
    catalog = json.loads(payload)
    pins = {manifest_path.name: sha256(payload).hexdigest()}
    if (not isinstance(catalog, dict) or catalog.get('contract') != 'dg-context-request-v1'
            or catalog.get('status') != 'pending_qualification' or catalog.get('evidence_eligible') is not False
            or catalog.get('synthetic') is not True or set(catalog.get('requests', {})) != _IDS):
        raise ValueError('DG contexts require the complete unqualified synthetic request contract')
    requests = {}
    for key, relative in catalog['requests'].items():
        if not isinstance(relative, str):
            raise ValueError('context manifest must reference JSON request files')
        source = (manifest_path.parent / relative).resolve()
        if not source.is_relative_to(manifest_path.parent.resolve()) or source.suffix != '.json':
            raise ValueError('context request must remain inside its catalog directory')
        payload = source.read_bytes()
        pins[relative] = sha256(payload).hexdigest()
        request = json.loads(payload)
        if (not isinstance(request, dict) or request.get('id') != key or request.get('synthetic') is not True
                or request.get('contract') != catalog['contract']
                or request.get('status') != 'pending_qualification' or request.get('evidence_eligible') is not False
                or request.get('target_provision') != ('SG25' if key.startswith('SG25_') else key)
                or request.get('expected_rule') != (_SHIP if key.startswith('SG') else _STOWAGE)
                or request.get('expected_mask_rule') != request.get('expected_rule')):
            raise ValueError('context request has a mismatched identity or rule')
        requests[key] = request
    return {**catalog, 'requests': requests, 'fixture_source_hashes': pins}


def _sources(value):
    if isinstance(value, Mapping):
        if 'provenance' in value:
            yield value['provenance']
        for key, child in value.items():
            if key != 'provenance':
                yield from _sources(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _sources(child)


def _register(registry, evidence, context, allowed_slots):
    if not isinstance(evidence, Mapping) or any(
            not isinstance(evidence.get(key), str) or not evidence[key].strip()
            for key in ('document', 'clause', 'scenario_id')):
        raise ValueError('authored canary evidence requires document, clause and scenario_id')
    record = asdict(context)
    record.pop('slot')
    record.update(document=evidence['document'], clause=evidence['clause'],
                  allowed_slots=allowed_slots, synthetic=True, scope='canary_declared_context')
    key = evidence['scenario_id']
    if key not in registry:
        registry[key] = []
    elif isinstance(registry[key], Mapping):
        registry[key] = [registry[key]]  # Preserve the existing whole registration.
    if not isinstance(registry[key], list):
        if isinstance(registry[key], tuple):
            registry[key] = list(registry[key])
        else:
            raise ValueError('existing scenario registrations are malformed')
    if record not in registry[key]:
        registry[key].append(record)
    if not provenance_matches(evidence, context, registry):
        raise ValueError('canary evidence did not resolve to its actual complete context')


def _roles(instance, request, role_container_ids):
    cargo = request['cargo']
    if not isinstance(cargo, Mapping) or not cargo:
        raise ValueError('canary request needs authored cargo roles')
    candidates = [box for box in instance.containers if box.length_ft == 20 and not box.reefer
                  and box.oog is None and not box.empty]
    if role_container_ids is None:
        if len(candidates) < len(cargo):
            raise ValueError('slice needs enough ordinary twenty-foot decision boxes for the requested roles')
        selected = dict(zip(cargo, candidates))
    else:
        by_id = {box.id: box for box in candidates}
        if set(role_container_ids) != set(cargo) or len(set(role_container_ids.values())) != len(cargo):
            raise ValueError('every canary role needs its own actual container')
        selected = {role: by_id[cid] for role, cid in role_container_ids.items()}
    return {role: box.id for role, box in selected.items()}


def materialize_context_pair(instance: Instance, canary_id: str, *, catalog=None,
                             role_container_ids=None) -> dict:
    """Author two context-only Instance JSONs; never fabricate plans or actions.

    Decision cargo is reconstructed from the requested roles using real input
    container IDs. Its mass/consignment and initial onboard anchors are part of
    the conditioning request, so any old qualification stamp is not evidence for
    the result. Hull, tanks, fixed background, DoC, port, rotation and voyage are
    untouched. Call AFTER selecting the physical slice; qualify it afterwards.
    """
    catalog = load_context_catalog() if catalog is None else deepcopy(catalog)
    if canary_id not in catalog.get('requests', {}):
        raise ValueError('unknown DG canary context: ' + canary_id)
    if tuple(int(part) for part in instance.schema_version.split('.')) < (1, 9):
        raise ValueError('DG canary contexts require current schema semantics')
    request = catalog['requests'][canary_id]
    parent = instance.vessel.slice_of or instance.vessel.id.split('.')[0]
    allowed = request.get('compatible_vessels', [catalog['reference_vessel']])
    if parent not in allowed:
        raise ValueError('request layout has not been authored for this physical vessel')
    if instance.onboard.placements:
        raise ValueError('author the initial canary anchor before qualification; existing decision stow cannot be discarded')
    role_ids = _roles(instance, request, role_container_ids)
    raw_metadata = (instance.vessel.imdg_placement_geometry.model_dump(mode='python')
                    if instance.vessel.imdg_placement_geometry is not None else {})
    design_evidence = [raw_metadata.get('living_quarters_map_provenance'), raw_metadata.get('heat_map_provenance')]
    design_evidence.extend(row.get('provenance') for row in raw_metadata.get('heat_sources', []))
    cells = {cell.id: cell for cell in instance.vessel.cells}
    stacks = {stack.id: stack for stack in instance.vessel.stacks}
    result = dict(name=request['name'], request_id=canary_id, contract=catalog['contract'],
                  target_provision=request['target_provision'], expected_mask_rule=request['expected_mask_rule'],
                  expected_rule=request['expected_rule'], expected_messages=request['expected_messages'],
                  status='context_only', evidence_eligible=False, role_container_ids=role_ids,
                  fixture_source_hashes=catalog['fixture_source_hashes'],
                  required_qualification=catalog['qualification_requirements'])
    for variant, selector in (('baseline', 'baseline_request'), ('defect', 'defect_request')):
        layout = request[selector]
        if (set(layout['placements']) != set(role_ids) or layout['candidate_role'] not in role_ids
                or not set(layout['onboard_roles']) < set(role_ids)
                or layout['candidate_role'] in layout['onboard_roles']):
            raise ValueError('layout must declare one candidate and its initial onboard roles')
        bound = instance.model_copy(deep=True)
        original_boxes = {box.id: box for box in bound.containers}
        bound.containers = [original_boxes[cid] for cid in role_ids.values()]
        config = bound.rule_overrides.setdefault(_STOWAGE, {})
        if not isinstance(config, dict):
            raise ValueError('IMDG stowage facts must be a mapping')
        registry = config.setdefault('assessment_scenarios', {})
        if not isinstance(registry, dict):
            raise ValueError('scenario registry must be a mapping')
        container_facts = config.setdefault('containers', {})
        if not isinstance(container_facts, dict):
            raise ValueError('container facts must be a mapping')
        ship_config = bound.rule_overrides.setdefault(_SHIP, {})
        ship_type = catalog['vessel_facts']['ship_type']
        if not isinstance(ship_config, dict) or ship_config.get('ship_type', ship_type) != ship_type:
            raise ValueError('authored vessel classification conflicts with existing evidence')
        ship_config['ship_type'] = ship_type
        placements = []
        for role, declaration in request['cargo'].items():
            box = original_boxes[role_ids[role]]
            if box.max_gross_mass_kg is None or declaration['gross_mass_kg'] > box.max_gross_mass_kg:
                raise ValueError('canary booking requires an actual adequate CSC plate rating')
            if box.height_m != declaration['height_m'] or box.length_ft != declaration['length_ft']:
                raise ValueError('request must retain actual container external dimensions')
            box.gross_mass_kg = declaration['gross_mass_kg']
            box.special_stowage = list(catalog['booking_defaults']['special_stowage'])
            entry = lookup_dgl(int(declaration['un_number']), declaration['packing_group'])
            if bound.port.requires_direct_delivery(entry.hazard_class):
                raise ValueError('these ordinary-yard canaries cannot bypass an actual direct-delivery requirement')
            box.direct_delivery = False
            facts = deepcopy(declaration['facts'])
            facts.update(deepcopy(layout.get('facts', {}).get(role, {})))
            if set(facts) - _ASSESSMENTS:
                raise ValueError('canary authoring cannot add protection/verification flags')
            dg = DangerousGoods(un_number=declaration['un_number'], imdg_class=entry.hazard_class,
                packing_group=entry.packing_group, stowage_category=entry.stowage_category,
                subsidiary_risks=list(entry.subsidiary_risks), stowage_codes=list(entry.stowage_codes),
                segregation_codes=list(entry.segregation_codes), **catalog['shipment_defaults'])
            if isinstance(facts.get('sw19'), Mapping):
                battery = facts['sw19']
                applicability = battery['special_provision_applicability']
                dg.special_provisions = [code for code in ('376', '377') if applicability[code]]
                if isinstance(battery.get('voyage'), Mapping):
                    dg.short_international_voyage = battery['voyage']['short_international_voyage']
            box.dg = dg
            container_facts[box.id] = facts
            slot = layout['placements'][role]
            for cid in cells_of_slot(slot):
                if cid not in cells or not cells[cid].accepts_20:
                    raise ValueError('physical slice must retain every requested twenty-foot decision cell: ' + slot)
                if stacks[cells[cid].stack_id].cell_ids[0] != cid:
                    raise ValueError('these fixtures require actual ground-supported placements')
            if role in layout['onboard_roles'] and not parse_slot(slot).on_deck:
                cover = stacks[cells[slot].stack_id].hatch_cover_id
                if cover is None or cover in bound.onboard.covers_off:
                    raise ValueError('initial H1 hold anchor requires its actual secured cover')
            context = context_for(bound, box, 'primary', slot)
            if context is None:
                raise ValueError('fixture needs its actual instance/entry/vessel and declared future voyage leg')
            # These are consignment/design declarations, not location approvals.
            # Bind every compatible decision footprint, as the shipment author
            # does, so C1/C2 can find alternate legal placements rather than
            # mistaking artificially unregistered alternatives for infeasibility.
            allowed_slots = sorted(cell.id for cell in bound.vessel.cells if cell.accepts_20)
            for evidence in [*design_evidence, *_sources(facts)]:
                if evidence is not None:
                    _register(registry, evidence, context, allowed_slots)
            placements.append(Placement(slot=slot, container_id=box.id))
        onboard_ids = {role_ids[role] for role in layout['onboard_roles']}
        candidate_id = role_ids[layout['candidate_role']]
        bound.onboard.placements = [p for p in placements if p.container_id in onboard_ids]
        bound.load_list = [candidate_id]
        bound.discharge_list = []
        for stack in bound.yard.stacks:
            stack.boxes = [cid for cid in stack.boxes if cid == candidate_id]
        bound.yard.stacks = [stack for stack in bound.yard.stacks if stack.boxes]
        bound.yard.quay_buffer = [cid for cid in bound.yard.quay_buffer if cid == candidate_id]
        # Model validation catches a candidate absent from the supplied real yard,
        # duplicate locations, invalid reference footprints and service flags.
        bound = Instance.model_validate(bound.model_dump(mode='python'))
        result[variant] = dict(input=bound.model_dump(mode='json'),
            placements=[p.model_dump(mode='json') for p in placements],
            candidate_container_id=candidate_id, candidate_slot=layout['placements'][layout['candidate_role']],
            expectation='accept' if variant == 'baseline' else 'deny', status='context_only')
    return result


def main(argv=None):
    """Emit context JSON from a current preparation input; no numerical job runs."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance', type=Path, required=True)
    parser.add_argument('--case', choices=sorted(_IDS), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--catalog', type=Path)
    args = parser.parse_args(argv)
    instance = Instance.model_validate_json(args.instance.read_bytes())
    pair = materialize_context_pair(instance, args.case, catalog=load_context_catalog(args.catalog))
    args.output.write_text(json.dumps(pair, sort_keys=True, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
