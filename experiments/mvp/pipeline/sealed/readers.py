"""Development recordings and the adopted D266 masked live prepass factory."""
from __future__ import annotations

import json
from pathlib import Path

from .common import ROOT, object_hash, read_json, sha256

ADAPTER = ROOT/'experiments/mvp/reader/adapter-v2'


class DevelopmentRecordedParser:
    """Recorded expected answers authorized for the CPU development study only.

    These records are not claimed to be adapter inference. The adopted adapter
    executes once per sealed sentence in the actual3.1 prepass.
    """
    development_only = True

    def __init__(self, path, expected_sha256=None):
        from .driver import public_path
        self.path = public_path(path)
        if expected_sha256 is not None and sha256(self.path) != expected_sha256:
            raise ValueError('Development recording hash differs from the frozen configuration')
        self.recordings = read_json(self.path)['readings']

    def parse(self, sentence, context):
        from experiments.mvp.reader.schema import validate_answer
        key = object_hash(dict(sentence=sentence,context=context))
        if key not in self.recordings:
            raise ValueError('No exact development sentence/context recording')
        raw = self.recordings[key]
        return validate_answer(json.dumps(raw),context),dict(
            parser='DevelopmentRecordedParser',recorded=True,adapter_sha256=None,
            development_only=True,recording_sha256=sha256(self.path),
            origin='Expected-answer development recording; not adapter-v2 inference',
            adopted_reader='adapter-v2; real inference deferred to3.1')


def development_recorded(*, path, expected_sha256=None):
    return DevelopmentRecordedParser(path,expected_sha256)


development_recorded.development_only = True


def adopted_reader(*, adapter_sha256, reader_identity=None):
    """Construct the adopted masked reader once in the serial prepass only."""
    from experiments.mvp.pipeline.reader_final.reader import AdoptedReader, identity
    from .prepare_launch_config import IDENTITY_KEYS
    known = identity()
    if known['name'] != 'swa-last-5' or known['adapter_sha256'] != adapter_sha256:
        raise ValueError('Adopted swa-last-5 hash differs from the frozen configuration')
    pinned = {key: known[key] for key in IDENTITY_KEYS}
    if reader_identity is None or reader_identity != pinned:
        raise ValueError('Adopted masked decoder identity differs from the frozen configuration')
    return AdoptedReader(known=known)
