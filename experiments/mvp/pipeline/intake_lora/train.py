"""CPU preflight and a fresh bare-base LoRA loop. Never reads sealed rows."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import partial
import hashlib
import json
import math
from pathlib import Path
import random
import time

from experiments.mvp.reader.train import TokenDataset, check_model

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
DATA = HERE / 'data'
ADAPTER = HERE / 'adapter-intake-v1'


class DeadlineReached(Exception):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def reject_sealed(path):
    # Inspect the resolved path as well, so a train/valid symlink cannot escape.
    if 'sealed' in str(path).lower() or 'sealed' in str(Path(path).resolve()).lower():
        raise ValueError('sealed inputs forbidden in training and CPU preflight')


def validate_recipe(cfg):
    if cfg.get('model') != 'models/glimmer-30b-4bit':
        raise ValueError('only bare models/glimmer-30b-4bit is permitted')
    if any(key in cfg for key in ('source_adapter', 'adapter_path', 'resume')):
        raise ValueError('fresh adapter only; reader adapters and resume forbidden')
    if cfg.get('adapter_dir') != str(ADAPTER.relative_to(ROOT)):
        raise ValueError('fresh adapter-intake-v1 destination required')
    if cfg['epochs'] != 1 or cfg['batch_size'] != 1:
        raise ValueError('one pass with microbatch size one required')
    for key in ('max_train_examples', 'grad_accumulation_steps', 'max_seq_length',
                'steps_per_report', 'steps_per_eval', 'steps_per_save', 'val_batches'):
        if type(cfg[key]) is not int or cfg[key] < 1:
            raise ValueError(f'{key} must be a positive integer')
    if not 0 < cfg['hard_stop_seconds'] <= 2700:
        raise ValueError('hard wall must be positive and at most 2700 seconds')
    if (cfg['rank'], cfg['scale'], cfg['dropout'], cfg['num_layers']) != (8, 20.0, 0.0, 16):
        raise ValueError('v3 LoRA architecture required')


def selected_rows(rows, cfg):
    """Select once by seed, then run the selected examples once, without padding."""
    indices = list(range(len(rows)))
    random.Random(cfg['seed']).shuffle(indices)
    return [rows[i] for i in indices[:cfg['max_train_examples']]]


def _rows(path):
    reject_sealed(path)
    with Path(path).open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def validate_splits(rows, valid):
    if not rows or not valid:
        raise ValueError('nonempty train and valid required')
    ids, inputs = set(), set()
    for row in rows + valid:
        if row['id'] in ids:
            raise ValueError('duplicate training or validation id')
        ids.add(row['id'])
        if not row.get('family') or not row.get('text') or not isinstance(row.get('context'), dict):
            raise ValueError('family, text and ship/voyage context required')
        key = (row['text'].strip().casefold(), json.dumps(row['context'], sort_keys=True))
        if key in inputs:
            raise ValueError('duplicate text/context in train/valid')
        inputs.add(key)
        messages = row['messages']
        if [m['role'] for m in messages] != ['system', 'user', 'assistant']:
            raise ValueError('system/user/assistant messages required')
        expected = json.loads(row['expected']) if isinstance(row['expected'], str) else row['expected']
        if json.loads(messages[-1]['content']) != expected:
            raise ValueError('completion differs from expected answer')
        if 'sealed' in str(row.get('source', '')).lower():
            raise ValueError('sealed source forbidden')
    train_families, valid_families = ({r['family'] for r in split} for split in (rows, valid))
    if train_families & valid_families:
        raise ValueError('train/valid template families overlap')
    return {'train_count': len(rows), 'valid_count': len(valid),
            'train_families': sorted(train_families), 'valid_families': sorted(valid_families)}


def data_preflight(data_dir=DATA, config=HERE / 'config.json', model=None):
    data_dir, config = Path(data_dir), Path(config)
    reject_sealed(data_dir)
    reject_sealed(config)
    cfg = json.loads(config.read_text())
    validate_recipe(cfg)
    configured = (ROOT / cfg['model']).resolve()
    if model is not None and Path(model).resolve() != configured:
        raise ValueError('model override cannot differ from bare Glimmer base')
    check_model(configured)
    manifest_path = data_dir / 'manifest.json'
    reject_sealed(manifest_path)
    source_manifest = json.loads(manifest_path.read_text())
    paths = [data_dir / f'{s}.jsonl' for s in ('train', 'valid')]
    rows, valid = [_rows(p) for p in paths]
    report = validate_splits(rows, valid)
    for path, split_rows in zip(paths, (rows, valid)):
        record = source_manifest['files'][f'data/{path.name}']
        if record['sha256'] != sha256(path) or record['count'] != len(split_rows):
            raise ValueError('data manifest hash/count mismatch')
    selected = selected_rows(rows, cfg)
    report.update(config_sha256=sha256(config), manifest_sha256=sha256(manifest_path),
                  model_config_sha256=sha256(configured / 'config.json'),
                  data_sha256={p.name: sha256(p) for p in paths},
                  selected_train_count=len(selected),
                  selected_train_ids_sha256=hashlib.sha256(
                      json.dumps([r['id'] for r in selected], separators=(',', ':')).encode()).hexdigest(),
                  optimizer_updates=math.ceil(len(selected) / cfg['grad_accumulation_steps']),
                  final_accumulation_rows=(len(selected) - 1) % cfg['grad_accumulation_steps'] + 1,
                  source_adapter=None, optimizer_policy='fresh Adam, fresh LoRA from bare base',
                  device_loaded=False, sealed_read=False)
    return cfg, configured, rows, valid, report


def token_preflight(cfg, model, rows, valid, report, *, tokenizer=None,
                    output=HERE / 'tokenizer-preflight.json'):
    if tokenizer is None:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(str(model), local_files_only=True)
    lengths = {}
    for name, split in [('train', rows), ('valid', valid), ('selected_train', selected_rows(rows, cfg))]:
        data = TokenDataset(split, tokenizer, cfg['max_seq_length'])
        sizes = [len(item[0]) for item in data.items]
        lengths[name] = {'count': len(sizes), 'max': max(sizes), 'total': sum(sizes)}
    report = {**report, 'token_lengths': lengths, 'max_seq_length': cfg['max_seq_length'],
              'tokenizer_sha256': {name: sha256(model / name) for name in (
                  'tokenizer.json', 'tokenizer_config.json', 'chat_template.jinja')
                  if (model / name).exists()},
              'prompt_prefix_verified': True, 'truncation_allowed': False,
              'at': datetime.now(timezone.utc).isoformat()}
    if output is not None:
        Path(output).write_text(json.dumps(report, indent=2) + '\n')
    return report


def recipe_files():
    # Freeze at explicit GPU launch, after every CPU preparation edit is complete.
    sources = sorted(p for p in HERE.glob('*.py') if not p.name.startswith('test_'))
    sources += [HERE / 'config.json', HERE / 'GATE.md', HERE / 'launch.sh',
                DATA / 'train.jsonl', DATA / 'valid.jsonl', DATA / 'manifest.json',
                HERE / 'tokenizer-preflight.json']
    model = ROOT / 'models/glimmer-30b-4bit'
    sources += [model / name for name in ('config.json', 'tokenizer.json',
                                        'tokenizer_config.json', 'chat_template.jinja')]
    # Pin imported training implementation as well as this package's recipe.
    sources += [ROOT / name for name in ('experiments/mvp/reader/train.py',
                                        'experiments/mvp/reader/evaluate.py',
                                        'experiments/mvp/reader/schema.py',
                                        'experiments/mvp/reader/v3/decoder.py',
                                        'src/llm/stowlm/train.py')]
    return sources


def check_recipe_freeze(path=HERE / 'recipe-freeze.json'):
    frozen = json.loads(Path(path).read_text())
    current = {str(p.relative_to(ROOT)): sha256(p) for p in recipe_files()}
    if frozen.get('files_sha256') != current or frozen.get('source_adapter') is not None:
        raise ValueError('recipe changed after GPU launch freeze')
    return frozen


def freeze_recipe(path=HERE / 'recipe-freeze.json'):
    path = Path(path)
    if path.exists():
        return check_recipe_freeze(path)
    frozen = {'version': 'intake-lora-recipe/1', 'source_adapter': None,
              'model': 'models/glimmer-30b-4bit',
              'files_sha256': {str(p.relative_to(ROOT)): sha256(p) for p in recipe_files()},
              'at': datetime.now(timezone.utc).isoformat()}
    with path.open('x') as stream:
        stream.write(json.dumps(frozen, indent=2) + '\n')
    return frozen


def save_weights(model, path):
    import mlx.core as mx
    from mlx.utils import tree_flatten
    temporary = path.with_name(path.stem + '.tmp.safetensors')
    mx.save_safetensors(str(temporary), dict(tree_flatten(model.trainable_parameters())))
    temporary.replace(path)


def run_train(rows, valid, model_path, cfg, manifest, destination=ADAPTER, *, started=None):
    # No caller in CPU preparation may invoke this function; gpu_run owns it.
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    from mlx.utils import tree_flatten, tree_map
    from mlx_lm import load
    from mlx_lm.tuner.datasets import CacheDataset
    from mlx_lm.tuner.trainer import evaluate, grad_checkpoint, iterate_batches
    from mlx_lm.tuner.utils import linear_to_lora_layers
    from llm.stowlm.train import completion_loss

    started = time.monotonic() if started is None else started
    deadline = started + cfg['hard_stop_seconds']
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('nonempty adapter destination; never overwrite or silently resume')
    mx.random.seed(cfg['seed'])
    mx.set_cache_limit(8 * 2**30)
    model, tokenizer = load(str(model_path))  # Deliberately no adapter_path argument.
    model.freeze()
    linear_to_lora_layers(model, cfg['num_layers'], {k: cfg[k] for k in ('rank', 'scale', 'dropout')})
    trainable = dict(tree_flatten(model.trainable_parameters()))
    if not trainable or any(not key.endswith(('.lora_a', '.lora_b')) for key in trainable):
        raise ValueError('only fresh LoRA leaves may train')
    training = TokenDataset(selected_rows(rows, cfg), tokenizer, cfg['max_seq_length'])
    validation = CacheDataset(TokenDataset(valid, tokenizer, cfg['max_seq_length']))
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'adapter_config.json').write_text(json.dumps({
        'fine_tune_type': 'lora', 'num_layers': cfg['num_layers'],
        'lora_parameters': {k: cfg[k] for k in ('rank', 'scale', 'dropout')},
        'model': str(model_path), 'intake_recipe': cfg['recipe']}, indent=2) + '\n')
    (destination / 'run.json').write_text(json.dumps({**manifest, 'config': cfg}, indent=2) + '\n')
    save_weights(model, destination / 'adapters.safetensors')
    optimizer = optim.Adam(learning_rate=cfg['learning_rate'])
    if cfg['grad_checkpoint']:
        grad_checkpoint(model.layers[0])
    value_and_grad = nn.value_and_grad(model, completion_loss)
    state = [model.state, optimizer.state, mx.random.state]

    @partial(mx.compile, inputs=state, outputs=state)
    def step(batch, gradients, divisor, apply):
        (loss, tokens), gradient = value_and_grad(model, *batch)
        if gradients is not None:
            gradient = tree_map(lambda a, b: a + b, gradient, gradients)
        if apply:
            optimizer.update(model, tree_map(lambda g: g / divisor, gradient))
            gradient = None
        return loss, tokens, gradient

    completed = updates = 0
    validation_history = []
    progress = destination / 'progress.json'
    reason = 'selected training budget completed'

    def record_checkpoint():
        save_weights(model, destination / 'adapters.safetensors')
        progress.write_text(json.dumps({'checkpoint_rows_completed': completed,
                                       'optimizer_updates': updates,
                                       'adapter_sha256': sha256(destination / 'adapters.safetensors')}) + '\n')

    def check_deadline():
        if time.monotonic() >= deadline - 30:
            raise DeadlineReached('cooperative stop with 30 seconds reserved for final save')

    def validate():
        check_deadline()
        loss = evaluate(model, validation, batch_size=1, num_batches=cfg['val_batches'],
                        max_seq_length=cfg['max_seq_length'], loss=completion_loss,
                        progress_callback=check_deadline)
        validation_history.append({'iteration': completed, 'val_loss': loss})
        model.train()

    record_checkpoint()
    try:
        validate()
        # selected_rows already permuted once; no padded rows or repeated epoch.
        accumulation = cfg['grad_accumulation_steps']
        for start in range(0, len(training), accumulation):
            check_deadline()
            count = min(accumulation, len(training) - start)
            gradients = None
            group_started = time.monotonic()
            for position in range(count):
                try:
                    check_deadline()
                except DeadlineReached:
                    if gradients is not None:
                        # Flush partial accumulation with its actual divisor.
                        optimizer.update(model, tree_map(lambda g: g / position, gradients))
                        mx.eval(state)
                        updates += 1
                    raise
                batch = next(iterate_batches([training[start + position]], 1, cfg['max_seq_length']))
                loss, tokens, gradients = step(batch, gradients, count, position == count - 1)
                mx.eval(state, loss, tokens, gradients)
                completed += 1
            updates += 1
            if completed % cfg['steps_per_report'] == 0 or completed == len(training):
                with (destination / 'train.jsonl').open('a') as stream:
                    stream.write(json.dumps({'iteration': completed, 'optimizer_updates': updates,
                                             'loss': float(loss.item()),
                                             'rows_per_second': count / max(.001, time.monotonic() - group_started)}) + '\n')
            if completed % cfg['steps_per_save'] == 0 or completed == len(training):
                record_checkpoint()
            if completed % cfg['steps_per_eval'] == 0 or completed == len(training):
                validate()
    except DeadlineReached as stop:
        reason = str(stop)
    record_checkpoint()
    report = {'reason': reason, 'rows_completed': completed, 'rows_planned': len(training),
              'selected_budget_completed': completed == len(training),
              'full_corpus_epoch_completed': completed == len(rows),
              'optimizer_updates': updates, 'elapsed_seconds': time.monotonic() - started,
              'validation_checks': validation_history, 'source_adapter': None,
              'adapter_sha256': sha256(destination / 'adapters.safetensors')}
    (destination / 'STOPPED.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=DATA)
    parser.add_argument('--config', type=Path, default=HERE / 'config.json')
    parser.add_argument('--model', type=Path)
    parser.add_argument('--data-only', action='store_true')
    parser.add_argument('--tokenize-check', action='store_true')
    args = parser.parse_args()
    from .admission import wait_admit
    evidence = wait_admit()
    cfg, model, rows, valid, report = data_preflight(args.data_dir, args.config, args.model)
    if args.tokenize_check:
        report = token_preflight(cfg, model, rows, valid, report)
    print(json.dumps({**report, 'config': cfg, 'admission': evidence}, indent=2))


if __name__ == '__main__':
    main()
