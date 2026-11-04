"""Pre-fix 8.1 train() (verbatim from the reviewed source), for before/after regression evidence only."""
import json, re, time
from dataclasses import asdict
from pathlib import Path
from llm.stowlm.sft_data import TAINT_OK_DIRS, read_dataset
from llm.stowlm.train import (TokenDataset, blocked_attention, completion_loss, learning_rate_schedule,
                              load_optimizer_state, lora_parameters, save_optimizer_state)


def _latest_checkpoint(adapter_dir):
    found = [(int(m[1]), p) for p in adapter_dir.glob("*_adapters.safetensors")
             if (m := re.fullmatch(r"(\d+)_adapters\.safetensors", p.name))]
    if not found:
        return None
    it, path = max(found)
    return path, it


def train(cfg):
    data, out = Path(cfg.data_dir), Path(cfg.adapter_dir)
    out.mkdir(parents=True, exist_ok=True)
    train_ex = read_dataset(data / f"{cfg.train_split}.jsonl")
    val_ex = read_dataset(data / f"{cfg.val_split}.jsonl")
    stubs = sorted({k for e in [*train_ex, *val_ex] for k in e.stubs})
    if stubs and not TAINT_OK_DIRS & set(out.resolve().parts):
        raise RuntimeError(f"dataset is stub-tainted ({stubs}); adapters from it may only live under tiny-chain/ or sft-pilot/")
    train_set, val_set = TokenDataset(train_ex, cfg.max_seq_length), TokenDataset(val_ex, cfg.max_seq_length)
    import mlx.core as mx
    import numpy as np
    import mlx.optimizers as optim
    from mlx_lm.utils import load_model as _lm
    from mlx_lm.tuner.callbacks import TrainingCallback
    from mlx_lm.tuner.datasets import CacheDataset
    from mlx_lm.tuner.trainer import TrainingArgs
    from mlx_lm.tuner.trainer import train as mlx_train
    from mlx_lm.tuner.utils import linear_to_lora_layers
    from mlx.utils import tree_flatten
    mx.random.seed(cfg.seed)
    np.random.seed(cfg.seed)
    model, _ = _lm(Path(cfg.model))  # only change: load() needs a tokenizer the tiny test model lacks
    if cfg.attn_block:
        from mlx_lm.models import muse_glimmer
        from mlx_lm.models.base import scaled_dot_product_attention as _sdpa
        muse_glimmer.scaled_dot_product_attention = blocked_attention(cfg.attn_block, model.args.sliding_window, _sdpa)
    model.freeze()
    linear_to_lora_layers(model, cfg.num_layers, lora_parameters(cfg))
    if cfg.steps_per_save % cfg.steps_per_report:
        raise ValueError("steps_per_save must be a multiple of steps_per_report")
    optimizer = optim.Adam(learning_rate=learning_rate_schedule(cfg))
    resumed_from, start_it, optimizer_restored = None, 0, False
    if cfg.resume and (ck := _latest_checkpoint(out)) is not None:
        model.load_weights(str(ck[0]), strict=False)
        resumed_from, start_it = str(ck[0]), ck[1]
        opt_file = out / f"{start_it:07d}_optimizer.safetensors"
        if opt_file.exists():
            load_optimizer_state(optimizer, model, opt_file)
            optimizer_restored = True
    metrics_file = out / "metrics.jsonl"

    class Recorder(TrainingCallback):
        def __init__(self):
            self.train, self.val = [], []

        def _write(self, kind, info):
            info = {"kind": kind, **{k: (float(v) if hasattr(v, "item") else v) for k, v in info.items()}}
            info["iteration"] = int(info.get("iteration", 0)) + start_it
            with open(metrics_file, "a") as f:
                f.write(json.dumps(info) + "\n")
            return info

        def on_train_loss_report(self, info):
            self.train.append(self._write("train", info))
            it = self.train[-1]["iteration"]
            if it % cfg.steps_per_save == 0 or it == cfg.iters:
                save_optimizer_state(optimizer, out / f"{it:07d}_optimizer.safetensors")

        def on_val_loss_report(self, info):
            self.val.append(self._write("val", info))

    rec = Recorder()
    remaining = max(0, cfg.iters - start_it)
    save_dir = out / ".resume" if start_it else out
    save_dir.mkdir(exist_ok=True)
    if remaining:
        args = TrainingArgs(batch_size=cfg.batch_size, iters=remaining, val_batches=cfg.val_batches,
                            steps_per_report=cfg.steps_per_report, steps_per_eval=cfg.steps_per_eval,
                            steps_per_save=cfg.steps_per_save, adapter_file=str(save_dir / "adapters.safetensors"),
                            max_seq_length=cfg.max_seq_length, grad_checkpoint=cfg.grad_checkpoint,
                            grad_accumulation_steps=cfg.grad_accumulation_steps)
        mlx_train(model=model, optimizer=optimizer, args=args, train_dataset=CacheDataset(train_set),
                  val_dataset=CacheDataset(val_set), loss=completion_loss, training_callback=rec)
        if start_it:
            for p in save_dir.glob("*_adapters.safetensors"):
                p.rename(out / f"{int(p.name.split('_')[0]) + start_it:07d}_adapters.safetensors")
            (save_dir / "adapters.safetensors").rename(out / "adapters.safetensors")
            save_dir.rmdir()
    record = {"config": asdict(cfg), "stubs": stubs, "resumed_from": resumed_from}
    (out / "run.json").write_text(json.dumps(record, indent=1))
    return record
