"""Build the four candidate adapters from the finished trajectory. CPU only.

Constant-learning-rate training ends bouncing around a minimum, and a weight
average sits nearer its centre (stochastic weight averaging, Izmailov et al.,
2018, https://arxiv.org/abs/1803.05407). LoRA stores two factors per layer and
the applied change is their product. This averages each factor, so the result is
a point in parameter space, not the mean of the products. That is sound along one
continuous trajectory, where factors don't flip sign or rotate, and select_adapter
measures the outcome instead of assuming it.

Each candidate is a fresh folder with weights, config and a STOPPED.json carrying
its own hash, so require_stopped_source and the final freeze treat them alike.
Usage: python -m experiments.mvp.reader.v3.swa
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import numpy as np
from safetensors.numpy import load_file, save_file

from experiments.mvp.reader.v3.finish_run import CHECKS, FINAL, RESULTS, ROWS_TOTAL
from experiments.mvp.reader.v3.train import HERE, sha256

CANDIDATES = HERE / 'candidates'
WEIGHTS = 'adapters.safetensors'


def check_folders(checks=CHECKS):
    """Finished check folders in iteration order; save_weights is atomic."""
    return sorted((path for path in checks.iterdir() if path.name.isdigit() and (path / WEIGHTS).is_file()),
                  key=lambda path: int(path.name))


def average(paths):
    """Plain mean of each tensor across paths, in float64, cast back to its dtype."""
    members = [load_file(str(path)) for path in paths]
    if not members or any(member.keys() != members[0].keys() for member in members):
        raise ValueError('checkpoints must hold the same tensors')
    result = {}
    for key, first in members[0].items():
        mean = np.mean([member[key].astype(np.float64) for member in members], axis=0)
        if not np.isfinite(mean).all():
            raise ValueError(f'non-finite average at {key}')
        result[key] = mean.astype(first.dtype)
    return result


def val_losses(log_path):
    rows = [json.loads(line) for line in Path(log_path).read_text().splitlines()]
    return {row['iteration']: row['val_loss'] for row in rows if row.get('event') == 'val-loss'}


def best_by_val_loss(folders, losses):
    """Lowest logged val_loss among saved checks; ties go to the later check."""
    scored = [(losses[int(folder.name)], -int(folder.name), folder) for folder in folders
              if int(folder.name) in losses]
    if not scored:
        raise ValueError('no check has a logged val_loss')
    return min(scored, key=lambda row: row[:2])[2]


def final_source(final=FINAL, checks=None):
    """The stopped adapter, or the last check if training died before stopping."""
    if (final / 'STOPPED.json').is_file():
        return final, False
    return checks[-1], True


def write_candidate(root, name, weights, record, config):
    folder = root / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    if isinstance(weights, Path):
        shutil.copyfile(weights, folder / WEIGHTS)
    else:
        save_file(weights, str(folder / WEIGHTS), metadata={'format': 'mlx'})
    shutil.copyfile(config, folder / 'adapter_config.json')
    digest = sha256(folder / WEIGHTS)
    (folder / 'STOPPED.json').write_text(json.dumps({**record, 'adapter_sha256': digest}, indent=2) + '\n')
    return {'name': name, 'path': str(folder), 'adapter_sha256': digest, **record}


def build(root=CANDIDATES, checks=CHECKS, final=FINAL, log_path=RESULTS / 'gpu-run.jsonl'):
    manifest_path = root / 'manifest.json'
    if manifest_path.exists():  # written last, so a resume never reuses a partial build
        return json.loads(manifest_path.read_text())
    folders = check_folders(checks)
    source, crashed = final_source(final, folders)
    stopped = json.loads((source / 'STOPPED.json').read_text()) if not crashed else {}
    config = source / 'adapter_config.json'
    rows = stopped.get('rows_completed', int(source.name) if crashed else None)
    entries = [write_candidate(root, 'final', source / WEIGHTS, {
        'kind': 'final', 'source': str(source), 'source_sha256': sha256(source / WEIGHTS),
        'rows_completed': rows, 'rows_planned': ROWS_TOTAL, 'training_crashed': crashed,
        'reason': stopped.get('reason', 'training ended without STOPPED.json; last saved check')}, config)]
    best = best_by_val_loss(folders, val_losses(log_path))
    losses = val_losses(log_path)
    entries.append(write_candidate(root, 'best-val-loss', best / WEIGHTS, {
        'kind': 'best-single-check', 'source': str(best), 'source_sha256': sha256(best / WEIGHTS),
        'iteration': int(best.name), 'val_loss': losses[int(best.name)],
        'reason': 'lowest logged val_loss among checks-final'}, config))
    for count in (3, 5):
        members = folders[-count:]
        paths = [member / WEIGHTS for member in members]
        entries.append(write_candidate(root, f'swa-last-{count}', average(paths), {
            'kind': 'weight-average', 'members': [{'iteration': int(member.name), 'sha256': sha256(path)}
                                                  for member, path in zip(members, paths)],
            'reason': f'mean of the last {count} checks, each LoRA factor separately'}, config))
    manifest = {'version': 'reader-v3-candidates/1', 'candidates': entries}
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    print(json.dumps(build(), indent=2))
