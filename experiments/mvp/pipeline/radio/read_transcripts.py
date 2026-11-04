"""Read the recorded radio transcripts on the leader's Metal host.

Nothing loads MLX at import time. Tests inject a reader and a ship context.
Usage: python -m experiments.mvp.pipeline.radio.read_transcripts
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.mvp.pipeline.reader_final.plan_cases import same_meaning
from experiments.mvp.pipeline.reader_final.read_all import LATTICE, instance_of
from experiments.mvp.pipeline.reader_final.reader import AdoptedReader, reader_label
from experiments.mvp.reader.pipeline import ParserOutputError

HERE = Path(__file__).resolve().parent


def hero_context():
    hero = json.loads((LATTICE / 'hero.json').read_text())['hero']['path']
    return instance_of(hero, {})


def read_transcripts(manifest, reader=None, context=None):
    """Warm once, read every clip, and compare its raw rule with its expectation.

    ``manifest`` is the JSON-able manifest, its clip list, or a manifest path.
    ``binds`` measures semantic agreement, not a checked plan or confirmation.
    """
    if isinstance(manifest, (str, Path)):
        manifest = json.loads(Path(manifest).read_text())
    clips = manifest['clips'] if isinstance(manifest, dict) else manifest
    context = hero_context() if context is None else context
    reader = AdoptedReader() if reader is None else reader
    known = getattr(reader, 'known', {})
    rows, summary = [], {}
    reader.warm()
    for clip in clips:
        transcript = clip['transcript']
        expected = clip.get('expected', clip.get('expected_rule'))
        if expected is None:
            raise ValueError('clip has no expected rule')
        try:
            _, evidence = reader.parse(transcript, context)
            raw = evidence.get('raw')
            binds = bool(same_meaning(raw, expected, context))
            verdict = 'binds_exactly' if binds else 'misread'
        except ParserOutputError as error:
            evidence = error.evidence
            raw, binds, verdict = evidence.get('raw'), False, 'malformed'
        rows.append({**clip, 'transcript': transcript, 'reading': raw,
                     'binds': binds, 'verdict': verdict,
                     'label': reader_label(evidence, known), 'evidence': evidence})
        counts = summary.setdefault(clip['condition'], {'total': 0, 'binds': 0, 'misread': 0, 'malformed': 0})
        counts['total'] += 1
        counts['binds' if binds else verdict] += 1
    return {'schema_version': 'stowlm.radio-readings/1', 'adopted': known,
            'context': context, 'rows': rows, 'summary': summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=HERE / 'manifest.json')
    parser.add_argument('--output', type=Path, default=HERE / 'readings.json')
    args = parser.parse_args()
    from .capacity import wait_for_capacity
    wait_for_capacity()
    result = read_transcripts(args.manifest)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()
