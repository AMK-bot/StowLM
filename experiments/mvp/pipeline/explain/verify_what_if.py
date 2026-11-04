"""Verify the recorded real sandbox example; --record rechecks one actual move."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import Explanation, Mode, MoveVerdict, sha256_json
from experiments.mvp.pipeline.explain.api import inputs, replay_plan, statement_menu
from experiments.mvp.pipeline.explain.make_fixture import wait_for_capacity
from experiments.mvp.pipeline.explain.render import render, validate_protected_tokens
from stowage.conventions import cells_of_slot

FIXTURES = Path(__file__).with_name('fixtures') / 'development'
OUTPUT = FIXTURES / 'what_if.json'


def fixture():
    return inputs(*(json.loads((FIXTURES / f'{name}.json').read_text())
                    for name in ('instance', 'plan', 'report')))


def proposal(instance, plan, report):
    """Choose a top 20-foot box and a free declared base cell on the same deck."""
    placements = replay_plan(instance, plan, report).final_state.ship.placements
    occupied = {cell for p in placements for cell in cells_of_slot(p.slot)}
    cells = {c.id: c for c in instance.vessel.cells}
    boxes = {b.id: b for b in instance.containers}
    stacks = {s.id: s for s in instance.vessel.stacks}
    candidates = []
    for p in placements:
        box = boxes[p.container_id]
        if box.length_ft != 20:
            continue
        source = cells[p.slot]
        stack = stacks[source.stack_id]
        level = stack.cell_ids.index(p.slot)
        if any(cid in occupied for cid in stack.cell_ids[level + 1:]):
            continue
        for other in instance.vessel.stacks:
            if other.on_deck != stack.on_deck or other.id == stack.id:
                continue
            target = other.cell_ids[0]
            if target in occupied or not cells[target].accepts_20:
                continue
            if box.reefer and not cells[target].reefer_plug:
                continue
            candidates.append(((-level, abs(other.bay - stack.bay), abs(other.row - stack.row),
                                p.container_id, target), p.container_id, p.slot, target))
    if candidates:
        _, box_id, source, target = min(candidates)
        return box_id, source, target, 'Top 20-foot box moved to an unoccupied supported base cell on the same deck.'
    p = placements[0]
    return p.container_id, p.slot, p.slot, 'No convenient free base found; same-slot full checker round trip.'


def record():
    admission = wait_for_capacity()
    instance, plan, report = fixture()
    box_id, source, target, selection = proposal(instance, plan, report)
    started = time.monotonic()
    # Exactly one public menu call invokes the real sandbox.try_move once.
    menu = statement_menu(instance, plan, report, box_id, what_if=target)
    wall_s = time.monotonic() - started
    statement = next(s for s in menu if s['id'] == 'what_if')
    link = statement['evidence'][0]
    verdict = MoveVerdict.model_validate(link['value'])
    assert verdict.screen == 'full' and not verdict.gauges.get('input_error')
    assert link['sha256'] == sha256_json(verdict.model_dump(mode='json'))
    explanations, audits = {}, {}
    for language in ('en', 'th'):
        rows, audit = render(menu, language, client=None)
        explanations[language] = Explanation(language=language, mode=Mode.recorded,
            text='Recorded answer (deterministic template; no model prose)\n' + '\n'.join(s['text'] for s in rows),
            statements=rows).model_dump(mode='json')
        audits[language] = audit
    value = dict(schema='stowlm.box-explanation-what-if/1', synthetic=True,
        label='Recorded answer', mode='recorded', created_utc=datetime.now(timezone.utc).isoformat(),
        input_hashes={name + '_sha256': sha256_json(obj.model_dump(mode='json')) for name, obj in
                      [('instance', instance), ('plan', plan), ('report', report)]},
        box_id=box_id, source_slot=source, target_slot=target, proposal_selection=selection,
        sandbox_verdict=verdict.model_dump(mode='json'), sandbox_evidence=link,
        explanations=explanations, audits=audits, wall_s=wall_s, admission=admission,
        cost_usd=0., paid_calls=0,
        notes='Real sandbox full checker; English and Thai deterministic templates. No native Thai reader review.')
    value['sha256'] = sha256_json(value)
    OUTPUT.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    return value


def verify():
    value = json.loads(OUTPUT.read_text())
    digest = value.pop('sha256')
    assert digest == sha256_json(value)
    instance, plan, report = fixture()
    assert value['input_hashes'] == {name + '_sha256': sha256_json(obj.model_dump(mode='json'))
        for name, obj in [('instance', instance), ('plan', plan), ('report', report)]}
    verdict = MoveVerdict.model_validate(value['sandbox_verdict'])
    assert verdict.screen == 'full' and not verdict.gauges.get('input_error')
    assert value['sandbox_evidence']['value'] == verdict.model_dump(mode='json')
    assert value['sandbox_evidence']['sha256'] == sha256_json(verdict.model_dump(mode='json'))
    english = Explanation.model_validate(value['explanations']['en'])
    thai = Explanation.model_validate(value['explanations']['th'])
    assert english.mode == thai.mode == Mode.recorded
    assert [s['id'] for s in english.statements] == [s['id'] for s in thai.statements]
    for en, th in zip(english.statements, thai.statements):
        assert validate_protected_tokens(en['text'], th['text'])
        assert en['evidence'] == th['evidence']
    return dict(box_id=value['box_id'], source_slot=value['source_slot'], target_slot=value['target_slot'],
                screen=verdict.screen, legal=verdict.legal, broken_rules=[r['rule_id'] for r in verdict.broken],
                wall_s=value['wall_s'], cost_usd=value['cost_usd'], checks='hashes, exact input binding, bilingual protected tokens and matching evidence')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', action='store_true', help='Wait for CPU admission and run a real full move check')
    args = parser.parse_args()
    if args.record:
        record()
    print(json.dumps(verify(), sort_keys=True))
