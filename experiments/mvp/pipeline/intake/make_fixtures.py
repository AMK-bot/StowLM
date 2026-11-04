"""Build labelled synthetic intake packets; never calls a model or scores a held-out case."""
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
OUT = Path(__file__).with_name('fixtures')
LB = 0.45359237


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def source_maps():
    dev_list = ROOT / 'experiments/mvp/pipeline/maps/dev.txt'
    if dev_list.exists():
        entries = [p for p in dev_list.read_text().splitlines() if p and not p.startswith('#')]
        if len(entries) >= 2:
            return [(ROOT / entries[0], 'dev'), (ROOT / entries[1], 'heldout')], str(dev_list.relative_to(ROOT))
    paths = [
        'experiments/mvp/corpus/val/small/feeder_500/simulated_yard/seed-040100010.json',
        'experiments/mvp/corpus/val/small/feeder_500/simulated_yard/seed-040100046.json',
    ]
    return [(ROOT / p, split) for p, split in zip(paths, ('dev', 'heldout'))], 'experiments/mvp/frontier/lab/sets/quick_small.txt'


def build(path, split):
    out = OUT / split
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
    if len(rows) < 8:
        raise ValueError('fixture requires at least eight original load-list rows')
    dg_row = next((r for r in rows if r['dg']), None)
    reefer_row = next((r for r in rows if r['reefer']), None)
    # If a new dev map lacks special cargo, a false declaration still exercises refusal.
    reserved = {r['id'] for r in (dg_row, reefer_row) if r}
    plain = [r for r in rows if r['id'] not in reserved]
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
        'From: Nida / Demo Forwarding <ops@example.invalid>',
        'To: Export desk <load@example.invalid>',
        f'Subject: RE: FWD: urgent load advice / {split.upper()}-BKK-117 / vessel DEMO FEEDER',
        'Date: Sun, 01 Nov 2026 07:35:00 +0700', '',
        'พี่คะ รบกวนเช็ค VGM อีกทีค่ะ — pls hold anything unclear, do not assume.',
        'Attached CSV is gate-in export. Photo is tally scribble from quay; PODs on it are not all in EDI.',
        'Abbrev: POL=load port, POD=discharge, MT=metric tonne, RF=operating reefer.',
        'The weight column is gross INCLUDING tare. EDI_VGM_kg preserves the scale export precision.',
        f"{missing['id']}: VGM ยังไม่มา / certificate pending, DO NOT use last voyage weight.",
        f"{reefer_row['id']}: RF=yes; temperature ??? — shipper has not sent set point.",
        f"{dg_row['id']}: DG desk typed UN9999; check before loading please.",
        f"{off_rotation['id']}: POD USLAX shown by booking; service rotation may be wrong.",
        '', '----- Forwarded message -----',
        'From: Somchai <gate@example.invalid>',
        'Sent: 31/10/26 18.10 ICT',
        'Subject: gate slips / short landed?',
        'As booked below. Tally and booking disagree on one destination; planner to confirm.',
    ]
    for row in observed:
        if row['id'] not in image_only:
            line = f"{row['id']} // POL {row['pol']} > POD {row['pod']} // ETD 02-Nov-26"
            email_lines.append(line)
            obs(row['id'], 'pod', row['pod'], 'booking_email', line)
    email_lines += ['', '----- Original message -----', 'Synthetic old reply: qty indicative only; final export in attachment. ขอบคุณค่ะ', 'Do not treat this email as a dangerous-goods clearance.']
    email = '\n'.join(email_lines) + '\n'
    (out / 'booking_email.txt').write_text(email)

    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['SYNTHETIC DOCUMENT / EXPORT GATE-IN','','','','',''])
    writer.writerow(['Booking details >>>','','Mass / mixed source units >>>','','Gate-in dates >>>',''])
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
        date = ['31/10/2026', 'Oct 31 26', '2026-10-31 23:40'][index % 3]
        line = [row['id'], row['iso_type'], row['pol'], pod, typed, kg, date, equipment]
        writer.writerow(line)
        # csv quoting is not needed to identify these short unique evidence spans.
        obs(row['id'], 'id', row['id'], 'gate_csv', row['id'])
        obs(row['id'], 'iso_type', row['iso_type'], 'gate_csv', row['iso_type'])
        obs(row['id'], 'pol', row['pol'], 'gate_csv', row['pol'])
        if pod:
            obs(row['id'], 'pod', pod, 'gate_csv', pod)
        if kg:
            obs(row['id'], 'gross_mass_kg', mass, 'gate_csv', kg)
        # Attribute values live in a declared equipment annex, not undocumented defaults.
        # Quoted CSV escapes double quotes; cite exactly the escaped cell text.
        quoted_equipment = equipment.replace('"', '""')
        for field, value in visible.items():
            obs(row['id'], field, value, 'gate_csv', quoted_equipment)
    duplicate_line = [duplicate['id'], duplicate['iso_type'], duplicate['pol'], duplicate['pod'], 'same unit / repeated gate scan', '', '01-Nov-26', 'DUPLICATE GATE EVENT, not another container']
    writer.writerow(duplicate_line)
    reading['duplicates'].append({'row_id': duplicate['id'], 'document': 'gate_csv', 'quote': 'DUPLICATE GATE EVENT, not another container'})
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
    draw.text((85, 95), f'Quay tally / {split.upper()}-117', fill='#29354b', font=heading)
    draw.text((85, 190), '01 / 11 / 26      06:40       shift B', fill='#3e4664', font=hand)
    draw.text((85, 270), 'BOX NO.                      POD       remarks', fill='#3c4660', font=hand)
    for i, row in enumerate(image_rows):
        y = 365 + i * 117
        draw.line((75, y + 75, 1420, y + 75), fill='#bbb6a9', width=2)
        pod = conflict_pod if row['id'] == conflict['id'] else row['pod']
        line = f"{row['id']}    {pod}"
        draw.text((92 + (i % 2) * 5, y), row['id'], fill='#253e68', font=hand)
        draw.text((710, y + (i % 3) * 3), pod, fill='#253e68', font=hand)
        draw.text((1010, y + 3), ['check booking !', 'OK / seal seen', 'gate slip', 'OK', 'same scan?'][i], fill='#3b5266', font=ImageFont.truetype(hand_path, 34))
        obs(row['id'], 'pod', pod, 'quay_photo', line)
    draw.text((85, 1010), 'Destination only. Weights: use VGM certificate.', fill='#5a4b3f', font=hand)
    paper = paper.rotate(-2.2 if split == 'dev' else 1.7, resample=Image.Resampling.BICUBIC, expand=True, fillcolor='#696b65')
    paper.save(out / 'tally_sheet.png')
    documents = [
        {'id': 'booking_email', 'path': str((out / 'booking_email.txt').relative_to(ROOT)), 'mime_type': 'text/plain', 'text': email, 'synthetic': True},
        {'id': 'gate_csv', 'path': str((out / 'gate_dump.csv').relative_to(ROOT)), 'mime_type': 'text/csv', 'text': csv_text, 'synthetic': True},
        {'id': 'quay_photo', 'path': str((out / 'tally_sheet.png').relative_to(ROOT)), 'mime_type': 'image/png', 'synthetic': True},
    ]
    dump(out / 'base_instance.json', base)
    dump(out / 'documents.json', documents)
    dump(out / 'oracle_reading.json', reading)
    dump(out / 'expected.json', {'synthetic': True, 'label': 'Synthetic ground truth and human answers; not model output', 'source_map': str(path.relative_to(ROOT)), 'source_map_sha256': sha256_file(path), 'base_amendments': amendments, 'base_instance_sha256': sha256_json(base), 'rows': rows, 'traps': traps, 'answers': answers})
    return {'split': split, 'source_map': str(path.relative_to(ROOT)), 'source_map_sha256': sha256_file(path), 'base_amendments': amendments, 'rows': len(rows), 'traps': len(traps), 'files': {p.name: sha256_file(p) for p in sorted(out.iterdir()) if p.is_file()}}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    freeze = OUT / 'freeze.json'
    if freeze.exists():
        raise SystemExit('Fixtures already frozen; do not regenerate a scored held-out split.')
    paths, map_list = source_maps()
    cases = [build(path, split) for path, split in paths]
    manifest = {'schema_version': 'stowlm.intake-fixture-freeze/1', 'frozen_before_scoring': True, 'synthetic': True, 'source_list': map_list, 'selection': 'Two development maps; prefer existing DG and reefer for trap realism. Not the pipeline sealed cohort.', 'cases': cases}
    dump(freeze, manifest)
    (OUT / 'freeze.sha256').write_text(sha256_file(freeze) + '  freeze.json\n')
    print(compact({'freeze_sha256': sha256_file(freeze), 'cases': [{k: v for k, v in case.items() if k != 'files'} for case in cases]}))


if __name__ == '__main__':
    main()
