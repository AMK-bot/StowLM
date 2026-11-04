"""Deterministic sampled alternatives, screened without search or a full check."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import inspect
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import MoveVerdict, sha256_file, sha256_json
from stowage.conventions import format_slot, parse_slot

SCHEMA = 'stowlm.box-alternatives/1'
SELECTOR = 'same-size-spread-v1'
ROOT = Path(__file__).resolve().parents[4]


def _json(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


def _hashes(instance, plan, report):
    return {name + '_sha256': sha256_json(_json(value)) for name, value in
            [('instance', instance), ('plan', plan), ('report', report)]}


def source_fingerprint(screen):
    """Pin screening dependencies and the actual callable, including test doubles.

    This is content binding, not a signature or a claim of a full checker pass.
    """
    paths = [Path(__file__), Path(__file__).with_name('api.py'),
             ROOT / 'experiments/mvp/pipeline/contract/schema.py']
    sandbox = ROOT / 'experiments/mvp/pipeline/sandbox'
    paths.extend(sandbox / name for name in ('moves.py', 'physics.py', 'securing.py', 'visibility.py'))
    paths.extend((ROOT / 'src/stowage').rglob('*.py'))
    files = {str(path.relative_to(ROOT)): sha256_file(path) for path in sorted(set(paths))}
    try:
        text = inspect.getsource(screen)
    except (OSError, TypeError):
        raise ValueError('Fast screen callable must have inspectable source') from None
    value = {'files': files, 'callable': screen.__module__ + '.' + screen.__qualname__,
             'callable_source_sha256': hashlib.sha256(text.encode()).hexdigest()}
    return dict(value, sha256=sha256_json(value))


def _context(instance, plan, report, box_id):
    from .api import inputs, replay_plan
    instance, plan, report = inputs(instance, plan, report)
    replay = replay_plan(instance, plan, report)
    if not replay.legal:
        raise ValueError('Plan does not replay legally')
    box = next((b for b in instance.containers if b.id == box_id), None)
    placement = next((p for p in replay.final_state.ship.placements if p.container_id == box_id), None)
    if box is None or placement is None:
        raise ValueError('Box must be aboard in the departure plan')
    return instance, plan, report, box, placement


def select_targets(instance, box, current_slot, *, limit=6, targets=None):
    """Sample actual same-length-compatible ship slots, without filtering failures.

    Plugs, occupancy, supports, DG, physics and crane order belong to screening;
    they must not make the sample look clearer than it is.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 64:
        raise ValueError('Alternative limit must be an integer from 1 to 64')
    cells = {c.id: c for c in instance.vessel.cells}
    candidates = set()
    for cell in cells.values():
        a = parse_slot(cell.id)
        if box.length_ft == 20:
            if cell.accepts_20:
                candidates.add(cell.id)
        elif a.bay < 998:
            partner = cells.get(format_slot(a.bay + 2, a.row, a.tier))
            if partner and all(getattr(c, f'accepts_{box.length_ft}') for c in (cell, partner)):
                candidates.add(format_slot(a.bay + 1, a.row, a.tier))
    candidates.discard(current_slot)
    if targets is not None:
        if not isinstance(targets, (list, tuple)) or any(not isinstance(s, str) for s in targets):
            raise ValueError('Targets must be a list of actual compatible slot strings')
        if len(set(targets)) != len(targets) or any(s not in candidates for s in targets):
            raise ValueError('Targets must be distinct same-size slots excluding the current slot')
        if len(targets) > limit:
            raise ValueError('Explicit targets exceed alternative limit')
        return list(targets)
    ordered = sorted(candidates)
    count = min(limit, len(ordered))
    if count < 2:
        return ordered[:count]
    # Spread over the actual geometry; no scoring or cherry-picking verdicts.
    return [ordered[i * (len(ordered) - 1) // (count - 1)] for i in range(count)]


def _classification(verdict):
    if verdict['screen'] != 'fast':
        raise ValueError('Alternative evidence must be a fast screen')
    if verdict['gauges'].get('input_error'):
        return 'input_invalid', None
    hard = sorted({v['rule_id'] for v in verdict['broken'] if v.get('severity', 'hard') == 'hard'})
    if hard:
        return 'broken_rule', hard[0]
    if not verdict['legal']:
        return 'screen_unresolved', None
    later = verdict['gauges'].get('lift_counts', {}).get('projected_later_delta')
    return ('survives_later_unknown' if later is None else
            'survives_later_increase' if later > 0 else
            'survives_later_decrease' if later < 0 else 'survives_later_unchanged'), None


def _summary(rows):
    classes = Counter(row['category'] for row in rows)
    rules = Counter(row['primary_rule'] for row in rows if row['category'] == 'broken_rule')
    return {'alternatives': len(rows), 'primary_broken_rules': dict(sorted(rules.items())),
            'categories': dict(sorted(classes.items())), 'full_check_still_needed': True,
            'scope': 'Sampled final-stow and proposed crane-order screens, not a placement necessity proof.'}


def screen_alternatives(instance, plan, report, box_id, *, limit=6, targets=None):
    """Run only sandbox.fast_screen. Caller admits CPU work before calling this."""
    import experiments.mvp.pipeline.sandbox as sandbox
    original_hashes = _hashes(instance, plan, report)
    instance, plan, report, box, position = _context(instance, plan, report, box_id)
    selected = select_targets(instance, box, position.slot, limit=limit, targets=targets)
    source = source_fingerprint(sandbox.fast_screen)
    binding = dict(original_hashes, source_sha256=source['sha256'])
    rows = []
    for slot in selected:
        verdict = _json(sandbox.fast_screen(instance, plan, box_id, slot))
        verdict = MoveVerdict.model_validate(verdict).model_dump(mode='json')
        category, primary = _classification(verdict)
        rows.append({'target_slot': slot, 'verdict': verdict, 'verdict_sha256': sha256_json(verdict),
                     'binding': dict(binding), 'category': category, 'primary_rule': primary,
                     'full_check_still_needed': True})
    if source_fingerprint(sandbox.fast_screen) != source:
        raise ValueError('Fast-screen source changed during recording; repeat after edits settle')
    return {'schema': SCHEMA, 'binding': binding, 'normalized_inputs': _hashes(instance, plan, report),
            'source': source, 'box_id': box_id, 'current_slot': position.slot,
            'label': 'Fast screen', 'selector': SELECTOR, 'limit': limit,
            'requested_targets': list(targets) if targets is not None else None,
            'alternatives': rows, 'summary': _summary(rows)}


def _semantic(value):
    if isinstance(value, dict):
        return {k: _semantic(v) for k, v in value.items()
                if k != 'wall_s' and not k.endswith('_wall_s')}
    if isinstance(value, list):
        return [_semantic(v) for v in value]
    return value


def rederive_validate(data, instance, plan, report, *, rerun_screens=False):
    """Recheck input/source/selector, every evidence digest and derived category.

    Set rerun_screens=True for independent semantic re-screening (CPU admission
    required). The default validates recorded evidence integrity and rederives
    its interpretation; it does not pretend to repeat a checker calculation.
    """
    import experiments.mvp.pipeline.sandbox as sandbox
    original_hashes = _hashes(instance, plan, report)
    instance, plan, report, box, position = _context(instance, plan, report, data['box_id'])
    source = source_fingerprint(sandbox.fast_screen)
    binding = dict(original_hashes, source_sha256=source['sha256'])
    if (data['schema'] != SCHEMA or data['binding'] != binding or data['source'] != source
            or data['normalized_inputs'] != _hashes(instance, plan, report)
            or data['label'] != 'Fast screen' or data['selector'] != SELECTOR
            or data['current_slot'] != position.slot):
        raise ValueError('Alternative input or source binding changed')
    selected = select_targets(instance, box, position.slot, limit=data['limit'], targets=data['requested_targets'])
    if selected != [row['target_slot'] for row in data['alternatives']]:
        raise ValueError('Alternative selector no longer rederives')
    for row in data['alternatives']:
        verdict = MoveVerdict.model_validate(row['verdict']).model_dump(mode='json')
        if (row['binding'] != binding or sha256_json(verdict) != row['verdict_sha256']
                or (row['category'], row['primary_rule']) != _classification(verdict)
                or row['full_check_still_needed'] is not True):
            raise ValueError('Alternative verdict evidence no longer rederives')
        if rerun_screens:
            fresh = _json(sandbox.fast_screen(instance, plan, box.id, row['target_slot']))
            if _semantic(fresh) != _semantic(verdict):
                raise ValueError('Fresh fast-screen verdict differs from recorded evidence')
    if data['summary'] != _summary(data['alternatives']):
        raise ValueError('Alternative summary no longer rederives')
    return {'passed': True, 'screen_recomputed': rerun_screens,
            'checked': ['input hashes', 'source fingerprint', 'selector', 'verdict digests', 'categories', 'summary'],
            'full_check': False}


def alternatives_statement(data):
    """Code fills every role in both languages; Spark cannot swap quantities."""
    summary = data['summary']
    categories = summary['categories']
    broken = categories.get('broken_rule', 0)
    surviving = sum(count for category, count in categories.items() if category.startswith('survives_'))
    unresolved = summary['alternatives'] - broken - surviving
    summary_en = (f"Fast screen: {summary['alternatives']} sampled alternatives for {data['box_id']}; "
                  f'{broken} break hard rules, {surviving} survive the screen, '
                  f'{unresolved} are unresolved or invalid; every alternative still needs a full check')
    summary_th = (f"Fast screen: ทางเลือกที่เลือกเป็นตัวอย่าง {summary['alternatives']} ตำแหน่งสำหรับ {data['box_id']}; "
                  f'{broken} ตำแหน่งผิดกฎบังคับ, {surviving} ตำแหน่งผ่านการคัดกรอง, '
                  f'{unresolved} ตำแหน่งยังสรุปไม่ได้หรือคำขอไม่ถูกต้อง; ทุกทางเลือกยังต้องผ่านการตรวจสอบฉบับเต็ม')
    en = [f"Fast screen: of {summary['alternatives']} sampled same-size alternatives for {data['box_id']}"]
    th = [f"Fast screen: จากทางเลือกขนาดเดียวกันที่สุ่มตัวอย่างแบบกำหนดแน่นอน {summary['alternatives']} ตำแหน่งสำหรับ {data['box_id']}"]
    for rule, count in summary['primary_broken_rules'].items():
        en.append(f'{count} break primary rule {rule}')
        th.append(f'{count} ตำแหน่งผิดกฎหลัก {rule}')
    labels = {
        'input_invalid': ('invalid requests', 'คำขอย้ายไม่ถูกต้อง'),
        'screen_unresolved': ('unresolved screens', 'ผลคัดกรองยังสรุปไม่ได้'),
        'survives_later_increase': ('survive with increased projected later lifts', 'ผ่านการคัดกรองแต่จำนวนยกภายหลังที่คาดการณ์เพิ่มขึ้น'),
        'survives_later_decrease': ('survive with decreased projected later lifts', 'ผ่านการคัดกรองและจำนวนยกภายหลังที่คาดการณ์ลดลง'),
        'survives_later_unchanged': ('survive with unchanged projected later lifts', 'ผ่านการคัดกรองโดยจำนวนยกภายหลังที่คาดการณ์คงเดิม'),
        'survives_later_unknown': ('survive with unknown projected later lifts', 'ผ่านการคัดกรองแต่ยังไม่ทราบจำนวนยกภายหลังที่คาดการณ์'),
    }
    for category, count in summary['categories'].items():
        if category == 'broken_rule':
            continue
        english, thai = labels[category]
        en.append(f'{count} {english}')
        th.append(f'{count} {thai}')
    for row in data['alternatives']:
        verdict = row['verdict']
        now = verdict['extra_lifts']
        later = verdict['gauges'].get('lift_counts', {}).get('projected_later_delta')
        now_text = 'unavailable' if now is None else str(now)
        later_text = 'unavailable' if later is None else str(later)
        # Literal field labels bind values to their actual sandbox paths.
        en.append(f"{row['target_slot']}: extra_lifts (current-port delta) {now_text}, "
                  f'projected_later_delta (projection) {later_text}')
        th.append(f"{row['target_slot']}: extra_lifts (ผลต่างจำนวนยกที่ท่าปัจจุบัน) {now_text}, "
                  f'projected_later_delta (ค่าคาดการณ์) {later_text}')
    en.append('Full check still needed for every alternative; a screen rejection can concern the proposed crane order')
    th.append('ทุกทางเลือกยังต้องผ่านการตรวจสอบฉบับเต็ม; การคัดกรองอาจปฏิเสธเพราะลำดับยกที่เสนอ')
    digest = sha256_json(data)
    refs = [{'source': 'alternatives', 'sha256': digest, 'path': '/' + key,
             'value': deepcopy(data[key]), 'href': f'alternatives:{digest}#/{key}',
             **data['binding']} for key in ('summary', 'alternatives', 'source')]
    return {'id': 'alternatives', 'text': '; '.join(en) + '.', 'th': '; '.join(th) + '.',
            'summary_en': summary_en, 'summary_th': summary_th, 'evidence': refs}
