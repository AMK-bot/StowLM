"""Read p110-lattice's candidate hero wordings; keep those that bind exactly to the stored rule.

Candidates are found by name under lattice/: any hero*candidate*, hero*wording* or natural*wording*
JSON file. Each dict holding a sentence and a stored rule is one candidate, in file order, so the
first kept one is p110's preferred. Nothing here trains on them. A misreading is reported, not
fixed, and the next is tried. Exit code 3 means no candidate file exists yet.
Usage: python -m experiments.mvp.pipeline.reader_final.hero_candidates
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from experiments.mvp.reader.pipeline import ParserOutputError
from .plan_cases import same_meaning
from .read_all import HERE, LATTICE, READINGS, instance_of
from .reader import AdoptedReader, reader_label

OUTPUT = READINGS / 'hero-candidates.json'
PATTERNS = ('hero*candidate*.json', 'hero*wording*.json', 'natural*wording*.json')
SENTENCE_KEYS = ('sentence', 'wording', 'text', 'instruction')
RULE_KEYS = ('canonical_rule', 'canonical_rules', 'rule', 'rules', 'expected')


def files(root=LATTICE):
    found = sorted({path for pattern in PATTERNS for path in root.rglob(pattern)
                    if 'pytest' not in str(path) and 'test' not in path.name})
    return found


def walk(value):
    if isinstance(value, dict):
        sentence = next((value[key] for key in SENTENCE_KEYS if isinstance(value.get(key), str)), None)
        rule = next((value[key] for key in RULE_KEYS if value.get(key)), None)
        if sentence and rule:
            yield {'sentence': sentence, 'rule': rule[0] if isinstance(rule, list) else rule}
            return
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def find(path):
    return list(walk(json.loads(Path(path).read_text())))


def judge(candidates, reader, context):
    rows = []
    for item in candidates:
        try:
            answer, evidence = reader.parse(item['sentence'], context)
            raw = evidence['raw']
            binds = same_meaning(json.loads(raw), item['rule'], context)
            verdict = 'binds_exactly' if binds else 'misread'
        except ParserOutputError as error:
            evidence, raw, binds, verdict = error.evidence, None, False, 'malformed'
        rows.append({**item, 'binds': binds, 'verdict': verdict, 'reading': raw,
                     'label': reader_label(evidence, reader.known), 'evidence': evidence})
    return rows


def main():
    paths = files()
    if not paths:
        print('no candidate file yet')
        sys.exit(3)
    candidates = list({item['sentence']: item for path in paths for item in find(path)}.values())
    hero = json.loads((LATTICE / 'hero.json').read_text())['hero']['path']
    reader = AdoptedReader()
    reader.warm()
    rows = judge(candidates, reader, instance_of(hero, {}))
    kept = [row for row in rows if row['binds']]
    OUTPUT.write_text(json.dumps({'adopted': reader.known, 'sources': [str(path) for path in paths], 'kept': kept,
                                  'misread': [row for row in rows if not row['binds']],
                                  'read': len(rows), 'trained_on_these': False}, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'read': len(rows), 'kept': len(kept)}))


if __name__ == '__main__':
    main()
