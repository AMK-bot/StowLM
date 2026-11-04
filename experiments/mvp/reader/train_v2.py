"""D219 warm-start recipe. Default: CPU preflight; --execute: GPU owner only.

Unlike v1's rounded iteration count, this loop never repeats rows to fill an
accumulation group and applies the final short group with its actual divisor.
No sealed path is ever read here. Adam state deliberately starts fresh because
the new data and its permutation differ from v1; source weights are immutable.
"""
from __future__ import annotations

import argparse
from functools import partial
import hashlib
import json
import math
from pathlib import Path
import random
import time

from experiments.mvp.reader.evaluate import read_jsonl
from experiments.mvp.reader.train import HERE, ROOT, TokenDataset, check_model, validate_splits


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class DeadlineReached(Exception):
    pass


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise DeadlineReached('hard stop: three elapsed hours including validation')


def epoch_groups(count: int, accumulation: int, seed: int):
    if count < 1 or accumulation < 1:
        raise ValueError('positive rows and accumulation required')
    indices = list(range(count))
    random.Random(seed).shuffle(indices)
    return [indices[i:i + accumulation] for i in range(0, count, accumulation)]


def validate_recipe(cfg: dict, source: Path, destination: Path) -> dict:
    if cfg['epochs'] != 1 or cfg['batch_size'] != 1:
        raise ValueError('v2 requires exactly one epoch and microbatch size one')
    if cfg['grad_accumulation_steps'] < 1 or cfg['hard_stop_seconds'] > 10800:
        raise ValueError('invalid accumulation or hard stop exceeds three hours')
    if cfg['hard_stop_seconds'] <= 0:
        raise ValueError('hard stop must be positive')
    if source.resolve() == destination.resolve():
        raise ValueError('source adapter must be immutable and distinct from destination')
    original = json.loads((source / 'adapter_config.json').read_text())
    if original.get('fine_tune_type') != 'lora' or original['num_layers'] != cfg['num_layers']:
        raise ValueError('source LoRA architecture mismatch')
    if original['lora_parameters'] != {k: cfg[k] for k in ('rank', 'scale', 'dropout')}:
        raise ValueError('source LoRA parameters mismatch')
    return {'source_adapter': str(source.resolve()),
            'source_adapter_sha256': sha256(source / 'adapters.safetensors'),
            'source_adapter_config_sha256': sha256(source / 'adapter_config.json'),
            'optimizer_policy': 'fresh Adam; weight warm-start, no optimizer/scheduler resume'}


def preflight(data_dir: Path, source: Path, destination: Path, config: Path, model: Path | None = None):
    cfg = json.loads(config.read_text())
    model = model or ROOT / cfg['model']
    if 'sealed' in str(data_dir).lower():
        raise ValueError('sealed data is forbidden for training')
    check_model(model)
    train, valid = [read_jsonl(data_dir / f'{split}.jsonl') for split in ('train', 'valid')]
    manifest = validate_splits(train, valid)
    manifest.update(validate_recipe(cfg, source, destination))
    manifest.update(config_sha256=sha256(config), model_config_sha256=sha256(model / 'config.json'),
                    data_sha256={f'{s}.jsonl': sha256(data_dir / f'{s}.jsonl') for s in ('train', 'valid')},
                    iterations=len(train), optimizer_updates=math.ceil(len(train) / cfg['grad_accumulation_steps']),
                    final_accumulation_rows=(len(train) - 1) % cfg['grad_accumulation_steps'] + 1)
    return cfg, model, train, valid, manifest


def save_weights(model, path):
    import mlx.core as mx
    from mlx.utils import tree_flatten
    temporary = path.with_name(path.stem + '.tmp.safetensors')
    mx.save_safetensors(str(temporary), dict(tree_flatten(model.trainable_parameters())))
    temporary.replace(path)


def run_train(train_rows, valid_rows, model_path, source, destination, cfg, manifest,
              callback=None, started=None):
    # This is the only device/model gate. CPU preflight never imports MLX.
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    from mlx.utils import tree_flatten, tree_map
    from mlx_lm import load
    from mlx_lm.tuner.datasets import CacheDataset
    from mlx_lm.tuner.trainer import evaluate, grad_checkpoint, iterate_batches
    from llm.stowlm.train import completion_loss

    started = time.monotonic() if started is None else started
    deadline = started + cfg['hard_stop_seconds']
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('destination must be empty; refusing to overwrite or silently resume')
    mx.random.seed(cfg['seed'])
    mx.set_cache_limit(8 * 2**30)
    model, tokenizer = load(str(model_path), adapter_path=str(source))
    # load_adapters does not freeze the base model: freeze everything then only
    # unfreeze actual adapter leaves, so full base weights cannot be trained.
    model.freeze()
    for _, module in model.named_modules():
        keys = [key for key in ('lora_a', 'lora_b') if key in module]
        if keys:
            module.unfreeze(keys=keys)
    expected_keys = set(mx.load(str(source / 'adapters.safetensors')))
    if set(dict(tree_flatten(model.trainable_parameters()))) != expected_keys:
        raise ValueError('trainable leaves differ from source adapter; refusing partial warm-start')
    training = TokenDataset(train_rows, tokenizer, cfg['max_seq_length'])
    validation = CacheDataset(TokenDataset(valid_rows, tokenizer, cfg['max_seq_length']))
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'adapter_config.json').write_text(json.dumps({
        'fine_tune_type': 'lora', 'num_layers': cfg['num_layers'],
        'lora_parameters': {k: cfg[k] for k in ('rank', 'scale', 'dropout')},
        'model': str(model_path), 'reader_recipe': cfg['recipe']}, indent=2) + '\n')
    run = dict(manifest, config=cfg, token_lengths={
        'train_max': max(len(item[0]) for item in training.items),
        'train_total': sum(len(item[0]) for item in training.items)})
    (destination / 'run.json').write_text(json.dumps(run, indent=2) + '\n')
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
    report_started = time.monotonic()
    report_loss = report_rows = 0
    reason = 'one full epoch finished'
    history = []

    def check():
        nonlocal reason
        if time.monotonic() >= deadline:
            reason = 'hard stop: three elapsed hours including validation'
            return False
        try:
            val_loss = evaluate(model, validation, batch_size=1, num_batches=cfg['val_batches'],
                                max_seq_length=cfg['max_seq_length'], loss=completion_loss,
                                progress_callback=lambda: check_deadline(deadline))
        except DeadlineReached as stop:
            reason = str(stop)
            return False
        model.train()
        if callback:
            stop = callback(model=model, iteration=completed, val_loss=val_loss, deadline=deadline)
            history.append({'iteration': completed, 'val_loss': val_loss})
            if stop:
                reason = stop
                return False
        return True

    model.train()
    if check():
        for group in epoch_groups(len(training), cfg['grad_accumulation_steps'], cfg['seed']):
            if time.monotonic() >= deadline:
                reason = 'hard stop: three elapsed hours including validation'
                break
            gradients = None
            processed = 0
            expired = False
            previous_completed = completed
            for position, index in enumerate(group):
                if time.monotonic() >= deadline:
                    # Flush already computed rows once, with their actual count.
                    # Device work is cooperative: deadline observed between MLX
                    # operations; never start another forward pass after expiry.
                    if gradients is not None:
                        optimizer.update(model, tree_map(lambda g: g / processed, gradients))
                        mx.eval(state)
                    expired = True
                    reason = 'hard stop: three elapsed hours including validation'
                    break
                # One-row batches retain completion offsets and add no repeated
                # examples, even if row count is not divisible by four.
                batch = next(iterate_batches([training[index]], 1, cfg['max_seq_length']))
                loss, tokens, gradients = step(batch, gradients, len(group), position == len(group) - 1)
                mx.eval(state, loss, tokens, gradients)
                report_loss += float(loss.item())
                report_rows += 1
                completed += 1
                processed += 1
            updates += bool(processed)
            if (completed // cfg['steps_per_report'] != previous_completed // cfg['steps_per_report']
                    or completed == len(training) or expired):
                if callback and hasattr(callback, 'on_progress'):
                    callback.on_progress(iteration=completed, optimizer_updates=updates,
                                         loss=report_loss / max(1, report_rows),
                                         rows_per_second=report_rows / max(.001, time.monotonic() - report_started))
                report_started = time.monotonic()
                report_loss = report_rows = 0
            if expired:
                break
            if completed % cfg['steps_per_save'] == 0 or completed == len(training):
                save_weights(model, destination / 'adapters.safetensors')
            if completed % cfg['steps_per_eval'] == 0 or completed == len(training):
                if not check():
                    break
    save_weights(model, destination / 'adapters.safetensors')
    report = {'reason': reason, 'rows_completed': completed, 'rows_planned': len(training),
              'full_epoch_completed': completed == len(training), 'optimizer_updates': updates,
              'elapsed_seconds': time.monotonic() - started, 'validation_checks': history,
              'adapter_sha256': sha256(destination / 'adapters.safetensors')}
    (destination / 'STOPPED.json').write_text(json.dumps(report, indent=2) + '\n')
    if report['full_epoch_completed']:
        (destination / 'DONE.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=HERE / 'data-v2')
    parser.add_argument('--source-adapter', type=Path, default=HERE / 'adapter-v1')
    parser.add_argument('--adapter-dir', type=Path, default=HERE / 'adapter-v2')
    parser.add_argument('--config', type=Path, default=HERE / 'config-v2.json')
    parser.add_argument('--model', type=Path)
    parser.add_argument('--tokenize-check', action='store_true')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    cfg, model, train, valid, manifest = preflight(args.data_dir, args.source_adapter, args.adapter_dir, args.config, args.model)
    if args.tokenize_check:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(str(model), local_files_only=True)
        manifest['token_lengths'] = {name: {'count': len(rows), 'max': max(len(x[0]) for x in TokenDataset(rows, tokenizer, cfg['max_seq_length']).items)}
                                     for name, rows in [('train', train), ('valid', valid)]}
    if not args.execute:
        print(json.dumps(dict(manifest, config=cfg, device_loaded=False), indent=2))
    else:
        raise SystemExit('Use gpu_run_v2 --train-only: execution requires held-out checks and the hard-stop runner')


if __name__ == '__main__':
    main()
