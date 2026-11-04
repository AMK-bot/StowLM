from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import MoveVerdict, sha256_json
from experiments.mvp.pipeline.explain import alternatives
from experiments.mvp.pipeline.explain.api import inputs, replay_plan
from experiments.mvp.pipeline.explain.render import validate_protected_tokens
from stowage.conventions import cells_of_slot

FIXTURES = Path(__file__).with_name('fixtures') / 'development'


@pytest.fixture
def checked():
    return tuple(json.loads((FIXTURES / f'{name}.json').read_text()) for name in ('instance', 'plan', 'report'))


def _box(checked):
    instance, plan, report = inputs(*checked)
    placement = replay_plan(instance, plan, report).final_state.ship.placements[0]
    box = next(b for b in instance.containers if b.id == placement.container_id)
    return instance, box, placement.slot


def fake_screen(instance, plan, box_id, target_slot):
    # Different same-size cells lead to genuinely different injected outcomes.
    index = _FAKE_SLOTS.index(target_slot)
    if index < 2:
        return MoveVerdict(legal=False, screen='fast', broken=[
            {'rule_id': 'rule.z', 'severity': 'hard'}, {'rule_id': 'rule.a', 'severity': 'hard'}],
            wall_s=0.1)
    later = [2, 0, -2, None][index - 2]
    return MoveVerdict(legal=True, screen='fast', extra_lifts=index - 2,
                       gauges={'lift_counts': {'projected_later_delta': later}}, wall_s=0.1)


_FAKE_SLOTS = []


@pytest.fixture
def screened(checked, monkeypatch):
    import experiments.mvp.pipeline.sandbox as sandbox
    global _FAKE_SLOTS
    instance, box, slot = _box(checked)
    _FAKE_SLOTS = alternatives.select_targets(instance, box, slot)
    assert len(_FAKE_SLOTS) == 6
    monkeypatch.setattr(sandbox, 'fast_screen', fake_screen)
    monkeypatch.setattr(sandbox, 'try_move', lambda *a, **k: pytest.fail('full checker is forbidden'))
    return alternatives.screen_alternatives(*checked, box.id)


def test_actual_same_size_selector_is_deterministic_and_unfiltered(checked):
    instance, box, current = _box(checked)
    targets = alternatives.select_targets(instance, box, current)
    assert targets == alternatives.select_targets(instance, box, current)
    assert len(targets) == len(set(targets)) == 6 and current not in targets
    cells = {cell.id: cell for cell in instance.vessel.cells}
    for target in targets:
        footprint = cells_of_slot(target)
        assert len(footprint) == (1 if box.length_ft == 20 else 2)
        assert all(cell in cells and getattr(cells[cell], f'accepts_{box.length_ft}') for cell in footprint)
    # Occupied cells remain eligible: we screen the move/swap rather than
    # removing potential failures before making the denominator.
    instance, plan, report = inputs(*checked)
    occupied = replay_plan(instance, plan, report).final_state.ship.placements
    other = next(p.slot for p in occupied if p.slot != current and
                 len(cells_of_slot(p.slot)) == len(cells_of_slot(current)))
    assert alternatives.select_targets(instance, box, current, targets=[other]) == [other]


def test_explicit_targets_reject_duplicates_current_and_bad_cells(checked):
    instance, box, current = _box(checked)
    target = alternatives.select_targets(instance, box, current)[0]
    for targets in ([target, target], [current], ['9999999'], ['bad-slot']):
        with pytest.raises(ValueError):
            alternatives.select_targets(instance, box, current, targets=targets)
    for limit in (0, 65, True, 1.5):
        with pytest.raises(ValueError):
            alternatives.select_targets(instance, box, current, limit=limit)


def test_disjoint_broken_rules_and_later_deltas(screened, checked):
    data = screened
    assert data['label'] == 'Fast screen'
    assert data['summary']['primary_broken_rules'] == {'rule.a': 2}
    assert data['summary']['categories'] == {
        'broken_rule': 2, 'survives_later_increase': 1, 'survives_later_unchanged': 1,
        'survives_later_decrease': 1, 'survives_later_unknown': 1}
    assert sum(data['summary']['categories'].values()) == 6
    assert data['summary']['full_check_still_needed'] is True
    for row in data['alternatives']:
        assert row['verdict_sha256'] == sha256_json(row['verdict'])
        assert row['binding'] == data['binding']
        assert row['binding']['report_sha256'] == sha256_json(checked[2])
        assert row['full_check_still_needed'] is True
    assert json.loads(json.dumps(data)) == data


def test_bilingual_alternatives_bind_now_and_later_separately(screened):
    statement = alternatives.alternatives_statement(screened)
    assert validate_protected_tokens(statement['text'], statement['th'])
    assert 'of 6 sampled' in statement['text']
    assert '2 break primary rule rule.a' in statement['text']
    assert 'extra_lifts (current-port delta) 0, projected_later_delta (projection) 2' in statement['text']
    assert 'extra_lifts (ผลต่างจำนวนยกที่ท่าปัจจุบัน) 0, projected_later_delta (ค่าคาดการณ์) 2' in statement['th']
    assert 'Full check still needed' in statement['text']
    for ref in statement['evidence']:
        assert ref['sha256'] == sha256_json(screened)
        assert screened[ref['path'][1:]] == ref['value']


def test_compact_summary_is_code_bound_and_preserves_protected_tokens(screened):
    statement = alternatives.alternatives_statement(screened)
    assert validate_protected_tokens(statement['summary_en'], statement['summary_th'])
    assert '6 sampled alternatives' in statement['summary_en']
    assert '2 break hard rules, 4 survive the screen, 0 are unresolved or invalid' in statement['summary_en']
    assert '2 ตำแหน่งผิดกฎบังคับ, 4 ตำแหน่งผ่านการคัดกรอง, 0 ตำแหน่งยังสรุปไม่ได้' in statement['summary_th']
    assert 'full check' in statement['summary_en']
    assert 'extra_lifts' not in statement['summary_en']
    assert 'projected_later_delta' not in statement['summary_en']
    assert len(statement['summary_en']) < len(statement['text']) / 3


def test_compact_summary_does_not_count_unresolved_as_surviving(screened):
    data = deepcopy(screened)
    row = data['alternatives'][-1]
    row['verdict']['legal'] = False
    row['category'], row['primary_rule'] = alternatives._classification(row['verdict'])
    data['summary'] = alternatives._summary(data['alternatives'])
    statement = alternatives.alternatives_statement(data)
    assert '2 break hard rules, 3 survive the screen, 1 are unresolved or invalid' in statement['summary_en']
    assert validate_protected_tokens(statement['summary_en'], statement['summary_th'])


def test_rederive_and_semantic_rescreen(screened, checked):
    audit = alternatives.rederive_validate(screened, *checked)
    assert audit['passed'] and not audit['screen_recomputed'] and not audit['full_check']
    audit = alternatives.rederive_validate(screened, *checked, rerun_screens=True)
    assert audit['passed'] and audit['screen_recomputed']


@pytest.mark.parametrize('mutation', ['summary', 'hash', 'input', 'selector', 'category', 'source'])
def test_tampering_rejected(screened, checked, mutation):
    data = deepcopy(screened)
    original = list(deepcopy(checked))
    if mutation == 'summary':
        data['summary']['alternatives'] = 5
    elif mutation == 'hash':
        data['alternatives'][0]['verdict']['legal'] = True
    elif mutation == 'input':
        original[2]['parts'] = {}
    elif mutation == 'selector':
        data['alternatives'].reverse()
    elif mutation == 'category':
        data['alternatives'][2]['category'] = 'survives_later_unchanged'
    else:
        data['source']['callable_source_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        alternatives.rederive_validate(data, *original)


def test_fresh_screen_disagrees_even_with_self_consistent_record(screened, checked):
    data = deepcopy(screened)
    row = data['alternatives'][2]
    row['verdict']['extra_lifts'] = 8
    row['verdict_sha256'] = sha256_json(row['verdict'])
    with pytest.raises(ValueError, match='Fresh fast-screen'):
        alternatives.rederive_validate(data, *checked, rerun_screens=True)


def test_real_fast_screen_invalid_input_is_not_a_broken_rule(checked):
    # Stops at parse_slot before expensive physics or any checker is invoked.
    from experiments.mvp.pipeline.sandbox import fast_screen
    verdict = fast_screen(checked[0], checked[1], _box(checked)[1].id, 'bad-slot')
    assert verdict.screen == 'fast' and not verdict.legal
    assert verdict.gauges['input_error'] and not verdict.broken
    assert alternatives._classification(verdict.model_dump(mode='json')) == ('input_invalid', None)


def test_full_verdict_cannot_masquerade_as_fast_screen():
    verdict = MoveVerdict(legal=True, screen='full', wall_s=0).model_dump(mode='json')
    with pytest.raises(ValueError, match='fast screen'):
        alternatives._classification(verdict)
