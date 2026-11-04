"""Adopted local reader, or explicitly labelled authenticated hero playback."""
from __future__ import annotations

import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_file, sha256_json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FINAL = HERE.parent / 'reader_final'
SELECTION = HERE.parent / 'radio/hero-selection.json'
RECORDING = FINAL / 'readings/hero-candidates.json'
RECORDING_SHA256 = '9fc8f99baf54a6da6630e9fa97ae5350688e0cc956d12b0efa9a03e403c184f8'
RECORDED_INSTANCE = ROOT / 'experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_500/f60-low/seed-192200002.json'
RECORDED_INSTANCE_SHA256 = 'cabff3d68d56e64ba9956761bcd0ca2abfa5077d41296db35b630efc11b04678'
IDENTITY_FIELDS = ('name', 'adapter_sha256', 'decoder_sha256', 'domain_words_sha256', 'decoding_policy')


def _read(path):
    return json.loads(Path(path).read_text())


def _recorded_context():
    from experiments.mvp.reader.schema import context_from_instance
    from stowage.model import Instance
    instance = Instance.model_validate(_read(RECORDED_INSTANCE))
    if sha256_json(instance.model_dump(mode='json')) != RECORDED_INSTANCE_SHA256:
        raise ValueError('recorded hero reader context source changed')
    return context_from_instance(instance)


class _Parser:
    def __init__(self, inner, known, *, recorded=False):
        self.inner = inner
        self.known = known
        self.recorded_offline = recorded

    def warm(self):
        """Call before run_packet's reader deadline, only in an authorised Metal process."""
        if hasattr(self.inner, 'warm'):
            self.inner.warm()

    def parse(self, sentence, context):
        if sentence == '':
            from experiments.mvp.reader.schema import validate_answer
            raw = canonical_json({'schema_version': 'stowlm.reader/2', 'refusal': {
                'refused': True, 'code': 'not_an_instruction',
                'reason': 'No standing instruction was supplied.'}})
            return validate_answer(raw, context), {
                'parser': 'EmptyStanding', 'model': None, 'adapter_sha256': None,
                'source_sha256': sha256_file(__file__), 'raw': raw,
                'deterministic': True, 'recorded': False,
                'label': 'No standing instruction; deterministic empty-input handling'}
        answer, evidence = self.inner.parse(sentence, context)
        return answer, {**evidence, 'name': self.known['name']}


def adopted_parser(mode='live', selection_path=SELECTION):
    """Return ``(parser, metadata)``; never silently replace live inference.

    Construction loads no model. A live caller may call ``parser.warm()`` before
    run_packet; recorded mode authenticates exact bytes and exact ship context.
    ``selection_path`` is the radio hero-selection JSON, or None for no selected
    sentence restriction. Playback contains only successfully read candidates.
    """
    if mode not in {'live', 'recorded'}:
        raise ValueError('adopted reader mode must be live or recorded')
    from experiments.mvp.pipeline.reader_final import reader
    known = reader.identity()
    if known['name'] != 'swa-last-5':
        raise ValueError('hero reader must identify adopted swa-last-5')
    selection = _read(selection_path) if selection_path is not None else None
    if selection is not None and any(selection.get('reader', {}).get(key) != known[key]
                                     for key in IDENTITY_FIELDS):
        raise ValueError('hero selection reader differs from adopted identity')
    metadata = {**{key: known[key] for key in IDENTITY_FIELDS}, 'mode': mode,
                'display_name': 'StowLM Reader ' + known['name'],
                'model': reader.MODEL_NAME,
                'label': reader.LABEL if mode == 'live' else 'Recorded reading',
                'source_sha256': sha256_file(reader.__file__),
                'selection_sha256': sha256_file(selection_path) if selection_path is not None else None}
    timing_path = FINAL / 'readings/timing.json'
    timing = _read(timing_path)
    if any(timing.get('identity', {}).get(key) != known[key] for key in IDENTITY_FIELDS):
        raise ValueError('reader timing differs from adopted identity')
    metadata['measured_timing'] = {key: timing[key] for key in (
        'sentences', 'model_load_s', 'cold_start_s', 'warm_median_s', 'warm_p90_s')}
    metadata['timing_source_sha256'] = sha256_file(timing_path)
    if mode == 'live':
        return _Parser(reader.AdoptedReader(known=known), known), metadata
    if RECORDING.is_symlink() or sha256_file(RECORDING) != RECORDING_SHA256:
        raise ValueError('untrusted adopted hero reader recording: published SHA256 differs')
    recorded = _read(RECORDING)
    if any(recorded.get('adopted', {}).get(key) != known[key] for key in IDENTITY_FIELDS):
        raise ValueError('hero recording differs from adopted reader identity')
    context = _recorded_context()
    rows = []
    for original in recorded['kept']:
        evidence = dict(original['evidence'])
        if reader.reader_label(evidence, known) != reader.LABEL:
            raise ValueError('hero recording lacks adopted masked decoder provenance')
        evidence.update(name=known['name'], recorded=True, label='Recorded reading', recording_sha256=RECORDING_SHA256,
                        recording_source=str(RECORDING.relative_to(ROOT)),
                        original_wall_s=evidence.get('wall_s'))
        rows.append({'key': reader.reading_key(original['sentence'], context),
                     'status': 'read', 'evidence': evidence})
    if selection is not None and reader.reading_key(selection['hero_sentence'], context) not in {
            row['key'] for row in rows}:
        raise ValueError('selected hero sentence has no authenticated adopted reading')
    metadata.update(recording_sha256=RECORDING_SHA256, recording_path=str(RECORDING.relative_to(ROOT)),
                    recording_context_sha256=sha256_json(context),
                    recorded_instance_sha256=RECORDED_INSTANCE_SHA256,
                    recording_evidence=[row['evidence'] for row in rows])
    return _Parser(reader.StoredReadings(rows), known, recorded=True), metadata
