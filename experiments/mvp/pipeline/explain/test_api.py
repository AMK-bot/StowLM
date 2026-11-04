from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.contract.schema import MoveVerdict, sha256_json
from experiments.mvp.pipeline.explain.api import explain_box, inputs, statement_menu
from experiments.mvp.pipeline.explain.render import render, validate_protected_tokens
from stowage.model import Instance, MovePlan, CheckReport
from stowage.moves import apply_plan

FIXTURES = Path(__file__).with_name('fixtures') / 'development'


@pytest.fixture
def checked():
    return tuple(model.model_validate(json.loads((FIXTURES / f'{name}.json').read_text()))
                 for name, model in [('instance', Instance), ('plan', MovePlan), ('report', CheckReport)])


def test_all_boxes_bilingual_and_evidence_resolves(checked):
    instance, plan, report = checked
    replay = apply_plan(instance, plan).final_state.ship
    sources = {name: value.model_dump(mode='json') for name, value in
               dict(instance=instance, plan=plan, report=report, replay=replay).items()}
    hashes = {name: sha256_json(value) for name, value in sources.items()}
    for placement in replay.placements:
        menu = statement_menu(*checked, placement.container_id)
        assert {'position', 'checker', 'dg'} <= {s['id'] for s in menu}
        assert any(s['id'].startswith('weight:') for s in menu)
        assert any(s['id'].startswith('pod:') for s in menu)
        thai, audit = render(menu, 'th')
        assert audit['fallbacks'] == []
        for en, th in zip(menu, thai):
            assert validate_protected_tokens(en['text'], th['text'])
            for link in en['evidence']:
                value = sources[link['source']]
                assert hashes[link['source']] == link['sha256']
                for key in link['path'].lstrip('/').split('/'):
                    value = value[int(key)] if isinstance(value, list) else value[key.replace('~1', '/').replace('~0', '~')]
                assert value == link['value']


def test_bound_report_rejects_same_id_plan_edit(checked):
    instance, plan, report = checked
    plan = plan.model_copy(deep=True)
    plan.moves = plan.moves[:-1]
    with pytest.raises(ValueError, match='bound'):
        statement_menu(instance, plan, report, instance.load_list[0])


def test_bound_report_rejects_same_id_instance_edit(checked):
    instance, plan, report = checked
    instance = instance.model_copy(deep=True)
    instance.containers[0].gross_mass_kg += 1
    with pytest.raises(ValueError, match='bound'):
        inputs(instance, plan, report)


def test_unknown_and_bad_language(checked):
    with pytest.raises(ValueError, match='Unknown box'):
        statement_menu(*checked, 'NO-SUCH-BOX')
    with pytest.raises(ValueError, match='language'):
        explain_box(*checked, checked[0].load_list[0], language='fr')


def test_sandbox_evidence_is_actual_call(checked, monkeypatch):
    calls = []
    def try_move(instance, plan, box_id, slot):
        calls.append((box_id, slot))
        return MoveVerdict(legal=False, screen='fast', broken=[{
            'rule_id': 'expert.physical.stack_weight', 'message': 'Stack exceeds its mass limit.',
            'measured': 40000, 'limit': 30000, 'unit': 'kg', 'boxes': [box_id]}],
            extra_lifts=2, wall_s=0.)
    monkeypatch.setitem(sys.modules, 'experiments.mvp.pipeline.sandbox', SimpleNamespace(try_move=try_move))
    box = checked[0].load_list[0]
    menu = statement_menu(*checked, box, what_if={'target_slot': '0010182'})
    assert calls == [(box, '0010182')]
    moved = next(s for s in menu if s['id'] == 'what_if')
    assert moved['evidence'][0]['value']['screen'] == 'fast'
    assert 'fast screen' in moved['text']
    assert 'not a full-checker verdict' in moved['text']
    assert moved['evidence'][0]['plan_sha256'] == sha256_json(checked[1].model_dump(mode='json'))
    assert any(s['id'] == 'what_if:broken:0' for s in menu)
    assert '40000 kg' in next(s for s in menu if s['id'] == 'what_if:broken:0')['text']
    assert any(s['id'] == 'what_if:lifts' for s in menu)
    thai, audit = render(menu, 'th')
    assert not audit['fallbacks']
    for en, th in zip(menu, thai):
        assert validate_protected_tokens(en['text'], th['text'])


def test_real_sandbox_invalid_target_is_not_checked(checked):
    menu = statement_menu(*checked, checked[0].load_list[0], what_if='bad-slot')
    moved = next(s for s in menu if s['id'] == 'what_if')
    assert 'was not checked: invalid request' in moved['text']
    assert moved['evidence'][0]['value']['gauges']['input_error'] is True


def test_invalid_what_if_is_not_model_verdict(checked):
    with pytest.raises(ValueError, match='target slot'):
        statement_menu(*checked, checked[0].load_list[0], what_if={'legal': True})


def test_worker_reuses_real_verdict_and_quotes_both_lift_deltas(checked, monkeypatch):
    from experiments.mvp.pipeline.explain.api import explain_move
    def forbidden(*args, **kwargs):
        pytest.fail('Worker adapter must not repeat a completed checker call')
    monkeypatch.setitem(sys.modules, 'experiments.mvp.pipeline.sandbox', SimpleNamespace(try_move=forbidden))
    verdict = MoveVerdict(legal=True, screen='full', broken=[], extra_lifts=2, wall_s=.1,
                          gauges={'lift_counts': {'projected_later_delta': 4}})
    for language in ('en', 'th'):
        answer = explain_move(*checked, checked[0].load_list[0], '0010182', verdict, language=language)
        assert answer.mode.value == 'live' and 'no model summary' in answer.text
        rows = {s['id']: s for s in answer.statements}
        assert rows['what_if']['evidence'][0]['value'] == verdict.model_dump(mode='json')
        assert 'extra_lifts' in rows['what_if:lifts']['text']
        assert 'projected_later_delta' in rows['what_if:lifts']['text']
        assert '2' in rows['what_if:lifts']['text'] and '4' in rows['what_if:lifts']['text']


def test_incomplete_report_never_claims_pass(checked):
    instance, plan, report = checked
    report = report.model_copy(deep=True)
    report.parts = {}
    menu = statement_menu(instance, plan, report, instance.load_list[0])
    assert 'does not establish' in next(s for s in menu if s['id'] == 'checker')['text']


def test_missing_condition_evidence_never_claims_full_pass(checked):
    instance, plan, report = checked
    report = report.model_copy(deep=True)
    report.parts = {k: v for k, v in report.parts.items() if not k.startswith('condition:')}
    menu = statement_menu(instance, plan, report, instance.load_list[0])
    assert 'does not establish' in next(s for s in menu if s['id'] == 'checker')['text']


def test_no_local_dg_margin_invented(checked):
    menu = statement_menu(*checked, checked[0].load_list[0])
    dg = next(s for s in menu if s['id'] == 'dg')
    assert ('no numeric DG segregation margin' in dg['text']
            or 'not box-specific margins' in dg['text'])


def test_weight_margins_follow_support_shares(checked):
    instance, plan, report = checked
    replay = apply_plan(instance, plan).final_state.ship
    from stowage.conventions import cells_of_slot
    boxes = {b.id: b for b in instance.containers}
    box = replay.placements[0].container_id
    for s in statement_menu(*checked, box):
        if s['id'].startswith('weight:'):
            sid = s['id'].split(':', 1)[1]
            stack = next(stack for stack in instance.vessel.stacks if stack.id == sid)
            mass = sum(boxes[p.container_id].gross_mass_kg / len(cells_of_slot(p.slot))
                       for p in replay.placements if set(cells_of_slot(p.slot)) & set(stack.cell_ids))
            assert f'planned column mass {mass:g} kg' in s['text']
