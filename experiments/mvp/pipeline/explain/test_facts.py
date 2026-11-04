"""Development-only source fixture tests; it is not a demo recording."""
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.pipeline.explain.facts import additional_facts
from experiments.mvp.pipeline.explain.render import validate_protected_tokens
from stowage.conventions import cells_of_slot, parse_slot
from stowage.model import CheckReport, Instance, MovePlan
from stowage.moves import apply_plan


@pytest.fixture(scope='module')
def checked_facts():
    folder = Path(__file__).with_name('fixtures') / 'development'
    instance, plan, report = (model.model_validate(json.loads((folder / f'{name}.json').read_text()))
                              for name, model in [('instance', Instance), ('plan', MovePlan), ('report', CheckReport)])
    return instance, plan, report, apply_plan(instance, plan)


def menu(values, box):
    instance, plan, report, replay = values
    return {row['id']: row for row in additional_facts(instance, plan, report, box, replay)}


def aboard(values):
    ids = {p.container_id for p in values[3].final_state.ship.placements}
    return [b for b in values[0].containers if b.id in ids]


def test_every_box_has_distinguishing_rank_and_bound_evidence(checked_facts):
    instance, plan, report, replay = checked_facts
    sources = {name: obj.model_dump(mode='json') for name, obj in
               [('instance', instance), ('plan', plan), ('report', report), ('replay', replay.final_state.ship)]}
    hashes = {name: sha256_json(value) for name, value in sources.items()}
    for position in replay.final_state.ship.placements:
        rows = menu(checked_facts, position.container_id)
        assert {'cargo', 'bay_profile', 'restow'} <= rows.keys()
        assert any(key.startswith('rank:') for key in rows)
        for row in rows.values():
            assert validate_protected_tokens(row['text'], row['th']), row
            assert validate_protected_tokens(row['summary_en'], row['summary_th']), row
            assert not row['summary_en'].endswith('.')
            for link in row['evidence']:
                value = sources[link['source']]
                assert hashes[link['source']] == link['sha256']
                for key in link['path'].lstrip('/').split('/'):
                    value = value[int(key)] if isinstance(value, list) else value[key.replace('~1', '/').replace('~0', '~')]
                assert value == link['value']


def test_weight_rank_and_bay_mean_are_rederived(checked_facts):
    instance, plan, report, replay = checked_facts
    positions = replay.final_state.ship.placements
    boxes = {b.id: b for b in instance.containers}
    for position in positions:
        box = boxes[position.container_id]
        bays = {parse_slot(cid).bay for cid in cells_of_slot(position.slot)}
        members = [boxes[p.container_id] for p in positions
                   if any(parse_slot(cid).bay in bays for cid in cells_of_slot(p.slot))]
        rank = 1 + sum(b.gross_mass_kg > box.gross_mass_kg for b in members)
        mean = sum(b.gross_mass_kg for b in members) / len(members)
        row = menu(checked_facts, box.id)['bay_profile']
        assert f'weight rank {rank} of {len(members)}' in row['text']
        assert f'mean {mean:g} kg' in row['text']


def test_cargo_includes_dg_reefer_and_empty(checked_facts):
    instance, _, _, _ = checked_facts
    for box in aboard(checked_facts):
        row = menu(checked_facts, box.id)['cargo']['text']
        assert f'ISO {box.iso_type}' in row
        assert f'gross mass {box.gross_mass_kg:g} kg' in row
        assert f'reefer={str(box.reefer).lower()}' in row
        assert f'empty={str(box.empty).lower()}' in row
        if box.dg:
            assert f'class {box.dg.imdg_class}, UN {box.dg.un_number}' in row


def test_non_operating_reefer_describes_plug_requirement_not_container_type(checked_facts):
    instance, plan, report, replay = checked_facts
    changed = instance.model_copy(deep=True)
    box_id = aboard(checked_facts)[0].id
    index = next(i for i, box in enumerate(changed.containers) if box.id == box_id)
    cargo = changed.containers[index].model_dump(mode='json')
    cargo.update(iso_type='45R1', length_ft=40, height_m=2.896, reefer=False, reefer_setpoint_c=None)
    # Container permits an unplugged/non-operating reefer. No changed plan is
    # checked or presented here; this isolates the cargo-description function.
    changed.containers[index] = type(changed.containers[index]).model_validate(cargo)
    row = menu((changed, plan, report, replay), box_id)['cargo']
    assert 'ISO 45R1' in row['text'] and 'reefer=false' in row['text']
    assert 'ISO 45R1' in row['th']
    assert 'ไม่ต้องใช้ปลั๊กสำหรับควบคุมอุณหภูมิ' in row['th']
    assert 'ไม่ใช่ตู้ควบคุมอุณหภูมิ' not in row['th']
    assert validate_protected_tokens(row['text'], row['th'])


def test_reefer_free_plug_count_rederived(checked_facts):
    instance, _, _, replay = checked_facts
    occupied = {cid for p in replay.final_state.ship.placements for cid in cells_of_slot(p.slot)}
    box = next(b for b in aboard(checked_facts) if b.reefer)
    plugs = [c for c in instance.vessel.cells if c.reefer_plug and getattr(c, f'accepts_{box.length_ft}')]
    free = sum(c.id not in occupied for c in plugs)
    assert f'{free} of {len(plugs)}' in menu(checked_facts, box.id)['reefer_capacity']['text']


def test_dg_pass_requires_completed_checker_part(checked_facts):
    instance, plan, report, replay = checked_facts
    box = next(b for b in aboard(checked_facts) if b.dg)
    facts = menu(checked_facts, box.id)
    assert 'imdg.segregation.7_2_4' in facts['dg_rules']['text']
    assert 'reported no violation' in facts['dg_rules']['text']
    assert 'full legality are not screened' in facts['dg_allowed']['text']
    incomplete = report.model_copy(deep=True)
    incomplete.parts['rules'].ran = False
    changed = menu((instance, plan, incomplete, replay), box.id)
    assert 'does not establish' in changed['dg_rules']['text']


def test_restow_warns_about_overstow_and_quotes_replay_projection(checked_facts):
    instance, _, _, replay = checked_facts
    box = aboard(checked_facts)[0]
    # Every box receives a projection, including boxes that are not blockers.
    row = menu(checked_facts, box.id)['restow']['text']
    call = next(c.index for c in instance.rotation.calls if c.index > instance.call_index and c.locode == box.pod)
    count = next(count for count in replay.count.future if count.call_index == call)
    assert 'Projection' in row and 'whole-plan' in row
    assert f'{count.restowed_boxes} restowed boxes, {count.restow_lifts} restow lifts' in row
    assert 'not attributed solely to this box' in row
    assert 'does not construct an executable future policy' in row
    assert any('later POD boxes above it: none' not in menu(checked_facts, b.id)['restow']['text']
               for b in aboard(checked_facts))


def test_restow_rejects_count_that_does_not_rederive(checked_facts):
    instance, plan, report, replay = checked_facts
    wrong = report.model_copy(deep=True)
    wrong.count.future[0].restow_lifts += 2
    assert 'restow' not in menu((instance, plan, wrong, replay), aboard(checked_facts)[0].id)


def test_order_and_dg_evidence_keep_their_cargo_fields(checked_facts):
    instance, _, _, _ = checked_facts
    box = next(b for b in aboard(checked_facts) if b.dg)
    rows = menu(checked_facts, box.id)
    for row in rows.values():
        if row['id'].startswith('pod_block:') or row['id'] == 'restow':
            pod_links = [link for link in row['evidence'] if link['path'].endswith('/pod')]
            assert len(pod_links) == len(aboard(checked_facts))
            assert box.pod in [link['value'] for link in pod_links]
    index = next(i for i, b in enumerate(instance.containers) if b.id == box.id)
    assert any(link['path'] == f'/containers/{index}' and link['value']['dg']
               for link in rows['dg_allowed']['evidence'])


def test_atomic_clauses_work_in_bilingual_summary_slots(checked_facts):
    from experiments.mvp.pipeline.explain.summary import slots_for, validate_summary

    rows = menu(checked_facts, aboard(checked_facts)[0].id)
    slots = slots_for(list(rows.values()))
    ids = ['bay_profile', 'restow']
    summaries = {}
    for language, framing in [('en', 'Consider the comparison'), ('th', 'พิจารณาข้อมูลประกอบ')]:
        spec = {'sentences': [dict(before=framing, slot_id=sid, after='') for sid in ids]}
        summaries[language] = validate_summary(spec, slots, language, paired_ids=ids)
    assert validate_protected_tokens(summaries['en']['text'], summaries['th']['text'])
