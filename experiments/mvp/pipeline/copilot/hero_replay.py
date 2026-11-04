"""Offline playback only: never constructs a model or invokes a native tool."""
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_file

FIXTURES = Path(__file__).with_name('fixtures') / 'hero-shot10-delivered'


def replay_shot10(*, folder=FIXTURES):
    folder = Path(folder)
    manifest = json.loads((folder/'playback-manifest.json').read_text())
    for name, digest in manifest['files'].items():
        path = folder/name
        if not path.resolve().is_relative_to(folder.resolve()) or sha256_file(path) != digest:
            raise ValueError('Recorded hero artifact changed: '+name)
    return dict(mode='recorded', recorded=True, synthetic=True,
                label='Recorded tool evidence; playback does not perform a current check or install a plan.',
                conversation=json.loads((folder/'shot10.json').read_text()),
                facts=json.loads((folder/'shot10-facts.json').read_text()))
