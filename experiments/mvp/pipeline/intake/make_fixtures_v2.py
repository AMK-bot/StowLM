"""Generate three fresh frozen held-out packets; no model calls, validation scores or tuning."""
from __future__ import annotations

import csv
from copy import deepcopy
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from stowage.model.instance import Instance


ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).with_name('fixtures') / 'heldout_v2'
LB = 0.45359237


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def source_maps():
    # Explicit unused fallback cohort, fixed before document construction or scores.
    # These approved development maps are not the pipeline's sealed map cohort.
    paths = [
        'experiments/mvp/corpus/val/small/feeder_800/random_yard/seed-040100023.json',
        'experiments/mvp/corpus/val/small/feeder_1700/random_yard/seed-041100002.json',
        'experiments/mvp/corpus/val/small/feeder_1700/simulated_yard/seed-041100007.json',
    ]
    allowed = set((ROOT / 'experiments/mvp/frontier/lab/sets/quick_small.txt').read_text().splitlines())
    if not set(paths) <= allowed:
        raise ValueError('Every v2 source must be an approved unused quick_small development map')
    return [(ROOT / path, f'case_{i:02d}') for i, path in enumerate(paths, 1)], 'experiments/mvp/frontier/lab/sets/quick_small.txt'


def build(path, split):
    out = OUT / split
    case_number = int(split.rsplit('_', 1)[1])
    day = 26 + case_number
    email_id, csv_id, image_id = (f'{split}-{kind}' for kind in ('forwarded_booking', 'gate_worksheet', 'quay_tally'))
    operator = ['Mali', 'Preecha', 'Kanya'][case_number - 1]
    route_reference = ['SYN-LCH-204', 'SYN-RAY-319', 'SYN-BKK-407'][case_number - 1]
    message = [
        'ขอเช็คปลายทางด้วยค่ะ / tally says another POD; please reconcile before load.',
        'รบกวนตามเอกสารที่ยังขาดครับ / only confirmed shipper data may be used.',
        'ช่วยเช็คทีค่ะ ใบจองกับใบหน้าท่าไม่ตรงกัน / hold unresolved boxes for planner.',
    ][case_number - 1]
    out.mkdir(parents=True, exist_ok=True)
    instance = Instance.model_validate_json(path.read_text())
    base = instance.model_dump(mode='json')
    amendments = []
    for row in base['containers']:
        if row['id'] in base['load_list'] and row['reefer'] and row['reefer_setpoint_c'] is None:
            row['reefer_setpoint_c'] = -18.0
            amendments.append({'row_id': row['id'], 'field': 'reefer_setpoint_c', 'source_value': None,
                               'fixture_value': -18.0,
                               'reason': 'Synthetic shipper declaration supplies a known answer absent from source map; the input documents deliberately omit it to plant the missing-setpoint trap.'})
    # This declared fixture base is the exact assembly target, distinct from its source map.
    instance = Instance.model_validate(base)
    by_id = {row['id']: row for row in base['containers']}
    rows = [deepcopy(by_id[cid]) for cid in base['load_list']]
    if len(rows) < 16:
        raise ValueError('Fresh held-out cases require at least sixteen original load-list rows')
    dg_row = next((r for r in rows if r['dg']), None)
    reefer_row = next((r for r in rows if r['reefer']), None)
    # If a new dev map lacks special cargo, a false declaration still exercises refusal.
    reserved = {r['id'] for r in (dg_row, reefer_row) if r}
    plain = [r for r in rows if r['id'] not in reserved]
    offset = (case_number * 3) % len(plain)
    plain = plain[offset:] + plain[:offset]
    conflict, missing, impossible, off_rotation, duplicate = plain[:5]
    dg_row = dg_row or plain[5]
    reefer_row = reefer_row or plain[6]
    reading = {'observations': [], 'duplicates': [], 'notes': ['SYNTHETIC ORACLE: handwritten by fixture generator; not Spark output.']}
    traps = []
    answers = []
    observed = deepcopy(rows)
    observed_by_id = {r['id']: r for r in observed}
    observed_by_id[missing['id']].pop('gross_mass_kg')
    observed_by_id[impossible['id']]['gross_mass_kg'] = 999999.0
    observed_by_id[off_rotation['id']]['pod'] = 'USLAX'
    observed_by_id[reefer_row['id']]['reefer'] = True
    observed_by_id[reefer_row['id']].pop('reefer_setpoint_c')
    if dg_row['dg']:
        observed_by_id[dg_row['id']]['dg']['un_number'] = '9999'
    else:
        observed_by_id[dg_row['id']]['dg'] = {'un_number': '9999', 'imdg_class': '3', 'stowage_category': 'A'}
    for kind, row, field, reason in [
        ('conflict_between_documents', conflict, 'pod', 'cross_document_conflict'),
        ('missing_vgm', missing, 'gross_mass_kg', 'missing_vgm'),
        ('unknown_un', dg_row, 'dg', 'unknown_un_number'),
        ('impossible_weight', impossible, 'gross_mass_kg', 'invalid_container'),
        ('reefer_without_setpoint', reefer_row, 'reefer_setpoint_c', 'missing_reefer_setpoint'),
        ('port_outside_rotation', off_rotation, 'pod', 'pod_not_on_rotation'),
        ('duplicate_id', duplicate, 'id', 'duplicate_id'),
    ]:
        traps.append({'kind': kind, 'row_id': row['id'], 'field': field, 'expected': 'question_or_rejection', 'reason': reason})
        answers.append({'row_id': row['id'], 'field': field, 'value': 'keep_one' if kind == 'duplicate_id' else row[field], 'reason': reason})
    if not reefer_row['reefer']:
        answers.append({'row_id': reefer_row['id'], 'field': 'reefer', 'value': False, 'reason': 'false_reefer_declaration'})

    def obs(row_id, field, value, doc, quote):
        reading['observations'].append({'row_id': row_id, 'field': field, 'value': value, 'document': doc, 'quote': quote})

    # The image is the sole POD evidence for three boxes. It is not accompanied by a transcription.
    image_only = {r['id'] for r in plain[5:8]}
    image_rows = [conflict] + plain[5:8] + [duplicate]
    conflict_pod = next(c.locode for c in instance.rotation.calls[instance.call_index + 1:] if c.locode != conflict['pod'])
    email_lines = [
        'SYNTHETIC DOCUMENT — invented forwarder and cargo, no customer data',
        f'From: {operator} / Synthetic Export Services <desk@example.invalid>',
        'To: Export desk <load@example.invalid>',
        f'Subject: FW: RE: gate amendment / {route_reference} / SYNTHETIC FEEDER',
        f'Date: {day} Oct 2026 16:25:00 +0700', '',
        message,
        'Gate clerk forwarded the worksheet again; final discharge notes for blank POD cells are on the photographed tally.',
        'FYI abbreviations: POL load, POD discharge, RF plugged reefer. lb is pounds; MT is 1000 kg.',
        'Do not confuse net and gross: worksheet gross includes tare. EDI_VGM_kg is the exact scale machine export; display units are rounded.',
        f"{missing['id']}: ยังรอ VGM ใบใหม่ / no signed weight yet; do not reuse an old entry.",
        f"{reefer_row['id']}: RF operating, temp to follow; shipment instruction has no temperature.",
        f"{dg_row['id']}: UN9999 was keyed on the DG form. Needs the DG desk to confirm.",
        f"{off_rotation['id']}: booking says USLAX, not sure this sailing calls there. Confirm destination.",
        '', '----- Forwarded message -----',
        f'From: {operator} / gate control <quay@example.invalid>',
        f'Sent: {day-1}/10/26 22.15 ICT',
        f'Subject: {route_reference} / handover after night shift',
        'Working discharge advice below. A mismatch on the paper tally is unresolved; neither source supersedes the other.',
    ]
    for row in observed:
        if row['id'] not in image_only:
            line = f"{row['id']} // POL {row['pol']} > POD {row['pod']} // ETD {day+1}-Oct-26"
            email_lines.append(line)
            obs(row['id'], 'pod', row['pod'], email_id, line)
    email_lines += ['', '----- Original message -----', 'Synthetic forwarded draft: booking quantity provisional. Check amended worksheet and quay note. ขอบคุณครับ/ค่ะ', 'Do not treat this email as a dangerous-goods clearance.']
    email = '\n'.join(email_lines) + '\n'
    (out / 'booking_email.txt').write_text(email)

    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow([f'SYNTHETIC DOCUMENT / GATE RECONCILIATION {route_reference}','','','','',''])
    writer.writerow(['Booking / equipment >>>','','Mass / mixed source units >>>','','Gate-in dates >>>',''])
    writer.writerow(['Container no.','ISO / size','POL','POD (blank = tally)','Gross as typed','EDI_VGM_kg','Date / local','Equipment declaration JSON'])
    human_fields = {'id', 'iso_type', 'pol', 'pod', 'gross_mass_kg'}
    for index, row in enumerate(observed):
        visible = {field: value for field, value in row.items() if field not in human_fields}
        equipment = compact(visible)
        mass = row.get('gross_mass_kg')
        if mass is None:
            typed, kg = 'VGM TBA', ''
        elif index % 3 == 0:
            typed, kg = f'{mass / LB:.3f} lb', repr(mass)
        elif index % 3 == 1:
            typed, kg = f'{mass / 1000:.4f} MT', repr(mass)
        else:
            typed, kg = f'{mass:.3f} kg', repr(mass)
        pod = '' if row['id'] in image_only else row['pod']
        date = [f'{day}/10/2026', f'Oct {day} 26', f'2026-10-{day} 23:40'][index % 3]
        line = [row['id'], row['iso_type'], row['pol'], pod, typed, kg, date, equipment]
        writer.writerow(line)
        # csv quoting is not needed to identify these short unique evidence spans.
        obs(row['id'], 'id', row['id'], csv_id, row['id'])
        obs(row['id'], 'iso_type', row['iso_type'], csv_id, row['iso_type'])
        obs(row['id'], 'pol', row['pol'], csv_id, row['pol'])
        if pod:
            obs(row['id'], 'pod', pod, csv_id, pod)
        if kg:
            obs(row['id'], 'gross_mass_kg', mass, csv_id, kg)
        # Attribute values live in a declared equipment annex, not undocumented defaults.
        # Quoted CSV escapes double quotes; cite exactly the escaped cell text.
        quoted_equipment = equipment.replace('"', '""')
        for field, value in visible.items():
            obs(row['id'], field, value, csv_id, quoted_equipment)
    duplicate_line = [duplicate['id'], duplicate['iso_type'], duplicate['pol'], duplicate['pod'], 'rescan / same physical box', '', f'{day}-Oct-26', 'REPEATED ID / duplicate gate scan; reconcile count']
    writer.writerow(duplicate_line)
    reading['duplicates'].append({'row_id': duplicate['id'], 'document': csv_id, 'quote': 'REPEATED ID / duplicate gate scan; reconcile count'})
    csv_text = stream.getvalue()
    (out / 'gate_dump.csv').write_text(csv_text)

    paper = Image.new('RGB', (1500, 1160), '#f0ead9')
    draw = ImageDraw.Draw(paper)
    hand_path = '/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf'
    plain_path = '/System/Library/Fonts/Supplemental/Arial.ttf'
    hand = ImageFont.truetype(hand_path, 45)
    small = ImageFont.truetype(plain_path, 28)
    heading = ImageFont.truetype(hand_path, 59)
    draw.text((85, 38), 'GENERATED PICTURE — SYNTHETIC TALLY', fill='#653f35', font=small)
    draw.text((85, 95), f'Tally / {route_reference}', fill='#29354b', font=heading)
    draw.text((85, 190), f'{day} / 10 / 26      21:15       shift {case_number}', fill='#3e4664', font=hand)
    draw.text((85, 270), 'BOX NO.                      POD       remarks', fill='#3c4660', font=hand)
    for i, row in enumerate(image_rows):
        y = 365 + i * 117
        draw.line((75, y + 75, 1420, y + 75), fill='#bbb6a9', width=2)
        pod = conflict_pod if row['id'] == conflict['id'] else row['pod']
        line = f"{row['id']}    {pod}"
        draw.text((92 + (i % 2) * 5, y), row['id'], fill='#253e68', font=hand)
        draw.text((710, y + (i % 3) * 3), pod, fill='#253e68', font=hand)
        draw.text((1010, y + 3), ['POD conflict ?', 'counted / seal OK', 'gate card seen', 'checked', 'repeat event?'][i], fill='#3b5266', font=ImageFont.truetype(hand_path, 34))
        obs(row['id'], 'pod', pod, image_id, line)
    draw.text((85, 1010), 'Discharge notes only; VGM needs the signed return.', fill='#5a4b3f', font=hand)
    paper = paper.rotate((-2.8, 1.6, -1.9)[case_number - 1], resample=Image.Resampling.BICUBIC, expand=True, fillcolor='#696b65')
    paper.save(out / 'tally_sheet.png')
    documents = [
        {'id': email_id, 'path': str((out / 'booking_email.txt').relative_to(ROOT)), 'mime_type': 'text/plain', 'text': email, 'synthetic': True},
        {'id': csv_id, 'path': str((out / 'gate_dump.csv').relative_to(ROOT)), 'mime_type': 'text/csv', 'text': csv_text, 'synthetic': True},
        {'id': image_id, 'path': str((out / 'tally_sheet.png').relative_to(ROOT)), 'mime_type': 'image/png', 'synthetic': True},
    ]
    dump(out / 'base_instance.json', base)
    dump(out / 'documents.json', documents)
    dump(out / 'oracle_reading.json', reading)
    dump(out / 'expected.json', {'synthetic': True, 'label': 'Synthetic ground truth and human answers; not model output', 'source_map': str(path.relative_to(ROOT)), 'source_map_sha256': sha256_file(path), 'base_amendments': amendments, 'clean_control_ids': [r['id'] for r in rows if r['id'] not in {t['row_id'] for t in traps}], 'base_instance_sha256': sha256_json(base), 'rows': rows, 'traps': traps, 'answers': answers})
    return {'split': split, 'source_map': str(path.relative_to(ROOT)), 'source_map_sha256': sha256_file(path), 'base_amendments': amendments, 'rows': len(rows), 'traps': len(traps), 'clean_controls': len(rows) - len({t['row_id'] for t in traps}), 'files': {p.name: sha256_file(p) for p in sorted(out.iterdir()) if p.is_file()}}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    freeze = OUT / 'freeze.json'
    if freeze.exists():
        raise SystemExit('Fixtures already frozen; do not regenerate a scored held-out split.')
    paths, map_list = source_maps()
    cases = [build(path, split) for path, split in paths]
    manifest = {'schema_version': 'stowlm.intake-fixture-freeze/2', 'cohort': 'heldout_v2', 'frozen_before_scoring': True, 'synthetic': True, 'source_list': map_list, 'selection': 'Three unused quick_small maps fixed before generation; source seeds exclude development seed040100010 and consumed old-heldout seed040100046. Source rows >=16 retain clean controls beside seven planted traps. maps/dev.txt absent at v2 source selection. Not the pipeline sealed cohort.', 'cases': cases, 'evaluation_split': 'heldout_v2'}
    dump(freeze, manifest)
    (OUT / 'freeze.sha256').write_text(sha256_file(freeze) + '  freeze.json\n')
    print(compact({'freeze_sha256': sha256_file(freeze), 'cases': [{k: v for k, v in case.items() if k != 'files'} for case in cases]}))


if __name__ == '__main__':
    main()
