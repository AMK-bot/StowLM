"""Seeded property checks of full reports, using the model's synthetic builder.

These are random plans on a deliberately passing synthetic loading condition,
not a claim that arbitrary stows are safe.
A property is a condition tested across many inputs (Hypothesis terminology):
here reproducibility, input purity, and detection of one introduced fault.
Two seeds per property/fault run by default; the remaining seeds use ``slow``.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from scipy.optimize import brentq

import pytest

from stowage.conventions import CONTAINER_HEIGHT_STD_M, CONTAINER_LENGTH_M, CONTAINER_WIDTH_M
from stowage.checker.full import check
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.model import (BallastCondition, DgDocArea, FixedLoad,
                          FixedLoadSilhouette, Instance, MovePlan,
                          VoyageConditionIdentity, VoyageSpec)
from stowage.model.cargo import DangerousGoods
from stowage.model.provenance import Provenance
from stowage.model.fixtures import build_instance
from stowage.model.moves import Load
from stowage.moves import apply_plan
from stowage.rules.imdg.dgl import lookup_dgl
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.fleet_slices import slice_vessel
from stowage.vessel.fleet_conditions import CONDITION_PREFIX, slice_condition, _deck_background_loads


def _random_plan(seed):
    rng = random.Random(seed)
    instance = build_instance()
    # Production builders retain mandatory D40 policy and D42 box/support evidence.
    instance.vessel = slice_vessel(load_vessel('feeder_800'), [1, 3], name='property.slice')
    hull = instance.vessel.hull
    instance.onboard.vessel_id = instance.vessel.id
    instance.onboard.tank_masses_kg = {tank.id: 0. for tank in instance.vessel.tanks}
    instance.port.max_draught_m = hull.max_draught_m
    # D25 uses catalogue weight classes and rank tolerance, not legacy kg overrides.
    instance.rule_overrides = {}
    for box in instance.containers:
        box.length_ft, box.iso_type = 20, '22G1'
        box.height_m, box.tare_kg = 2.591, 2200
        box.gross_mass_kg = rng.randint(6000, 18000)
        box.reefer, box.reefer_setpoint_c = False, None
        box.dg, box.oog, box.special_stowage = None, None, []
        box.pod = 'THBKK'
    for stack in instance.yard.stacks:
        stack.length_ft = 20
    slots = ['0010182', '0030182', '0010282', '0030282']
    rng.shuffle(slots)
    yards = {cid: stack for stack in instance.yard.stacks for cid in stack.boxes}
    jobs = [(box, yards[box.id], slot) for box, slot in zip(instance.containers, slots)]
    rng.shuffle(jobs)
    moves = [Load(call_index=0, container_id=box.id, src=f'{yard.key}-01', dst=slot)
             for box, yard, slot in jobs]
    plan = MovePlan(id=f'random.{seed}', instance_id=instance.id, call_index=0,
                    moves=moves, ballast=BallastCondition(tank_masses_kg={}, source='planner'))
    # APPROXIMATION: homogeneous 12 t background on production support geometry.
    # Rebuild points with the production aggregation helper and regenerate the
    # actual cell/stack mass audit. No physical limit or evidence gate is relaxed.
    audit = slice_condition(instance.vessel)
    support = {cell.id: cell for cell in instance.vessel.fixed_load_cells}
    stacks = {stack.id: stack for stack in instance.vessel.fixed_load_stacks}
    records = {box.cell_ids[0]: box for load in instance.vessel.fixed_loads for box in load.cargo_boxes}
    points, deck = [], {}
    for row in audit['background_cells']:
        row['mass_kg'] = 12000.
        cell = support[row['cell_id']]
        box = records[cell.id].model_copy(deep=True)
        box.gross_mass_kg = 12000.
        box.provenance.parents.append('APPROXIMATION: homogeneous 12 t property fixture')
        silhouette = None
        if stacks[cell.stack_id].on_deck:
            silhouette = FixedLoadSilhouette(x_min_m=cell.lcg_m-CONTAINER_LENGTH_M[20]/2, x_max_m=cell.lcg_m+CONTAINER_LENGTH_M[20]/2,
                y_min_m=cell.tcg_m-CONTAINER_WIDTH_M/2, y_max_m=cell.tcg_m+CONTAINER_WIDTH_M/2,
                z_min_m=cell.vcg_base_m, z_max_m=cell.vcg_base_m+CONTAINER_HEIGHT_STD_M)
        point = FixedLoad(x_m=cell.lcg_m, y_m=cell.tcg_m, z_m=cell.vcg_base_m+CONTAINER_HEIGHT_STD_M/2,
                          mass_kg=12000., kind='cargo', silhouette=silhouette, cargo_boxes=[box])
        if stacks[cell.stack_id].on_deck:
            deck.setdefault(cell.stack_id, []).append(point)
        else:
            points.append(point)
    points.extend(_deck_background_loads(deck[sid]) for sid in sorted(deck))
    instance.vessel.fixed_loads = points
    for row in audit['background_stacks']:
        row['mass_kg'] = sum(cell['mass_kg'] for cell in audit['background_cells']
                             if cell['stack_id'] == row['stack_id'])
    background_mass = sum(load.mass_kg for load in points)
    cargo_mass = sum(box.gross_mass_kg for box in instance.containers)
    displacement = hull.lightship_mass_kg+background_mass+cargo_mass
    audit.update(fixed_cargo_mass_kg=background_mass, fixed_load_mass_kg=background_mass,
                 expected_load_mass_kg=cargo_mass, consumables_mass_kg=0.,
                 expected_displacement_kg=displacement, target_displacement_kg=displacement,
                 cargo_t_per_teu=12., cargo_scale=1., gm_screen=None, ballast_moment_reserve=None,
                 booking_reference=None, expected_load_policy='actual_property_booking_mass',
                 expected_load_t_per_teu=cargo_mass/len(instance.containers)/1000,
                 unscaled_decision_c3_mass_kg=None,
                 achieved_design_band=.9*audit['design_displacement_kg'] <= displacement <= audit['design_displacement_kg'],
                 implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 departure_policy='synthetic_homogeneous_property_fixture',
                 source='APPROXIMATION: homogeneous 12 t property fixture')
    instance.vessel.sources = [source for source in instance.vessel.sources
                               if not source.startswith(CONDITION_PREFIX)]
    instance.vessel.sources.append(CONDITION_PREFIX+json.dumps(audit, sort_keys=True))
    # APPROXIMATION: this test ship has an explicitly designed lightship KG
    # of 4 m and even-keel LCG, with a consistent two-block mass distribution.
    # The unchanged production operating/strength limits still decide acceptance.
    geometry = StabilityGeometry(load_ship_params('feeder_800'))
    draught = brentq(lambda d: geometry.full_submerged(d, d).displacement_kg-displacement,
                     1., hull.max_draught_m)
    submerged = geometry.full_submerged(draught, draught)
    boxes = {box.id: box for box in instance.containers}
    cells = {cell.id: cell for cell in instance.vessel.cells}
    cargo_x = sum(boxes[m.container_id].gross_mass_kg*cells[m.dst].lcg_m for m in moves)
    hull.lightship_lcg_m = (displacement*submerged.lcb_m
        -sum(load.mass_kg*load.x_m for load in points)-cargo_x)/hull.lightship_mass_kg
    hull.lightship_vcg_m = 4.
    forward_mass = hull.lightship_mass_kg*(hull.lightship_lcg_m-hull.lpp_m/4)/(hull.lpp_m/2)
    hull.lightship_distribution = [(0., hull.lpp_m/2, hull.lightship_mass_kg-forward_mass),
                                   (hull.lpp_m/2, hull.lpp_m, forward_mass)]
    instance.vessel.sources.append('APPROXIMATION: synthetic property lightship KG4; balanced LCG and two-block distribution')
    # Every actual consumable tank is explicitly empty above. The production
    # policy and default draw-down therefore give the same two endpoint fills.
    evidence = Provenance(created_by='test_full_properties.synthetic_voyage',
        parents=['DECLARATION: synthetic next-call environment and seawater density'])
    instance.voyage = VoyageSpec(**{
        phase: VoyageConditionIdentity(
            id=f'property.{seed}.{phase}', phase=phase, call_index=index,
            locode=instance.rotation.calls[index].locode,
            environment_id=f'property.{phase}', density_id='synthetic-seawater-1025',
            water_density_kg_m3=instance.port.water_density_kg_m3,
            provenance=evidence)
        for index, phase in enumerate(('departure', 'arrival'))}, provenance=evidence)
    return Instance.model_validate(instance.model_dump()), plan


def test_property_fixture_keeps_loading_policy_and_background_evidence():
    instance, _ = _random_plan(0)
    for field in ('min_trim_m', 'max_trim_m', 'min_displacement_kg',
                  'max_displacement_kg', 'bilge_keel_area_m2'):
        assert getattr(instance.vessel.hull, field) is not None
        evidence = getattr(instance.vessel.hull, field+'_provenance')
        assert evidence is not None and (evidence.source or evidence.assumption)
    assert instance.vessel.fixed_load_cells and instance.vessel.fixed_load_stacks
    assert instance.vessel.fixed_loads
    assert all(load.kind == 'cargo' and load.cargo_boxes for load in instance.vessel.fixed_loads)
    audit = slice_condition(instance.vessel)
    assert audit['cargo_t_per_teu'] == 12.
    assert audit['fixed_cargo_mass_kg'] == sum(load.mass_kg for load in instance.vessel.fixed_loads)
    assert audit['booking_reference'] is None
    assert instance.vessel.voyage_policy is not None
    assert {tank_id for group in instance.vessel.voyage_policy.groups
            for tank_id in group.tank_ids} == {
                tank.id for tank in instance.vessel.tanks
                if tank.kind in {'fuel_oil', 'diesel_oil', 'lube_oil', 'fresh_water', 'stores'}}
    assert instance.voyage is not None and instance.voyage.consumption is None


def _seed_cases(count):
    """Retain a small default smoke sample without losing the larger slow corpus."""
    return [seed if seed < 2 else pytest.param(seed, marks=pytest.mark.slow)
            for seed in range(count)]


@pytest.mark.parametrize('seed', _seed_cases(12))
def test_random_legal_plan_reports_are_deterministic_and_pure(seed):
    instance, plan = _random_plan(seed)
    assert apply_plan(instance, plan).legal
    before = instance.model_dump_json(), plan.model_dump_json()
    first, second = check(instance, plan), check(instance, plan)
    assert first == second
    assert (instance.model_dump_json(), plan.model_dump_json()) == before
    assert set(first.parts) >= {'moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints'}
    assert {f'condition:property.{seed}.{phase}:{part}'
            for phase in ('departure', 'arrival') for part in ('stability', 'strength')} <= set(first.parts)
    assert all(part.ran for part in first.parts.values()), {name: part.error for name, part in first.parts.items() if not part.ran}
    assert first.count.loads == 4
    assert first.passed, first.violations
    assert first.passed == (not any(v.severity == 'hard' for v in first.violations))
    assert not first.shut_out


def _dg(un_number, packing_group=None):
    entry = lookup_dgl(un_number, packing_group)
    return DangerousGoods(un_number=f'{un_number:04d}', imdg_class=entry.hazard_class,
                          subsidiary_risks=list(entry.subsidiary_risks), packing_group=packing_group,
                          stowage_category=entry.stowage_category,
                          stowage_codes=list(entry.stowage_codes),
                          segregation_codes=list(entry.segregation_codes),
                          packing_form='packaged_goods_in_closed_freight_containers',
                          ctu_closed=True, clear_living_quarters=True, dry=True,
                          handling_compliant=True,
                          special_provisions=list(entry.special_provisions),
                          shipment_provenance=Provenance(
                              created_by='synthetic full-check property fixture'))


@pytest.mark.parametrize('seed', _seed_cases(6))
@pytest.mark.parametrize(('fault', 'rule_id'), [
    ('overweight', 'expert.physical.stack_weight'),
    ('dg_pair', 'imdg.segregation.7_4_3'),
    ('unplugged_reefer', 'expert.physical.reefer_plug'),
    ('missing_box', 'objective.all_loaded'),
])
def test_one_injected_fault_is_caught_with_catalogue_id(seed, fault, rule_id):
    instance, plan = _random_plan(seed)
    base = check(instance, plan)
    assert base.passed, base.violations
    target = plan.moves[0]
    box = next(c for c in instance.containers if c.id == target.container_id)
    if fault == 'overweight':
        stack_id = next(c.stack_id for c in instance.vessel.cells if c.id == target.dst)
        stack = next(s for s in instance.vessel.stacks if s.id == stack_id)
        stack.max_mass_kg = stack.max_mass_20_kg = box.gross_mass_kg - 1
    elif fault == 'unplugged_reefer':
        box.reefer, box.reefer_setpoint_c = True, -18
        next(c for c in instance.vessel.cells if c.id == target.dst).reefer_plug = False
    elif fault == 'missing_box':
        plan.moves = plan.moves[1:]
    else:
        # Same bay, adjacent rows: 2.5 m < the required level-2 clearance.
        first = next(m for m in plan.moves if m.dst == '0010182')
        second = next(m for m in plan.moves if m.dst == '0010282')
        a, b = (next(c for c in instance.containers if c.id == m.container_id) for m in (first, second))
        a.dg, b.dg = _dg(1057), _dg(1202, 'III')
        for stack in instance.vessel.stacks:
            stack.dg_classes_allowed = ['2.1', '3']
            stack.dg_doc_area_id = 'synthetic-test-area'
        # APPROXIMATION: synthetic authorization for this deliberately permitted
        # pair; no real vessel's certified DoC is implied by this fixture.
        instance.vessel.dg_doc_areas = [DgDocArea(
            id='synthetic-test-area', stack_ids=[s.id for s in instance.vessel.stacks],
            classes_allowed=['2.1', '3'],
            packing_forms_allowed=['packaged_goods_in_closed_freight_containers'],
            assumption='APPROXIMATION: synthetic test DoC authorization')]
        instance.rule_overrides['imdg.segregation.7_4_3'] = {
            'ship_type': 'closed_holds',
            'slots': {m.dst: {'closed': True, 'hold_id': 'one'} for m in (first, second)},
            'pairs': {'|'.join(sorted((first.dst, second.dst))): {
                'bulkheads': 0, 'barriers_resistant_to_fire_and_liquids': True,
                'distance_from_intervening_bulkhead_m': 0}},
        }
    report = check(instance, plan)
    assert not report.passed
    assert any(v.rule_id == rule_id and v.severity == 'hard' for v in report.violations)
    assert not any(v.rule_id == 'checker.internal_error' for v in report.violations)


@pytest.mark.parametrize('seed', _seed_cases(12))
def test_random_illegal_plans_never_raise(seed):
    instance, plan = _random_plan(seed)
    rng = random.Random(seed)
    mode = rng.choice(['duplicate_load', 'wrong_source', 'occupied_slot'])
    if mode == 'duplicate_load':
        plan.moves.append(plan.moves[rng.randrange(4)].model_copy())
    elif mode == 'wrong_source':
        plan.moves[0] = plan.moves[0].model_copy(update={'src': 'A-006-02-03'})
    else:
        plan.moves[1] = plan.moves[1].model_copy(update={'dst': plan.moves[0].dst})
    replay = apply_plan(instance, plan)
    assert not replay.legal
    report = check(instance, plan)
    assert not report.passed
    replay_ids = {v.rule_id for v in replay.violations}
    assert replay_ids <= {v.rule_id for v in report.violations if v.severity == 'hard'}
    assert set(report.parts) >= {'stability', 'strength', 'visibility', 'lashing'}
