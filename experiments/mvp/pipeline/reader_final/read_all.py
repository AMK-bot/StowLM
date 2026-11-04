"""GPU step: every D211 and demo sentence through the adopted reader, serially. Resumable.

Each reading is one line in readings/readings.jsonl, keyed by exact sentence and ship.
Expected answers come from the sources named per group and never reach the reader.
Usage: python -m experiments.mvp.pipeline.reader_final.read_all
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from time import time

from experiments.mvp.pipeline.contract.schema import VoyagePacket, sha256_file
from experiments.mvp.reader.pipeline import ParserOutputError
from experiments.mvp.reader.schema import context_from_instance
from .reader import ROOT, AdoptedReader, reading_key, timing

HERE = Path(__file__).resolve().parent
READINGS = HERE / 'readings'
STORE = READINGS / 'readings.jsonl'
QUEUE = READINGS / 'queue.json'
TIMING = READINGS / 'timing.json'
READER = ROOT / 'experiments/mvp/reader'
LATTICE = ROOT / 'experiments/mvp/pipeline/lattice'
VOICE = ROOT / 'experiments/mvp/voice/runs/v5-1.3'
HERO_FILE = ROOT / 'app/v6_hero.json'
STANDING = 'One port per hatch.'
STAMP = '2026-11-02T00:00:00+00:00'


# Twelve of D211's twenty: all four rule types, tiny and small, both yards, three ships. DG-02 is a
# proven impossibility and hatch-04 had no start on 0.9, so the path's hard exits are measured too.
SELECTED = ('close_bay-01', 'close_bay-03', 'close_bay-05', 'max_tiers-01', 'max_tiers-03', 'max_tiers-05',
            'dg_keep_away-01', 'dg_keep_away-02', 'dg_keep_away-04',
            'one_port_per_hatch-01', 'one_port_per_hatch-04', 'one_port_per_hatch-05')


def d211_cases():
    cases = json.loads((READER / 'binding-cases.json').read_text())['cases']
    return [case for case in cases if case['case_id'] in SELECTED]


def packet(input_path, packet_id, sentences=()):
    path = ROOT / input_path
    return VoyagePacket(packet_id=packet_id, created_utc=STAMP, base_instance={'path': str(path), 'sha256': sha256_file(path)},
                        standing=STANDING, voyage_sentences=[{'text': text, 'origin': 'typed'} for text in sentences],
                        profile='standard').model_dump(mode='json')


def instance_of(input_path, cache):
    """The instance run_packet will assemble, so a reading's ship is the planner's ship."""
    if input_path not in cache:
        from experiments.mvp.pipeline.runner.assembly import assemble
        cache[input_path] = context_from_instance(assemble(VoyagePacket.model_validate(packet(input_path, 'context'))))
    return cache[input_path]


def d211_items(cache):
    items = []
    for case in d211_cases():
        context = instance_of(case['input'], cache)
        items.append(dict(group='d211', case=case['case_id'], sentence=case['sentence'], context=context,
                          expected=case['expected'], demo=False))
        items.append(dict(group='d211-standing', case=case['case_id'], sentence=STANDING, context=context,
                          expected={'schema_version': case['expected']['schema_version'],
                                    'constraint': {'type': 'one_port_per_hatch'}}, demo=False))
    return items


def rules_sentences(text):
    """First column of the RULES.md table, split where one row holds two sentences."""
    found = []
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split('|')]
        if len(cells) > 2 and cells[1] not in ('', 'Instruction', 'base') and not set(cells[1]) <= {'-', ':'}:
            found += [part.strip() for part in cells[1].split('; ') if part.strip()]
    return list(dict.fromkeys(found))


def lattice_items(cache):
    hero = json.loads((LATTICE / 'hero.json').read_text())
    known, inputs = {}, {}
    for role in ('hero', 'backup'):
        inputs[hero[role]['path']] = role
    def add(sentences, rules, path):
        if path in inputs and len(sentences) == len(rules):
            known.update({(path, sentence): rule for sentence, rule in zip(sentences, rules)})
    for path in sorted((LATTICE / 'store').glob('*.json')):
        try:
            row = json.loads(path.read_text())
        except ValueError:  # the lattice campaign may be mid-write
            continue
        add(row.get('sentences') or [], row.get('canonical_rules') or row.get('rules') or [], row.get('instance_path'))
    for story in hero.get('story_candidates', []):
        add([story['sentence']], story['canonical_rules'], hero['hero']['path'])
    for manifest in sorted((LATTICE / 'screening').glob('current-*/manifest.json')):
        data = json.loads(manifest.read_text())
        for row in data.get('instructions', []) + data.get('off_lattice', []):
            add(row.get('sentences') or [], row.get('rules') or [], data.get('instance_path'))
    meaning = {sentence: rule for (_, sentence), rule in known.items()}  # the wording comes from one template per rule
    for sentence in rules_sentences((LATTICE / 'RULES.md').read_text()):
        for path in inputs:
            known.setdefault((path, sentence), None)
    items = []
    for (path, sentence), rule in sorted(known.items(), key=lambda item: (item[0][0], item[0][1])):
        context = instance_of(path, cache)
        items.append(dict(group=f'lattice-{inputs[path]}', case=inputs[path], sentence=sentence, context=context,
                          expected=rule or fits(meaning.get(sentence), context), demo=True))
    return items


def fits(rule, context):
    """A rule borrowed from the other ship counts only if it's legal on this one."""
    from experiments.mvp.reader.schema import validate_answer
    try:
        validate_answer(json.dumps(rule), context)
    except (ValueError, TypeError):
        return None
    return rule


def voice_items():
    hero = json.loads(HERO_FILE.read_text())
    shown = {hero['clip']['id'].split('-', 1)[1], hero.get('live_reading', '').split('-', 1)[-1]}
    cohort = json.loads((VOICE / 'cohort.json').read_text())
    rows = {row['id']: row for row in cohort['english']}  # the Thai block is a skipped-state record, not rows
    items = []
    for path in sorted((VOICE / 'checkpoints').glob('*.json')):
        clip = json.loads(path.read_text())
        row = rows.get(clip.get('source_id'))
        sentence = clip.get('transcript')
        if row is None or not isinstance(sentence, str) or not sentence.strip():
            continue
        items.append(dict(group='voice-' + clip.get('condition', path.stem.rsplit('-', 1)[1]), case=path.stem,
                          sentence=sentence, context=row['context'], expected=row['expected'],
                          demo=clip['source_id'] in {f'reader-{tail}' for tail in shown} or clip['source_id'] in shown))
    return items


def refusal_items(cache):
    manifest = json.loads((READER / 'runs/refusals/manifest.json').read_text())
    def shown(case):
        # With no registered meaning, compare with what the card shows now.
        return json.loads((READER / 'runs/refusals/parses' / f"{case['case_id']}.json").read_text())['parsed']

    return [dict(group='refusal-card', case=row['case_id'], sentence=row['sentence'], context=instance_of(row['input'], cache),
                 expected=row.get('expected') or shown(row), demo=True) for row in manifest['cases']]


def build_queue():
    cache = {}
    return d211_items(cache) + lattice_items(cache) + refusal_items(cache) + voice_items()


def load_rows(path=STORE):
    rows = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            rows[row['key']] = row
    return rows


def read_one(reader, item):
    key = reading_key(item['sentence'], item['context'])
    try:
        _, evidence = reader.parse(item['sentence'], item['context'])
        status = 'read'
    except ParserOutputError as error:
        evidence, status = error.evidence, 'malformed'
    return {'key': key, 'sentence': item['sentence'], 'status': status, 'evidence': evidence, 'at': time()}


def read_all(queue, reader, path=STORE, wait=lambda: None):
    rows = load_rows(path)
    if path.exists() and path.read_bytes()[-1:] not in (b'', b'\n'):
        with path.open('a') as stream:
            stream.write('\n')
    for item in queue:
        if reading_key(item['sentence'], item['context']) in rows:
            continue
        wait()
        row = read_one(reader, item)
        with path.open('a') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        rows[row['key']] = row
    return rows


def main():
    from experiments.mvp.reader.v3.finish_run import wait_for_admission
    READINGS.mkdir(exist_ok=True)
    queue = build_queue()
    QUEUE.write_text(json.dumps(queue, ensure_ascii=False, indent=1) + '\n')
    wait_for_admission()
    reader = AdoptedReader()
    new = not STORE.exists()
    reader.warm()
    rows = read_all(queue, reader, wait=wait_for_admission)
    if new:
        TIMING.write_text(json.dumps({**timing(list(rows.values()), reader.backend.load_s),
                                      'identity': reader.known}, indent=2) + '\n')
    print(json.dumps({'queued': len(queue), 'unique_readings': len(rows)}))


if __name__ == '__main__':
    main()
