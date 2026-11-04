"""D224 v3 always runs; immutable stopped-source selection, fresh Adam.

Freeze chooses finalized v2 iff its stop hash matches, otherwise stopped v1.
Training consumes that one frozen selection even if v2 finishes afterwards.
Only train/valid data-domain rows enter preflight; no sealed/test inputs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from experiments.mvp.reader import train_v2 as _v2
from experiments.mvp.reader.train import TokenDataset

HERE = Path(__file__).resolve().parent
READER = HERE.parent
ROOT = HERE.parents[3]
DATA = HERE / 'data-domain'
sha256 = _v2.sha256
epoch_groups = _v2.epoch_groups
HARD_STOP_LIMIT_S = 14400  # D224: four hours for v3; train_v2's own three-hour limit stays, because v2's freeze binds that file


_validate_v2 = _v2.validate_recipe


def validate_recipe(cfg, source, destination):
    if cfg['hard_stop_seconds'] > HARD_STOP_LIMIT_S:
        raise ValueError('hard stop exceeds four hours')
    return _validate_v2({**cfg, 'hard_stop_seconds': min(cfg['hard_stop_seconds'], 10800)}, source, destination)


_v2.validate_recipe = validate_recipe  # _v2.preflight looks it up by name; v2's own process never imports this module
save_weights = _v2.save_weights
run_train = _v2.run_train


def check_admission(path: Path | None = None, now: float | None = None) -> dict:
    """Reject stale/oversubscribed shared fleet state before local work."""
    path = path or ROOT / 'experiments/fleet/core-state.json'
    state = json.loads(path.read_text())
    age = (time.time() if now is None else now) - float(state['t'])
    mask = state['mask']
    mask = int(mask, 0) if isinstance(mask, str) else int(mask)
    if not (0 <= age < 120 and mask > 0 and mask.bit_count() >= 8
            and 0 <= float(state['load1']) <= 45):
        raise ValueError('CPU admission denied: require fresh <120s, >=8 mask cores, load1<=45')
    return {'mask': mask, 'load1': state['load1'], 'age_seconds': age}


def require_stopped_source(source=None):
    """V2 needs its finalized weight hash; legacy v1 stop has reason+at only."""
    source = Path(source) if source is not None else READER / 'adapter-v2'
    if not (source / 'STOPPED.json').is_file():
        raise ValueError('source adapter must be stopped and immutable before v3 preflight/freeze')
    stopped = json.loads((source / 'STOPPED.json').read_text())
    expected = stopped.get('adapter_sha256')
    if expected is not None:
        if expected != sha256(source / 'adapters.safetensors'):
            raise ValueError('source adapter differs from its finalized STOPPED.json hash')
    elif not (source.name == 'adapter-v1' and stopped.get('reason') and stopped.get('at')):
        raise ValueError('source requires a finalized STOPPED.json hash (legacy v1 needs reason+at)')
    # Legacy v1's bytes are bound by source-selection.json, not a fabricated stop hash.
    return stopped


def _source_record(source):
    source = Path(source).resolve()
    stopped = require_stopped_source(source)
    return {'source_adapter': str(source), 'source_name': source.name,
            'source_adapter_sha256': sha256(source / 'adapters.safetensors'),
            'source_adapter_config_sha256': sha256(source / 'adapter_config.json'),
            'source_stop_sha256': sha256(source / 'STOPPED.json'),
            'stop_binding': 'finalized-stop-hash' if stopped.get('adapter_sha256') else 'legacy-v1-computed-hash'}


def load_source_selection(selection_path=None):
    path = Path(selection_path) if selection_path is not None else HERE / 'results/source-selection.json'
    selected = json.loads(path.read_text())
    if selected.get('version') != 'reader-v3-source-selection/1':
        raise ValueError('unknown source selection version')
    current = _source_record(Path(selected['source_adapter']))
    if any(selected.get(key) != value for key, value in current.items()):
        raise ValueError('selected stopped source changed; never reroute or reselect')
    return selected


def select_source(selection_path=None, reader_dir=None):
    """Explicit freeze-time mutation only; exclusive creation pins a one-time call.

    No v2 score/gate is inspected. A missing, unfinished or hash-mismatched v2
    selects stopped v1. Once written, selection never falls forward to v2.
    """
    path = Path(selection_path) if selection_path is not None else HERE / 'results/source-selection.json'
    reader_dir = Path(reader_dir) if reader_dir is not None else READER
    if path.exists():
        return load_source_selection(path)
    try:
        selected = _source_record(reader_dir / 'adapter-v2')
    except (OSError, ValueError, KeyError, TypeError):
        selected = _source_record(reader_dir / 'adapter-v1')
    selected.update(version='reader-v3-source-selection/1',
                    selected_utc=datetime.now(timezone.utc).isoformat(),
                    policy='D224: v3 always runs; finalized v2 hash else stopped v1; immutable after selection')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x') as stream:
            stream.write(json.dumps(selected, indent=2) + '\n')
    except FileExistsError:
        return load_source_selection(path)
    return load_source_selection(path)


def frozen_source(selection_path=None, recipe_freeze=None):
    """Read verified freeze and selection; this path cannot create either file."""
    from experiments.mvp.reader.freeze_v2 import verify
    path = Path(selection_path) if selection_path is not None else HERE / 'results/source-selection.json'
    recipe = Path(recipe_freeze) if recipe_freeze is not None else HERE / 'results/recipe-freeze.json'
    frozen = json.loads(recipe.read_text())
    verify(frozen, ROOT)
    if frozen.get('source_selection_sha256') != sha256(path):
        raise ValueError('recipe does not pin this source selection')
    selected = load_source_selection(path)
    if frozen.get('source_adapter_sha256') != selected['source_adapter_sha256']:
        raise ValueError('recipe selected source hash mismatch')
    return selected, frozen


def preflight(data_dir=DATA, source=None, destination=HERE / 'adapter-v3',
              config=HERE / 'config.json', model=None, *, selection_path=None, recipe_freeze=None):
    selected, frozen = frozen_source(selection_path, recipe_freeze)
    chosen = Path(selected['source_adapter'])
    if source is not None and Path(source).resolve() != chosen:
        raise ValueError('source override differs from immutable frozen selection')
    for path in [Path(config), *[Path(data_dir) / f'{name}.jsonl' for name in ('train', 'valid')]]:
        name = str(path.resolve().relative_to(ROOT))
        if frozen['files_sha256'].get(name) != sha256(path):
            raise ValueError('preflight input is outside frozen recipe')
    configured_model = (ROOT / json.loads(Path(config).read_text())['model']).resolve()
    if model is not None and Path(model).resolve() != configured_model:
        raise ValueError('model override differs from frozen recipe')
    result = _v2.preflight(Path(data_dir), chosen, destination, Path(config), model)
    result[-1].update(source_selection_sha256=sha256(selection_path or HERE / 'results/source-selection.json'),
                      source_policy=selected['policy'])
    return result


def data_preflight(data_dir=DATA, config=HERE / 'config.json', model=None):
    """Read/tokenize train/valid only; never select or touch mutable weights."""
    from experiments.mvp.reader.evaluate import read_jsonl
    from experiments.mvp.reader.train import check_model, validate_splits
    if 'sealed' in str(data_dir).lower():
        raise ValueError('sealed inputs forbidden')
    cfg = json.loads(config.read_text())
    model = model or ROOT / cfg['model']
    check_model(model)
    rows, valid = [read_jsonl(data_dir / f'{name}.jsonl') for name in ('train', 'valid')]
    manifest = validate_splits(rows, valid)
    manifest.update(config_sha256=sha256(config), source_adapter_validation_deferred=True,
                    data_sha256={f'{name}.jsonl': sha256(data_dir / f'{name}.jsonl')
                                 for name in ('train', 'valid')})
    return cfg, model, rows, valid, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=DATA)
    parser.add_argument('--source-adapter', type=Path,
                        help='optional assertion against frozen selection; cannot override it')
    parser.add_argument('--adapter-dir', type=Path, default=HERE / 'adapter-v3')
    parser.add_argument('--config', type=Path, default=HERE / 'config.json')
    parser.add_argument('--model', type=Path)
    parser.add_argument('--tokenize-check', action='store_true')
    parser.add_argument('--data-only', action='store_true',
                        help='read train/valid and tokenizer only; no source selection or weights')
    args = parser.parse_args()
    admission = check_admission()
    if args.data_only:
        cfg, model, train, valid, manifest = data_preflight(args.data_dir, args.config, args.model)
    else:
        cfg, model, train, valid, manifest = preflight(args.data_dir, args.source_adapter,
                                                     args.adapter_dir, args.config, args.model)
    if args.tokenize_check:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(str(model), local_files_only=True)
        manifest['token_lengths'] = {
            name: {'count': len(rows), 'max': max(len(item[0]) for item in
                   TokenDataset(rows, tokenizer, cfg['max_seq_length']).items)}
            for name, rows in [('train', train), ('valid', valid)]}
    print(json.dumps(dict(manifest, config=cfg, admission=admission, device_loaded=False), indent=2))


if __name__ == '__main__':
    main()
