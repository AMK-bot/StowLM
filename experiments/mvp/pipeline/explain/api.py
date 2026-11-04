"""Box facts from a content-bound checker report and the replayed plan."""
from __future__ import annotations

import importlib
from collections import OrderedDict

from stowage.checker import report_matches_inputs
from stowage.conventions import cells_of_slot
from stowage.model import CheckReport, Instance, MovePlan
from stowage.moves import apply_plan

from experiments.mvp.pipeline.contract.schema import Explanation, Mode, sha256_json

_REPLAYS = OrderedDict()


def replay_plan(instance, plan, report):
    """Reuse only after inputs() verified the complete checker content binding."""
    key = tuple(sorted(p for p in report.provenance.parents
                       if p.startswith(('checker.instance.sha256:', 'checker.plan.sha256:'))))
    if key not in _REPLAYS:
        _REPLAYS[key] = apply_plan(instance, plan)
        if len(_REPLAYS) > 4:
            _REPLAYS.popitem(last=False)
    _REPLAYS.move_to_end(key)
    return _REPLAYS[key]


def inputs(instance, plan, report):
    instance = instance if isinstance(instance, Instance) else Instance.model_validate(instance)
    plan = plan if isinstance(plan, MovePlan) else MovePlan.model_validate(plan)
    report = report if isinstance(report, CheckReport) else CheckReport.model_validate(report)
    if not report_matches_inputs(report, instance, plan):
        raise ValueError('Checker report is not bound to this instance and plan; recheck them')
    if instance.provenance.tainted or plan.provenance.tainted or report.provenance.tainted:
        raise ValueError('Stub-marked evidence cannot explain a checked plan')
    return instance, plan, report


def statement_menu(instance, plan, report, box_id, *, what_if=None):
    instance, plan, report = inputs(instance, plan, report)
    replay = replay_plan(instance, plan, report)
    if not replay.legal:
        raise ValueError('Plan does not replay legally; no departure box explanation is available')
    dumps = {name: obj.model_dump(mode='json') for name, obj in
             [('instance', instance), ('plan', plan), ('report', report), ('replay', replay.final_state.ship)]}
    hashes = {name: sha256_json(value) for name, value in dumps.items()}

    def evidence(source, path, value, **extra):
        return {'source': source, 'sha256': hashes[source], 'path': path, 'value': value,
                'href': f'{source}:{hashes[source]}#{path}', **extra}

    def ref(source, path, **extra):
        value = dumps[source]
        for key in path.lstrip('/').split('/'):
            value = value[int(key)] if isinstance(value, list) else value[key.replace('~1', '/').replace('~0', '~')]
        return evidence(source, path, value, **extra)

    boxes = {b.id: b for b in instance.containers}
    if box_id not in boxes:
        raise ValueError(f'Unknown box: {box_id}')
    placements = replay.final_state.ship.placements
    positions = {p.container_id: p for p in placements}
    if box_id not in positions:
        raise ValueError(f'Box is not aboard in this departure plan: {box_id}')
    p = positions[box_id]
    box = boxes[box_id]
    box_index = next(i for i, b in enumerate(instance.containers) if b.id == box_id)
    place_index = next(i for i, q in enumerate(placements) if q.container_id == box_id)
    box_ref = ref('instance', f'/containers/{box_index}', box_id=box_id, locode=box.pod)
    position_ref = ref('replay', f'/placements/{place_index}', box_id=box_id)
    out = []

    def add(sid, en, th, refs):
        out.append({'id': sid, 'text': en, 'th': th, 'evidence': refs})

    add('position', f'{box_id} is at {p.slot}; its POD is {box.pod}.',
        f'{box_id} อยู่ที่ {p.slot}; POD คือ {box.pod}.', [position_ref, box_ref])
    from .facts import additional_facts
    extra = additional_facts(instance, plan, report, box_id, replay)
    cargo = next(s for s in extra if s['id'] == 'cargo')
    out[0] = {**cargo, 'id': 'position',
              'text': cargo['text'] + f' Slot {p.slot}; POD {box.pod}.',
              'th': cargo['th'] + f' Slot {p.slot}; POD {box.pod}.',
              'evidence': [*cargo['evidence'], position_ref]}
    out.extend(s for s in extra if s['id'] != 'cargo')
    complete = (report.passed and report.count is not None and not report.shut_out
                and {'moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints', 'voyage'} <= report.parts.keys()
                and all(sum(name.startswith('condition:') and name.endswith(':' + part)
                            for name in report.parts) >= 3
                        for part in ('stability', 'strength', 'visibility', 'lashing', 'constraints'))
                and all(part.ran and not part.error for part in report.parts.values())
                and not any(v.severity == 'hard' for v in report.violations)
                and not any(v.severity == 'hard' for part in report.parts.values() for v in part.violations))
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    complete = complete and (not rule_freeze_v1_enabled() or 'in_port' in report.parts)
    en = 'The supplied full checker report passes.' if complete else 'The supplied report does not establish a full checker pass.'
    th = 'รายงานการตรวจสอบฉบับเต็มที่ให้มาผ่านการตรวจสอบ.' if complete else 'รายงานที่ให้มายังยืนยันไม่ได้ว่าผ่านการตรวจสอบฉบับเต็ม.'
    part_refs = [ref('report', '/parts/' + name.replace('~', '~0').replace('/', '~1') + '/ran')
                 for name in report.parts]
    add('checker', en, th, [ref('report', '/passed'), *part_refs, ref('report', '/violations')])
    cells = {c.id: c for c in instance.vessel.cells}
    for si, stack in enumerate(instance.vessel.stacks):
        footprint = set(cells_of_slot(p.slot))
        if not footprint.intersection(stack.cell_ids):
            continue
        column = [(q, boxes[q.container_id], set(cells_of_slot(q.slot))) for q in placements
                  if set(cells_of_slot(q.slot)).intersection(stack.cell_ids)]
        column.sort(key=lambda row: min(stack.cell_ids.index(cid) for cid in row[2] if cid in stack.cell_ids))
        pos = next(i for i, row in enumerate(column) if row[0].container_id == box_id)
        stack_ref = ref('instance', f'/vessel/stacks/{si}')
        col_refs = [ref('replay', f'/placements/{next(i for i,q in enumerate(placements) if q.container_id == row[0].container_id)}') for row in column]
        cargo_refs = [ref('instance', f'/containers/{next(i for i,b in enumerate(instance.containers) if b.id == row[1].id)}') for row in column]
        for direction, offset in [('lower', -1), ('higher', 1)]:
            neighbour = pos + offset
            if 0 <= neighbour < len(column):
                other = column[neighbour][0].container_id
                en = f'In {stack.id}, the adjacent {direction} tier is occupied by {other}; moving there needs a changed arrangement.'
                term = 'ล่าง' if direction == 'lower' else 'บน'
                th = f'ใน {stack.id} ชั้นถัดไปด้าน{term}มี {other} อยู่; การย้ายไปที่นั่นต้องเปลี่ยนการจัดวาง.'
            elif direction == 'lower':
                en = f'In {stack.id}, this box is at the lowest declared cell; there is no lower cell in this stack.'
                th = f'ใน {stack.id} ตู้นี้อยู่ที่ตำแหน่งล่างสุดที่กำหนด; ไม่มีตำแหน่งต่ำกว่านี้ในกอง.'
            else:
                current = next(cid for cid in footprint if cid in stack.cell_ids)
                if stack.cell_ids.index(current) == len(stack.cell_ids) - 1:
                    en = f'In {stack.id}, there is no higher declared cell.'
                    th = f'ใน {stack.id} ไม่มีตำแหน่งสูงกว่านี้ที่กำหนดไว้.'
                else:
                    en = f'In {stack.id}, moving this top box higher alone would leave a support gap; other arrangements have not been checked.'
                    th = f'ใน {stack.id} การย้ายตู้บนสุดนี้ขึ้นไปเพียงตู้เดียวจะทำให้เกิดช่องว่างด้านล่าง; ยังไม่ได้ตรวจสอบการจัดวางแบบอื่น.'
            add(f'{direction}:{stack.id}', en, th, [stack_ref, *col_refs])
        ordered = ', '.join(f'{b.id}={b.pod}@{next(c.index for c in instance.rotation.calls if c.index > instance.call_index and c.locode == b.pod)}' for _, b, _ in column)
        add(f'pod:{stack.id}', f'Bottom to top in {stack.id}, POD and next call index: {ordered}. See the restow projection for blocking boxes; an earlier discharge below a later one can require restows.',
            f'จากล่างขึ้นบนใน {stack.id}, POD และลำดับเที่ยวแวะถัดไป: {ordered}. ดูประมาณการยกพักตู้ที่กีดขวาง; การขนถ่ายตู้ด้านล่างก่อนตู้ด้านบนอาจต้องยกพักตู้.',
            [stack_ref, *col_refs, *cargo_refs, ref('instance', '/rotation')])
        mass = sum(b.gross_mass_kg / len(fp) for _, b, fp in column)
        lengths = {b.length_ft for _, b, _ in column}
        limit = stack.max_mass_kg
        if 20 in lengths and stack.max_mass_20_kg is not None:
            limit = stack.max_mass_20_kg
        elif lengths <= {40, 45} and stack.max_mass_40_kg is not None:
            limit = stack.max_mass_40_kg
        share = box.gross_mass_kg / len(footprint)
        add(f'weight:{stack.id}', f'{box_id} contributes {share:g} kg to {stack.id}; planned column mass {mass:g} kg, declared limit {limit:g} kg, difference {limit-mass:g} kg (expert.physical.stack_weight). Spanning boxes share mass equally between supports.',
            f'{box_id} ลงน้ำหนัก {share:g} kg ที่ {stack.id}; น้ำหนักกองตามแผน {mass:g} kg, ขีดจำกัดที่กำหนด {limit:g} kg, ส่วนต่าง {limit-mass:g} kg (expert.physical.stack_weight). ตู้ที่คร่อมหลายแนวแบ่งน้ำหนักเท่ากันระหว่างจุดรองรับ.',
            [stack_ref, *col_refs, *cargo_refs])
    dg_refs = []
    for name, part in report.parts.items():
        for key, value in part.margins.items():
            if 'segregation' in key.lower() or 'imdg' in key.lower():
                esc = lambda s: s.replace('~', '~0').replace('/', '~1')
                dg_refs.append(ref('report', f'/parts/{esc(name)}/margins/{esc(key)}'))
    if dg_refs:
        values = ', '.join(f"{r['path']}={r['value']:g}" for r in dg_refs)
        add('dg', f'DG segregation margins reported for the whole plan: {values}. These are not box-specific margins.',
            f'ส่วนต่างการแยก DG ที่รายงานสำหรับแผนทั้งหมด: {values}. ส่วนต่างเหล่านี้ไม่ได้ระบุเฉพาะตู้.', dg_refs)
    else:
        add('dg', 'The checker report provides no numeric DG segregation margin; no box-specific distance is inferred.',
            'รายงานการตรวจสอบไม่ได้ให้ส่วนต่างการแยก DG เป็นตัวเลข; จึงไม่อนุมานระยะห่างเฉพาะตู้.',
            [ref('report', '/parts/' + name.replace('~', '~0').replace('/', '~1') + '/margins')
             for name in report.parts if not name.startswith('condition:')])
    for i, violation in enumerate(report.violations):
        if box_id in violation.containers:
            add(f'violation:{i}', f'Checker rule {violation.rule_id}: {violation.message}',
                f'กฎจากการตรวจสอบ {violation.rule_id}: {violation.message}', [ref('report', f'/violations/{i}')])
    if what_if is not None:
        _what_if(out, instance, plan, box_id, what_if)
    return out


def _what_if(out, instance, plan, box_id, what_if, *, checked_verdict=None):
    target = what_if if isinstance(what_if, str) else what_if.get('target_slot') if isinstance(what_if, dict) else None
    if not isinstance(target, str):
        raise ValueError('what_if must be a target slot or {target_slot: slot}')
    try:
        module = importlib.import_module('experiments.mvp.pipeline.sandbox')
    except ModuleNotFoundError as error:
        if error.name != 'experiments.mvp.pipeline.sandbox':
            raise
        out.append({'id': 'what_if:unavailable', 'text': f'Move to {target} was not checked: sandbox is unavailable.',
                    'th': f'ยังไม่ได้ตรวจสอบการย้ายไป {target}: sandbox ยังไม่พร้อมใช้งาน.',
                    'evidence': [{'source': 'sandbox', 'status': 'not_run', 'target_slot': target}]})
        return
    verdict = checked_verdict if checked_verdict is not None else module.try_move(instance, plan, box_id, target)
    value = verdict.model_dump(mode='json') if hasattr(verdict, 'model_dump') else verdict
    from experiments.mvp.pipeline.contract.schema import MoveVerdict
    value = MoveVerdict.model_validate(value).model_dump(mode='json')
    identity = sha256_json(value)
    screen, legal = value['screen'], str(value['legal']).lower()
    rules = ', '.join(str(v.get('rule_id', 'unreported')) for v in value['broken']) or 'none'
    en = f'Move {box_id} to {target}: {screen} screen, legal={legal}; reported broken rules: {rules}.'
    th = f'ย้าย {box_id} ไป {target}: การตรวจแบบ {screen}, legal={legal}; กฎที่รายงานว่าละเมิด: {rules}.'
    if value['gauges'].get('input_error'):
        error = value['gauges'].get('error', '')
        en = f'Move {box_id} to {target} was not checked: invalid request. {error}'
        th = f'ยังไม่ได้ตรวจสอบการย้าย {box_id} ไป {target}: คำขอไม่ถูกต้อง. {error}'
    elif screen == 'fast':
        en += ' This is not a full-checker verdict.'
        th += ' นี่ยังไม่ใช่ผลจากการตรวจสอบฉบับเต็ม.'
    links = [
        {'source': 'sandbox', 'sha256': identity, 'path': '', 'value': value,
         'href': f'sandbox:{identity}#', 'instance_sha256': sha256_json(instance.model_dump(mode='json')),
         'plan_sha256': sha256_json(plan.model_dump(mode='json')), 'box_id': box_id, 'target_slot': target}]
    out.append({'id': 'what_if', 'text': en, 'th': th, 'evidence': links})
    for index, broken in enumerate(value['broken']):
        rule = broken.get('rule_id', 'unreported')
        message = broken.get('message', '')
        measured, limit = broken.get('measured'), broken.get('limit')
        unit = (' ' + broken['unit']) if broken.get('unit') else ''
        en_value = lambda value: f'{value}{unit}' if value is not None else 'not reported'
        th_value = lambda value: f'{value}{unit}' if value is not None else 'ไม่มีข้อมูล'
        en = f'Checker rule {rule}: {message} Reported measurement {en_value(measured)}; limit {en_value(limit)}.'
        th = f'กฎจากการตรวจสอบ {rule}: {message} ค่าที่รายงาน {th_value(measured)}; ขีดจำกัด {th_value(limit)}.'
        out.append({'id': f'what_if:broken:{index}', 'text': en, 'th': th, 'evidence': links})
    count = value['extra_lifts']
    later = value['gauges'].get('lift_counts', {}).get('projected_later_delta')
    now_text, later_text = ('unavailable' if n is None else str(n) for n in (count, later))
    out.append({'id': 'what_if:lifts',
                'text': f'extra_lifts (current-port delta): {now_text}; projected_later_delta (projection): {later_text}. This edit retains the crane order and ballast; it does not replan.',
                'th': f'extra_lifts (ผลต่างจำนวนยกที่ท่าปัจจุบัน): {now_text}; projected_later_delta (ประมาณการ): {later_text}. การแก้ไขนี้คงลำดับยกและบัลลาสต์เดิม; ไม่ได้วางแผนใหม่.', 'evidence': links})


def explain_box(instance, plan, report, box_id, *, language='en', what_if=None):
    if language not in ('en', 'th'):
        raise ValueError('language must be en or th')
    instance, plan, report = inputs(instance, plan, report)
    if what_if is None:
        from pathlib import Path
        from .cache import load_cached
        try:
            cached = load_cached(instance, plan, report, box_id, language=language,
                                 out_dir=Path(__file__).parent / 'fixtures' / 'hero' / 'cache')
        except (OSError, ValueError, KeyError, TypeError):
            cached = None
        if cached is not None:
            return cached
    from .render import render
    statements, audit = render(statement_menu(instance, plan, report, box_id, what_if=what_if), language)
    header = 'Template placement facts (no model summary)' if language == 'en' else 'ข้อมูลตำแหน่งจากแม่แบบ (ไม่มีบทสรุปที่เขียนโดยโมเดล)'
    for statement in statements:
        statement['render_checks'] = audit.get('checks', {})
    return Explanation(language=language, text=header + '\n' + '\n'.join(s['text'] for s in statements),
                       statements=statements, mode=Mode.live)


def explain_move(instance, plan, report, box_id, target_slot, verdict, *, language='en'):
    """Trusted worker adapter: reuse its real try_move result, without a second check."""
    if language not in ('en', 'th'):
        raise ValueError('language must be en or th')
    instance, plan, report = inputs(instance, plan, report)
    menu = statement_menu(instance, plan, report, box_id)
    _what_if(menu, instance, plan, box_id, target_slot, checked_verdict=verdict)
    from .render import render
    rows, audit = render(menu, language)
    for row in rows:
        row['render_checks'] = audit['checks']
    return Explanation(language=language, mode=Mode.live,
                       text='Template move facts (actual sandbox verdict; no model summary)\n'
                            + '\n'.join(s['text'] for s in rows), statements=rows)
