"""Free synthetic extraction pairs; sealed bytes are written once, never read here.

Disjointness concerns container identities and measured values, not shared language
vocabularies: lengths, ISO types, ports and DG UN/class labels must remain shared.
Templates are authored here, not paraphrases of any protected evaluation set.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from experiments.mvp.pipeline.intake_lora.schema import compact_json, prompt_messages, validate_answer

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
SEED = 14001
CONTEXT = {'pods': ['SGSIN', 'MYPKG', 'VNSGN', 'THLCH'],
           'iso_types': ['22G1', '42G1', '45G1', '22R1', '45R1'], 'dg_classes': ['3', '8', '9']}
DG_PAIRS = [('1263', '3'), ('1789', '8'), ('3480', '9')]
# Each format is a distinct surface family; all members stay in one split.
TEMPLATES = {
    'train': {
        'booking_pipe': 'SYNTHETIC booking | CNTR {id} | ISO {iso} | LENGTH {length} ft | GROSS {mass} | POD {pod} | {reefer} | {dg}',
        'email_request': 'Synthetic email: Please book container {id} ({iso}, {length} feet), gross mass {mass}, discharge {pod}. {reefer}. {dg}. Thanks.',
        'thai_booking': 'ข้อมูลจำลอง จองตู้ {id} แบบ ISO {iso} ยาว {length} ฟุต น้ำหนักรวม {mass} ปลายทาง POD {pod}; {reefer}; {dg}',
        'dump_tsv': 'SYNTHETIC TSV\ncontainer\tiso\tfeet\tgross mass\tpod\tcooling\thazards\n{id}\t{iso}\t{length}\t{mass}\t{pod}\t{reefer}\t{dg}',
        'dispatch_note': 'Synthetic dispatch note -- {pod} discharge for {id}; equipment {iso}; overall length {length} ft; total gross {mass}; {dg}; {reefer}.',
        'forwarded_fragment': '[synthetic forwarded message]\n> box: {id}\n> size: {length} ft / ISO {iso}\n> gross: {mass}\n> unload: {pod}\n> {reefer}\n> {dg}',
        'thai_email': 'ตัวอย่างสังเคราะห์ เรียนทีม booking: ตู้ {id}, ISO {iso}, ความยาว {length} ft, gross {mass}, POD {pod}. ยืนยัน {reefer}. แจ้ง {dg}.',
        'key_value_dump': 'synthetic=1; id={id}; discharge_port={pod}; gross_mass={mass}; equipment={iso}; length_ft={length}; cooling={reefer}; dangerous_goods={dg}',
    },
    'valid': {
        'validation_ledger': 'Synthetic ledger entry: [{id}] destination[{pod}] dimension[{length} feet] weight[{mass}] type[{iso}] thermal[{reefer}] safety[{dg}].',
        'validation_sms': 'SYNTHETIC ops SMS / send {id} to {pod} / {length}ft {iso} / gross {mass} / {reefer} / {dg}',
        'validation_thai_tally': 'บันทึกจำลอง: หมายเลขตู้={id}\nส่งลง={pod}\nชนิดตู้={iso}\nขนาด={length} ฟุต\nมวลรวม={mass}\nความเย็น={reefer}\nสินค้าอันตราย={dg}',
        'validation_form': 'Synthetic booking form\nEquipment identifier: {id}\nISO equipment code: {iso}\nNominal length: {length} feet\nDeclared gross: {mass}\nPort of discharge: {pod}\nRefrigeration declaration: {reefer}\nDG declaration: {dg}',
    },
    'sealed': {
        'sealed_routing_slip': 'Synthetic routing slip — unload at {pod}; unit {id}; size/type {length} ft, {iso}; laden gross {mass}; temperature service: {reefer}; hazard statement: {dg}.',
        'sealed_chat_handover': 'SYNTHETIC chat handover: The box is {id}. Destination is {pod}. It is {length} feet, code {iso}. Gross is {mass}. Cooling: {reefer}. Cargo hazard: {dg}.',
        'sealed_thai_memo': 'ข้อความสังเคราะห์สำหรับส่งต่อ\nตู้คอนเทนเนอร์ {id} จะลงท่า {pod}\nISO {iso} ความยาว {length} ฟุต\nน้ำหนัก gross {mass}\nข้อมูลควบคุมอุณหภูมิ: {reefer}\nข้อมูล DG: {dg}',
        'sealed_csv': 'SYNTHETIC CSV\nPOD,CNTR,TYPE,LENGTH,GROSS,REEFER,DG\n"{pod}","{id}","{iso}","{length} ft","{mass}","{reefer}","{dg}"',
    },
}
SPLITS = {
    'train': {'count': 3000, 'owner': 'SYN', 'serial_start': 100000, 'mass_start': 10000, 'setpoints': [-20, -18, -16]},
    'valid': {'count': 400, 'owner': 'DEM', 'serial_start': 400000, 'mass_start': 18000, 'setpoints': [-12, -10]},
    'sealed': {'count': 200, 'owner': 'TST', 'serial_start': 700000, 'mass_start': 22000, 'setpoints': [-6, -4]},
}


def container_id(owner: str, serial: int) -> str:
    """ISO 6346 syntax and check digit, with synthetic owner codes."""
    prefix = f'{owner}U{serial:06d}'
    letters = {letter: number for letter, number in zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ',
               [n for n in range(10, 39) if n % 11])}
    check = sum((int(char) if char.isdigit() else letters[char]) * (2 ** i)
                for i, char in enumerate(prefix)) % 11 % 10
    return prefix + str(check)


def make_rows(split: str, count: int | None = None):
    """Yield newly authored rows. Do not call for sealed except during its write."""
    spec = SPLITS[split]
    count = spec['count'] if count is None else count
    if not 0 < count <= spec['count']:
        raise ValueError('count outside the fixed split pool')
    families = list(TEMPLATES[split])
    for i in range(count):
        family = families[i % len(families)]
        # Blocks ensure every surface family has successes and all refusal cases.
        case = (i // len(families)) % 12
        iso = CONTEXT['iso_types'][i % len(CONTEXT['iso_types'])]
        is_reefer = iso.endswith('R1')
        setpoint = spec['setpoints'][i % len(spec['setpoints'])] if is_reefer else None
        dg_pair = DG_PAIRS[(i // 5) % 3] if (i // 5) % 4 else None
        mass = spec['mass_start'] + 2 * i
        expected = {'id': container_id(spec['owner'], spec['serial_start'] + i), 'iso_type': iso,
                    'length_ft': 20 if iso.startswith('2') else 40, 'gross_mass_kg': mass,
                    'pod': CONTEXT['pods'][(i // 3) % 4], 'reefer': is_reefer,
                    'reefer_setpoint_c': setpoint,
                    'dg': {'un_number': dg_pair[0], 'imdg_class': dg_pair[1]} if dg_pair else None}
        thai = 'thai' in family
        reef = f'reefer yes, set point {setpoint} C' if is_reefer else 'dry, no reefer, no temperature setpoint'
        dg = f'DG UN{dg_pair[0]}, IMDG class {dg_pair[1]}' if dg_pair else 'non-DG, no dangerous goods'
        if thai:
            reef = f'ตู้เย็น reefer yes ตั้งอุณหภูมิ {setpoint} C' if is_reefer else 'ตู้แห้ง ไม่ใช่ตู้เย็น ไม่มี setpoint'
            dg = f'สินค้าอันตราย UN{dg_pair[0]} IMDG class {dg_pair[1]}' if dg_pair else 'ไม่ใช่สินค้าอันตราย non-DG'
        # Explicit tonnes and comma separators exercise deterministic unit conversion.
        mass_text = f'{mass / 1000:.3f} tonnes' if i % 3 == 0 else f'{mass:,} kg' if i % 3 == 1 else f'{mass} kg'
        values = {'id': expected['id'], 'iso': iso, 'length': expected['length_ft'],
                  'mass': mass_text, 'pod': expected['pod'], 'reefer': reef, 'dg': dg}
        code = None
        if case == 6:
            values['mass'], code = 'not supplied', 'MISSING_FIELD'
        elif case == 7:
            values['mass'], code = f'{mass} or {mass + 1} kg, unconfirmed', 'AMBIGUOUS'
        elif case == 8:
            if dg_pair:
                values['dg'] += f'; same UN{dg_pair[0]} also declared class {"8" if dg_pair[1] != "8" else "3"}; unresolved conflict'
            else:
                values['mass'] += f'; another declaration says {mass + 1} kg; unresolved conflict'
            code = 'CONFLICT'
        elif case == 9:
            values['pod'], code = 'HKHKG', 'OUT_OF_CONTEXT'
        elif case == 10:
            code = 'UNSUPPORTED'
        elif case == 11:
            values['dg'], code = 'DG status not supplied', 'MISSING_FIELD'
        text = TEMPLATES[split][family].format(**values)
        if case == 10:
            # One row is the task's scope; a two-row request must be refused.
            text = 'Extract TWO separate rows, not one. First: ' + text + ' Second: ' + TEMPLATES[split][family].format(**{**values, 'id': container_id(spec['owner'], spec['serial_start'] + 5000 + i)})
        if code:
            expected = {'refusal': {'code': code}}
        context = deepcopy(CONTEXT)
        expected = validate_answer(expected, context)
        messages = prompt_messages(text, context)
        messages.append({'role': 'assistant', 'content': compact_json(expected)})
        yield {'id': f'{split}-{i:05d}', 'family': family, 'text': text,
               'context': context, 'expected': expected, 'messages': messages}


def write_jsonl_exclusive(path: Path, rows) -> dict:
    """Hash each encoded line before writing; never reopen the output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    digest, counts, count = hashlib.sha256(), Counter(), 0
    with path.open('xb') as stream:
        for row in rows:
            line = (compact_json(row) + '\n').encode('utf-8')
            digest.update(line)
            stream.write(line)
            counts[row['expected'].get('refusal', {}).get('code', 'ROW')] += 1
            count += 1
    return {'count': count, 'sha256': digest.hexdigest(), 'outcomes': dict(sorted(counts.items()))}


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(output: Path = HERE) -> dict:
    """Create the whole corpus exactly once. Existing artifacts are never overwritten."""
    output = Path(output).resolve()
    if not output.is_relative_to(HERE):
        raise ValueError('corpus output must stay in intake_lora')
    names = {'train': 'data/train.jsonl', 'valid': 'data/valid.jsonl', 'sealed': 'sealed/sealed.jsonl'}
    manifest_path = output / 'data/manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        # Stat-only existence checks; sealed bytes remain unopened.
        if not all((output / name).is_file() for name in names.values()):
            raise ValueError('incomplete frozen corpus; refusing to rewrite')
        if manifest['generator_sha256'] != _sha(Path(__file__)):
            raise ValueError('generator differs from frozen corpus')
        for split in ('train', 'valid'):
            name = names[split]
            if _sha(output / name) != manifest['files'][name]['sha256']:
                raise ValueError('frozen development data hash mismatch')
        return manifest
    if any((output / name).exists() for name in names.values()):
        raise ValueError('partial corpus already exists; sealed may not be regenerated')
    dgl_path = REPO / 'src/stowage/rules/imdg/data/dgl.json'
    entries = json.loads(dgl_path.read_text())['entries']
    for un, cls in DG_PAIRS:
        if not any(str(e['un_number']).zfill(4) == un and e['hazard_class'] == cls for e in entries):
            raise ValueError(f'UN/class pair absent from repository DGL: {un}/{cls}')
    sources = [Path(__file__), HERE / 'schema.py', dgl_path]
    files = {names[split]: write_jsonl_exclusive(output / names[split], make_rows(split)) for split in names}
    split_metadata = {}
    for split, spec in SPLITS.items():
        split_metadata[split] = {'families': list(TEMPLATES[split]), 'value_pool': {
            'owner': spec['owner'], 'id_serial': [spec['serial_start'], spec['serial_start'] + 5000 + spec['count'] - 1],
            'gross_mass_kg': [spec['mass_start'], spec['mass_start'] + 2 * spec['count'] - 1],
            'reefer_setpoint_c': spec['setpoints']}, 'count': spec['count']}
    manifest = {'schema_version': 'stowlm.intake-data/1', 'synthetic': True, 'seed': SEED,
                'files': files, 'generator_sha256': _sha(Path(__file__)),
                'source_sha256': {str(path.relative_to(REPO)): _sha(path) for path in sources},
                'splits': split_metadata,
                'sealed_policy': 'Written once; SHA256 computed in memory while writing; never reopened until one score.',
                'shared_categorical_values': {'context': CONTEXT, 'length_ft': [20, 40], 'dg_pairs': DG_PAIRS, 'outside_rotation_pod': 'HKHKG'},
                'disjointness': 'Template surface families, synthetic owner/serial identities, gross masses (including conflict alternatives), and reefer setpoints are split-disjoint. Public categorical vocabulary and refusal language are intentionally shared.',
                'limitations': 'Synthetic extraction only; no operational manifest or shipment clearance. Thai is authored synthetic wording, not independently reviewed. DG labels cover three sample UN/class pairs and are not full IMDG declarations.'}
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open('x') as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=HERE)
    args = parser.parse_args()
    manifest = build(args.output)
    print(json.dumps({'files': manifest['files'], 'manifest': str(args.output / 'data/manifest.json')}, indent=2))


if __name__ == '__main__':
    main()
