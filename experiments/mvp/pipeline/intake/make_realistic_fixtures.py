"""Independent synthetic business documents; blind inputs are frozen before development tuning.

Never imports the reader, parser, validator or scorer. No model calls.
"""
from __future__ import annotations

import csv
from copy import deepcopy
import io
import json
from pathlib import Path
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from stowage.model.cargo import Container, DangerousGoods
from stowage.model.instance import Instance
from stowage.rules.imdg.dgl import dangerous_goods_list
from stowage.rules.catalogue import load_catalogue, rule_freeze_v1_enabled

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).with_name('fixtures') / 'realistic_v7'
VESSEL = 'SYNTHETIC LANTA'
VOYAGE = '2611S'
LABEL = 'SYNTHETIC DOCUMENT — invented cargo and companies; no customer data'
POLICY = {'this_port': 'THLCH', 'vessel': VESSEL, 'voyage': VOYAGE,
          'max_gross_mass_kg_by_length': {'20': 30480, '40': 32500, '45': 32500},
          'reefer_setpoint_c': {'min': -35, 'max': 30}}
# These development headers are intentionally public. Blind variants are separately frozen.
DEV_HEADERS = [
 ['Container', 'ISO', 'POL', 'POD', 'VGM_kg'],
 ['Container Number', 'ISO Code', 'Port of Loading', 'Port of Discharge', 'Verified Gross Mass'],
 ['CNTR NO', 'TYPE', 'POL', 'POD', 'VGM'],
]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def dg(un, pg, *, lq=False, mp=False, additional=None):
    e = next(e for e in dangerous_goods_list() if e.un_number == un and e.packing_group == pg)
    return DangerousGoods(un_number=f'{un:04}', imdg_class=e.hazard_class, packing_group=pg,
                         stowage_category=e.stowage_category, subsidiary_risks=list(e.subsidiary_risks),
                         stowage_codes=list(e.stowage_codes), segregation_codes=list(e.segregation_codes),
                         limited_quantity=lq, marine_pollutant=mp, additional=additional or []).model_dump(mode='json')


def csv_text(headers, rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue()


def make_base(path, case):
    base = json.loads(path.read_text())
    # Preserve source geometry and yard identities; amend only synthetic business facts.
    route = ['THLCH', 'HKHKG', 'VNSGN', 'SGSIN']
    for i, call in enumerate(base['rotation']['calls']):
        call['locode'] = route[i % len(route)]
    base['port']['locode'] = 'THLCH'
    if base.get('voyage'):
        base['voyage']['departure']['locode'] = 'THLCH'
        base['voyage']['arrival']['locode'] = base['rotation']['calls'][base['call_index'] + 1]['locode']
        for key in ('next_call', 'next_arrival'):
            if isinstance(base['voyage'].get(key), dict) and base['voyage'][key].get('locode') == 'THLCH':
                base['voyage'][key]['locode'] = 'SGSIN'
    base['vessel']['name'] = VESSEL
    base['id'] = f'realistic-v7-{case}'
    base['rule_overrides']['intake.policy'] = deepcopy(POLICY)
    by_id = {r['id']: r for r in base['containers']}
    for row in base['containers']:
        if row['pod'] not in {'HKHKG','VNSGN','SGSIN'}: row['pod'] = 'HKHKG'
        if row['pol'] == 'SGSIN': row['pol'] = 'THLCH'
    # Retain IDs present in the yard; no fabricated yard membership.
    ids = [cid for stack in base['yard']['stacks'] for cid in stack['boxes']]
    available = [cid for cid in base['load_list'] if cid in set(ids)]
    if len(available) < 8: raise ValueError('Source needs eight yard-backed load-list identities')
    base['load_list'] = available[:8]
    destinations = [c['locode'] for c in base['rotation']['calls'][base['call_index'] + 1:]]
    rows = []
    for i, cid in enumerate(base['load_list']):
        row = by_id[cid]
        length = row['length_ft']
        iso = ('22' if length == 20 else '45' if length == 40 else 'L5') + ('R1' if i == 2 else 'G1')
        row.update(iso_type=iso, length_ft=length,
                   height_m=2.896 if iso[1] == '5' else 2.591,
                   tare_kg=(3000.0 if length == 20 else 4500.0) if iso[2] == 'R' else (2300.0 if length == 20 else 3900.0),
                   gross_mass_kg=float(11200 + i * 860 + case * 10), max_gross_mass_kg=None,
                   weight_class=None, category=None, pol='THLCH', pod=destinations[i % len(destinations)],
                   empty=False, reefer=i == 2, reefer_setpoint_c=-18.0 if i == 2 else None,
                   dg=None, direct_delivery=False, special_stowage=[], oog=None)
        if i == 0:
            row['dg'] = dg(1263, 'III', additional=[dg(3082, 'III', mp=True)])
        if i == 1:
            row['dg'] = dg(3082, 'III', lq=True, mp=True)
        rows.append(deepcopy(row))
    # This base is the declared assembly target, not an unmodified source map.
    base = Instance.model_validate(base).model_dump(mode='json')
    return base, rows


def render_tally(path, rows, displayed, case):
    rng = random.Random(70600 + case)
    image = Image.new('RGB', (1640, 1260), '#eee5cd')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf', 42)
    big = ImageFont.truetype('/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf', 53)
    small = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 24)
    draw.text((65, 26), 'SYNTHETIC PICTURE / generated with PIL / not a real gate record', font=small, fill='#634939')
    draw.text((80, 83), f'LAEM CHABANG / Night shift {case:02d}', font=big, fill='#27364c')
    draw.text((80, 155), f'{VESSEL}  {VOYAGE}     01 NOV 2026', font=font, fill='#27364c')
    draw.text((80, 227), 'Container no.          POD       Seal / condition', font=font, fill='#263951')
    cells = []
    for i, row in enumerate(rows):
        y = 326 + i * 94
        draw.line((65, y + 67, 1540, y + 67), fill='#b4aa94', width=2)
        draw.text((80 + rng.randint(-3, 4), y + rng.randint(-3, 4)), row['id'], font=font, fill='#2e4363')
        pod = displayed.get(row['id'], row['pod'])
        draw.text((660 + rng.randint(-4, 4), y), pod, font=font, fill='#2e4363')
        draw.text((970, y + 2), ['intact', 'seal seen', 'dry', 'OK'][i % 4], font=font, fill='#354967')
        cells.append({'row_id': row['id'], 'field': 'pod', 'value': pod})
    for _ in range(450):
        x, y = rng.randrange(1640), rng.randrange(1260)
        draw.point((x, y), fill='#b6aa91')
    image = image.rotate([-1.7, 2.2, -2.5][case % 3], Image.Resampling.BICUBIC, expand=True, fillcolor='#77736b')
    image = image.filter(ImageFilter.GaussianBlur(.65))
    image.save(path)
    return cells


def build(path, split, case, headers, trap_names, *, oracle):
    out = OUT / split
    out.mkdir(parents=True, exist_ok=False)
    base, rows = make_base(path, case)
    observed = deepcopy(rows)
    by_id = {r['id']: r for r in observed}
    traps = []
    answers = []
    extra_documents = []
    tally_values = {}
    metadata = {r['id']: {'booked_vessel': VESSEL, 'booking_status': 'CONFIRMED'} for r in rows}
    vgm_units = {r['id']: 'kg' for r in rows}
    vgm_values = {r['id']: r['gross_mass_kg'] for r in rows}
    temp_units = {r['id']: 'C' for r in rows}

    def plant(name, index):
        row, actual = observed[index], rows[index]
        cid = row['id']
        field, reason = 'pod', 'cross_document_conflict'
        if name == 'wrong_pol': row['pol'] = 'THBKK'; field, reason = 'pol', 'wrong_pol'
        elif name == 'unbooked_vessel': metadata[cid]['booked_vessel'] = 'SYNTHETIC KOH'; field, reason = 'booked_vessel', 'unbooked_vessel'
        elif name == 'plausible_lb_kg':
            vgm_values[cid] = round(actual['gross_mass_kg'] / 0.45359237)
            # Plausible double-mass remains below the 40-ft ceiling.
            if vgm_values[cid] > 32000: vgm_values[cid] = round(actual['gross_mass_kg'] * 0.45359237)
            field, reason = 'gross_mass_kg', 'cross_document_conflict'
        elif name == 'net_as_vgm': vgm_values[cid] = actual['gross_mass_kg'] - actual['tare_kg']; field, reason = 'gross_mass_kg', 'cross_document_conflict'
        elif name in {'amendment_pod', 'cod'}:
            alternate = next(c['locode'] for c in base['rotation']['calls'][1:] if c['locode'] != actual['pod'])
            text = f'{LABEL}\nFrom: Mali <export@example.invalid>\nSubject: RE: {split} / COD {cid}\nวันที่ 1 พฤศจิกายน 2569 เวลา 18.42 น.\nเรียนฝ่ายเรือ\n{cid} เปลี่ยนปลายทางเป็น {alternate} ตามใบแก้ไขเลขที่ AM-{case:02d}-4 กรุณาใช้ปลายทางใหม่สำหรับเที่ยว {VOYAGE}\nมาลี\n'
            extra_documents.append(('amendment', 'amendment_email.txt', text, 'text/plain'))
            field, reason = 'pod', 'amendment_conflict'
        elif name == 'marine_pollutant_contradiction':
            row['dg'] = dg(3082, 'III', mp=True); field, reason = 'dg.marine_pollutant', 'cross_document_conflict'
        elif name == 'lq_contradiction':
            row['dg'] = dg(1263, 'III', lq=True); field, reason = 'dg.limited_quantity', 'cross_document_conflict'
        elif name == 'fahrenheit':
            row.update(iso_type=('22R1' if row['length_ft']==20 else '45R1'), height_m=(2.591 if row['length_ft']==20 else 2.896), reefer=True, reefer_setpoint_c=-18.0)
            actual.update(iso_type=('22R1' if actual['length_ft']==20 else '45R1'), height_m=(2.591 if actual['length_ft']==20 else 2.896), reefer=True, reefer_setpoint_c=-18.0)
            temp_units[cid] = 'F'; field, reason = 'reefer_setpoint_c', 'fahrenheit_confirmation'
        elif name == 'temperature_range':
            row.update(iso_type=('22R1' if row['length_ft']==20 else '45R1'), height_m=(2.591 if row['length_ft']==20 else 2.896), reefer=True, reefer_setpoint_c=-180.0)
            actual.update(iso_type=('22R1' if actual['length_ft']==20 else '45R1'), height_m=(2.591 if actual['length_ft']==20 else 2.896), reefer=True, reefer_setpoint_c=-18.0)
            field, reason = 'reefer_setpoint_c', 'reefer_temperature_range'
        elif name == 'reefer_iso': row.update(reefer=True, reefer_setpoint_c=-18.0); field, reason = 'reefer', 'reefer_iso_mismatch'
        elif name == 'empty_gross': row['empty'] = True; field, reason = 'empty', 'empty_gross_mismatch'
        elif name == 'tare_band': row['tare_kg'] = 850.0; field, reason = 'tare_kg', 'tare_out_of_band'
        elif name == 'duplicate': field, reason = 'id', 'duplicate_id'
        elif name == 'shut_out':
            extra_documents.append(('amendment', 'rollover_email.txt', f'{LABEL}\nFrom: Somsak <ops@example.invalid>\nSubject: RE: {split} / {cid}\nวันที่ 1 พฤศจิกายน 2569\n{cid} ไม่ทันปิดรับเที่ยว {VOYAGE} ขอถอนออกจากเที่ยวนี้และย้ายไปเรือเที่ยวถัดไปครับ\nสมศักดิ์\n', 'text/plain'))
            field, reason = 'booking_status', 'shut_out'
        elif name == 'missing_vgm': vgm_values[cid] = None; row.pop('gross_mass_kg'); field, reason = 'gross_mass_kg', 'missing_vgm'
        elif name == 'unknown_un': row['dg'] = dg(1263, 'III'); row['dg']['un_number'] = '9999'; field, reason = 'dg.un_number', 'unknown_un_number'
        elif name == 'off_rotation': row['pod'] = 'USLAX'; field, reason = 'pod', 'pod_not_on_rotation'
        elif name == 'missing_setpoint':
            row.update(iso_type=('22R1' if row['length_ft']==20 else '45R1'), height_m=(2.591 if row['length_ft']==20 else 2.896), reefer=True, reefer_setpoint_c=None)
            actual.update(iso_type=('22R1' if actual['length_ft']==20 else '45R1'), height_m=(2.591 if actual['length_ft']==20 else 2.896), reefer=True, reefer_setpoint_c=-18.0)
            field, reason = 'reefer_setpoint_c', 'missing_reefer_setpoint'
        elif name != 'paper_tally_pod': raise ValueError(name)
        if name == 'paper_tally_pod':
            tally_values[cid] = next(c['locode'] for c in base['rotation']['calls'][1:] if c['locode'] != actual['pod'])
        traps.append({'kind': name, 'row_id': cid, 'field': field, 'reason': reason, 'expected': 'question_or_rejection', 'plausible': name not in {'unknown_un','missing_vgm','off_rotation','temperature_range','missing_setpoint'}})
        value = actual.get(field)
        if '.' in field:
            value = actual
            for part in field.split('.'): value = value.get(part) if isinstance(value, dict) else None
        if field == 'booked_vessel': value = VESSEL
        if name == 'duplicate': value = 'keep_one'
        if name == 'shut_out': value = 'shut_out'
        answers.append({'row_id': cid, 'field': field, 'value': value, 'reason': reason})

    for i, name in enumerate(trap_names): plant(name, 4 + i % 4)
    # Every case has an image-only discrepancy; it is never announced in email or sheet.
    if 'paper_tally_pod' not in trap_names: plant('paper_tally_pod', 7)
    # Keep amendments to the synthetic assembly target explicit, and validate the full instance.
    base_by_id = {r['id']: r for r in base['containers']}
    for row in rows:
        row['weight_class'] = load_catalogue().weight_classes.classify(row['gross_mass_kg'])
        masskey = f"mass_kg={float(row['gross_mass_kg']).hex()}" if rule_freeze_v1_enabled() else row['weight_class']
        row['category'] = None if row['dg'] or row['reefer'] or row['special_stowage'] or row['oog'] else f"{row['pod']}:{row['iso_type']}:{masskey}:{'empty' if row['empty'] else 'loaded'}"
        base_by_id[row['id']].update(row)
    base = Instance.model_validate(base).model_dump(mode='json')

    extras = ['Tare (kg)', 'Empty', 'Operating reefer', 'Set temperature', 'DG cargo', 'Booked vessel', 'Status', 'Stowage request']
    si_headers = list(headers) + extras
    si_rows = []
    for i, row in enumerate(observed):
        temp = row.get('reefer_setpoint_c')
        display_temp = '' if temp is None else (f'{temp * 9/5 + 32:.1f} F' if temp_units[row['id']] == 'F' else f'{temp:g} C')
        si_rows.append([row['id'], row['iso_type'], row['pol'], '' if i == 3 else row['pod'],
                        '' if row.get('gross_mass_kg') is None else f"{row['gross_mass_kg']:.0f} kg",
                        f"{row['tare_kg']:.0f} kg", 'Y' if row['empty'] else 'N', 'Y' if row['reefer'] else 'N', display_temp,
                        'Y' if row['dg'] else 'N', metadata[row['id']]['booked_vessel'], 'CONFIRMED', ''])
    if 'duplicate' in trap_names: si_rows.append(deepcopy(si_rows[4 + trap_names.index('duplicate') % 4]))
    si = csv_text(si_headers, si_rows)
    vgm_headers = ['Equipment reference', 'Verified gross', 'Weighing method', 'Authorised signatory', 'Date of declaration'] if case % 2 else ['Unit ID', 'Mass incl. tare', 'Method', 'Signed by', 'Date']
    vgm_rows = [[r['id'], '' if vgm_values[r['id']] is None else f"{vgm_values[r['id']]:.0f} kg", '1', 'N. Chai', '01/11/2569'] for r in observed]
    vgm = csv_text(vgm_headers, vgm_rows)
    booking_lines = [LABEL, f'BOOKING CONFIRMATION  BK-{case:04d}', f'Vessel: {VESSEL}', f'Voyage: {VOYAGE}', 'Load port: THLCH / LAEM CHABANG', 'ETD: 02 NOV 2026 03:00 ICT', 'Status: CONFIRMED', '']
    gate_lines = [LABEL, 'EQUIPMENT INTERCHANGE RECEIPT / LAEM CHABANG', f'Vessel: {VESSEL} / {VOYAGE}', 'Gate in: 01/11/2569 19:20 ICT', '']
    dg_lines = [LABEL, 'MULTIMODAL DANGEROUS GOODS DECLARATION', f'Booking BK-{case:04d}; vessel {VESSEL}; voyage {VOYAGE}', 'Date: 01 November 2026; Shipper: Synthetic Eastern Pack Ltd', '']
    for i, row in enumerate(observed):
        cid = row['id']; info = metadata[cid]
        booking_lines.append(f"{cid}; ISO {row['iso_type']}; POL {row['pol']}; POD {row['pod']}; Vessel {info['booked_vessel']}; Status CONFIRMED; DG {'YES' if row['dg'] else 'NO'}")
        temp = row.get('reefer_setpoint_c')
        temp_text = '' if temp is None else (f'; Setpoint {temp * 9/5 + 32:.1f} F' if temp_units[cid] == 'F' else f'; Setpoint {temp:g} C')
        mass_text = '' if row.get('gross_mass_kg') is None else f"; Gross {row['gross_mass_kg']:.0f} kg"
        gate_lines.append(f"{cid}; ISO {row['iso_type']}; Tare {row['tare_kg']:.0f} kg{mass_text}; {'EMPTY' if row['empty'] else 'LADEN'}; Reefer {'YES' if row['reefer'] else 'NO'}{temp_text}; POD {row['pod']}; Seal SYN{case:02d}{i:03d}; IN GAUGE")
        if row['dg']:
            entries = [row['dg']] + row['dg']['additional']
            declarations = []
            for j, entry in enumerate(entries):
                mp = entry['marine_pollutant']; lq = entry['limited_quantity']
                # Two documents carry contradictory yes/no declarations on plausible DG traps.
                if (cid, 'marine_pollutant_contradiction') in [(t['row_id'], t['kind']) for t in traps]: mp = False
                if (cid, 'lq_contradiction') in [(t['row_id'], t['kind']) for t in traps]: lq = False
                segment = f"UN {entry['un_number']}; Class {entry['imdg_class']}; PG {entry['packing_group']}; Marine pollutant {'YES' if mp else 'NO'}; Limited quantity {'YES' if lq else 'NO'}"
                declarations.append(segment)
                dg_lines.append(f'{cid}; Item {j + 1}; {segment}; 24 fibreboard boxes; cargo gross 480 kg; closed freight container')
                # The independent booking attachment repeats the actual affirmative/negative declarations.
                booking_lines.append(f"{cid}; DG item {j + 1}; UN {entry['un_number']}; Class {entry['imdg_class']}; PG {entry['packing_group']}; Marine pollutant {'YES' if entry['marine_pollutant'] else 'NO'}; Limited quantity {'YES' if entry['limited_quantity'] else 'NO'}")
        else: dg_lines.append(f'{cid}; NON-DANGEROUS GOODS; no IMDG declaration')
    booking = '\n'.join(booking_lines) + '\n'
    gate = '\n'.join(gate_lines) + '\n'
    dgd = '\n'.join(dg_lines) + '\n'
    email = f'{LABEL}\nFrom: Mali <export@example.invalid>\nTo: Vessel planner <plan@example.invalid>\nSubject: FW: BK-{case:04d} / {VESSEL} {VOYAGE}\nวันที่ 1 พฤศจิกายน 2569 เวลา 20.15 น.\n\nพี่คะ ส่งใบจอง ใบชั่งและใบรับตู้เข้าท่ามาให้แล้วค่ะ ตู้เย็นเสียบไฟที่ลานเรียบร้อย คนขับฝากใบเช็คหน้าท่าไว้กับ รปภ. อีกใบ รบกวนรวมเอกสารชุดนี้ให้ก่อนปิดรับคืนนี้ด้วยค่ะ\n\nมาลี\n\n----- Forwarded message -----\nFrom: Somsak <gate@example.invalid>\nSent: 1/11/2569 19.58\nเรื่อง: ใบรับตู้กะค่ำ\nตรวจซีลครบแล้วครับ รูปใบเช็คแนบมาด้วย รถคันสุดท้ายเพิ่งออกจากประตูสาม\n'
    records = [('booking', 'booking_confirmation.txt', booking, 'text/plain'),
               ('shipping_instructions', 'shipping_instructions.csv', si, 'text/csv'),
               ('vgm', 'vgm_declaration.csv', vgm, 'text/csv'),
               ('dg_declaration', 'dg_declaration.txt', dgd, 'text/plain'),
               ('gate_slip', 'eir_gate_slips.txt', gate, 'text/plain'),
               ('email', 'forwarding_email.txt', email, 'text/plain')] + extra_documents
    documents = []
    for role, name, text, mime in records:
        (out / name).write_text(text)
        documents.append({'id': f'{split}-{name.rsplit(".", 1)[0]}', 'path': str((out / name).relative_to(ROOT)), 'mime_type': mime, 'text': text, 'synthetic': True, 'role': role})
    tally_cells = render_tally(out / 'tally_sheet.png', observed, tally_values, case)
    documents.append({'id': f'{split}-tally_sheet', 'path': str((out / 'tally_sheet.png').relative_to(ROOT)), 'mime_type': 'image/png', 'synthetic': True, 'role': 'tally'})
    expected = {'synthetic': True, 'label': 'Independent synthetic ground truth; not a model reading', 'case_label': split, 'source_map': str(path.relative_to(ROOT)), 'source_map_sha256': sha256_file(path), 'base_instance_sha256': sha256_json(base), 'rows': rows, 'traps': traps, 'answers': answers, 'clean_control_ids': [r['id'] for r in rows if r['id'] not in {t['row_id'] for t in traps}], 'tally_cells': tally_cells, 'caption': f'{len(rows)} synthetic boxes; selected from a development map, not a whole ship', 'business_fields': ['id','iso_type','pol','pod','gross_mass_kg','tare_kg','empty','reefer','reefer_setpoint_c','dg'], 'base_amendments': ['THLCH becomes the load port; geometry and yard retained', 'Load list reduced to eight existing yard IDs', 'Business facts replaced by independently authored synthetic declarations', 'Rounded whole-ten-kg VGMs; known setpoints; no CSC rating inferred']}
    dump(out / 'base_instance.json', base)
    dump(out / 'documents.json', documents)
    dump(out / 'expected.json', expected)
    if oracle:
        idoc = next(d for d in documents if d['role'] == 'shipping_instructions')
        vdoc = next(d for d in documents if d['role'] == 'vgm')
        fields = ['id','iso_type','pol','pod','gross_mass_kg','tare_kg','empty','reefer','reefer_setpoint_c','dg','booked_vessel','booking_status','special_stowage']
        maps = []
        for doc, heads, flds in [(idoc,si_headers,fields),(vdoc,vgm_headers,['id','gross_mass_kg',None,None,None])]:
            maps.append({'document': doc['id'], 'header_row': 1, 'columns': [{'column': i+1,'header':h,'field':f, **({'unit':'kg'} if f in {'gross_mass_kg','tare_kg'} else {})} for i,(h,f) in enumerate(zip(heads,flds)) if f]})
        dump(out / 'oracle_reading.json', {'header_maps': maps, 'observations': [], 'duplicates': [], 'notes': ['SYNTHETIC ORACLE, development only; never passed to Spark']})
    return {'split': split, 'rows': len(rows), 'traps': len(traps), 'clean_controls': len(expected['clean_control_ids']), 'source_map': str(path.relative_to(ROOT)), 'files': {p.name: sha256_file(p) for p in sorted(out.iterdir()) if p.is_file()}}


def main():
    from experiments.mvp.pipeline.intake.admission import wait_for_slot
    wait_for_slot()
    OUT.mkdir(parents=True, exist_ok=True)
    freeze = OUT / 'blind_freeze.json'
    if freeze.exists(): raise SystemExit('Blind fixtures are frozen; regeneration forbidden')
    quick = [ROOT / line for line in (ROOT / 'experiments/mvp/frontier/lab/sets/quick_small.txt').read_text().splitlines() if line]
    # Unused quick_small maps selected before any reading/scoring; never opens a sealed map.
    selected = [quick[i] for i in (0,2,4,6,7)]
    names = [
        ['Equipment identity','Size / type','Load terminal code','Final discharge','Shipper VGM'],
        ['Box reference','ISO equipment','Origin port','Destination UNLOCODE','Scale gross weight'],
        ['CTU identification','Equipment size-type','Load POL code','Discharge instruction','Verified cargo unit mass'],
        ['箱號 / Equipment ID','ISO 6346 type','Port loaded','Port discharged','VGM incl tare'],
        ['Equipment serial','ISO size/type code','Loading UN/LOCODE','Destination port code','Certified gross kg'],
    ]
    traps = [
        ['wrong_pol','unbooked_vessel','plausible_lb_kg','amendment_pod'],
        ['net_as_vgm','marine_pollutant_contradiction','lq_contradiction','paper_tally_pod'],
        ['fahrenheit','temperature_range','reefer_iso','empty_gross'],
        ['tare_band','duplicate','cod','shut_out'],
        ['missing_vgm','unknown_un','off_rotation','missing_setpoint'],
    ]
    cases = [build(path, f'blind_{i+1:02d}', i+11, headers, kinds, oracle=False) for i,(path,headers,kinds) in enumerate(zip(selected,names,traps))]
    manifest = {'schema_version':'stowlm.intake-realistic-freeze/1','synthetic':True,'cohort':'realistic_v7_blind','evaluation_split':'blind','frozen_before_tuning':True,'frozen_before_scoring':True,'independent_fixture_author':True,'label':'Five independently authored synthetic eight-box cases, not pipeline sealed maps or a whole ship','cases':cases,'total_boxes':sum(c['rows']for c in cases),'generator_sha256':sha256_file(Path(__file__))}
    dump(freeze,manifest)
    (OUT / 'blind_freeze.sha256').write_text(sha256_file(freeze)+'  blind_freeze.json\n')
    # Development documents are emitted only after blind bytes and header variants are frozen.
    public = [build(quick[5],'development_01',1,DEV_HEADERS[0],['wrong_pol','plausible_lb_kg','paper_tally_pod','amendment_pod'],oracle=True),
              build(quick[8],'development_02',2,DEV_HEADERS[1],['fahrenheit','marine_pollutant_contradiction','tare_band','shut_out'],oracle=True),
              build(quick[1],'demo',3,DEV_HEADERS[2],['paper_tally_pod','net_as_vgm','lq_contradiction','amendment_pod'],oracle=True)]
    dump(OUT / 'development_manifest.json',{'synthetic':True,'cases':public,'blind_freeze_sha256':sha256_file(freeze)})
    print(json.dumps({'blind_manifest':str(freeze.relative_to(ROOT)),'sha256':sha256_file(freeze),'cases':len(cases),'boxes':manifest['total_boxes'],'public_cases':[x['split']for x in public]}))


if __name__ == '__main__': main()
