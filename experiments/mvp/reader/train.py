"""Glimmer-only reader LoRA using the same MLX QLoRA stack as mvp/sft.

Default invocation checks data on CPU and prints the fixed recipe. --execute is
for the GPU executive only. This trainer never opens the sealed directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from experiments.mvp.reader.evaluate import normalized, read_jsonl, render_prompt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def check_model(path: Path) -> None:
    config = json.loads((path / "config.json").read_text())
    if config.get("model_type") != "muse_glimmer":
        raise ValueError("reader is Glimmer base only; refusing other architectures")
    if (path / "adapter_config.json").exists():
        raise ValueError("model path must be the unadapted Glimmer base")


def validate_splits(train: list[dict], valid: list[dict]) -> dict:
    if not train or not valid:
        raise ValueError("nonempty train and family-heldout valid required")
    seen_ids, seen_sentences = set(), set()
    for row in train + valid:
        if row["id"] in seen_ids:
            raise ValueError("duplicate id across splits")
        seen_ids.add(row["id"])
        sentence_key = (json.dumps(row.get("context"), sort_keys=True), row["sentence"].strip().casefold())
        if sentence_key in seen_sentences:
            raise ValueError("duplicate sentence/context across splits")
        seen_sentences.add(sentence_key)
        if not row.get("family"):
            raise ValueError("every row requires a paraphrase family")
        if "sealed" in str(row.get("source", "")).lower():
            raise ValueError("sealed data cannot enter training or validation")
        messages = row["messages"]
        if [m["role"] for m in messages] != ["system", "user", "assistant"]:
            raise ValueError("expected system/user/assistant training messages")
        if normalized(messages[-1]["content"], row.get("context")) != normalized(row["expected"], row.get("context")):
            raise ValueError("assistant completion disagrees with expected label")
    families_train = {r["family"] for r in train}
    families_valid = {r["family"] for r in valid}
    if families_train & families_valid:
        raise ValueError("paraphrase families overlap train/valid")
    return {"train_count": len(train), "valid_count": len(valid),
            "train_families": sorted(families_train), "valid_families": sorted(families_valid)}


class TokenDataset:
    def __init__(self, rows: list[dict], tokenizer, max_length: int):
        self.items = []
        for row in rows:
            chat = row["messages"]
            tokens = tokenizer.apply_chat_template(chat, tokenize=True, add_generation_prompt=False,
                                                    return_dict=False)
            prefix = tokenizer.encode(render_prompt(tokenizer, chat[:-1]), add_special_tokens=False)
            if tokens[:len(prefix)] != prefix:
                raise ValueError(f"chat prompt is not a prefix of full row {row['id']}")
            if len(tokens) > max_length:
                raise ValueError(f"{row['id']} exceeds max_seq_length; truncation forbidden")
            if len(tokens) <= len(prefix):
                raise ValueError("empty completion")
            self.items.append((tokens, len(prefix)))

    def __len__(self):
        return len(self.items)

    def __getitem__(self, index):
        return self.items[index]

    def process(self, item):
        return item


def iterations_for(n_train: int, cfg: dict) -> int:
    microsteps = math.ceil(n_train * cfg["epochs"] / cfg["batch_size"])
    accumulation = cfg["grad_accumulation_steps"]
    return math.ceil(microsteps / accumulation) * accumulation


def run_train(train_rows, valid_rows, model_path: Path, adapter: Path, cfg: dict, manifest: dict):
    # All device/model imports stay below the explicit --execute gate.
    import numpy as np
    import mlx.core as mx
    import mlx.optimizers as optim
    from mlx_lm import load
    from mlx_lm.tuner.datasets import CacheDataset
    from mlx_lm.tuner.trainer import TrainingArgs, train as mlx_train
    from mlx_lm.tuner.utils import linear_to_lora_layers
    from llm.stowlm.train import completion_loss

    if adapter.exists() and any(adapter.iterdir()):
        raise ValueError("adapter directory must be empty; this fixed run never silently resumes")
    np.random.seed(cfg["seed"])
    mx.random.seed(cfg["seed"])
    model, tokenizer = load(str(model_path))
    training = TokenDataset(train_rows, tokenizer, cfg["max_seq_length"])
    validation = TokenDataset(valid_rows, tokenizer, cfg["max_seq_length"])
    model.freeze()
    lora = {k: cfg[k] for k in ("rank", "scale", "dropout")}
    linear_to_lora_layers(model, cfg["num_layers"], lora)
    adapter.mkdir(parents=True, exist_ok=True)
    iters = iterations_for(len(train_rows), cfg)
    run = dict(manifest, iterations=iters, config=cfg,
               token_lengths={"train_max": max(len(x[0]) for x in training.items),
                              "valid_max": max(len(x[0]) for x in validation.items),
                              "train_total": sum(len(x[0]) for x in training.items)})
    (adapter / "run.json").write_text(json.dumps(run, indent=2) + "\n")
    (adapter / "adapter_config.json").write_text(json.dumps({
        "fine_tune_type": "lora", "num_layers": cfg["num_layers"], "lora_parameters": lora,
        "model": str(model_path), "reader_recipe": cfg["recipe"]}, indent=2) + "\n")
    args = TrainingArgs(**{k: cfg[k] for k in (
        "batch_size", "grad_accumulation_steps", "max_seq_length", "grad_checkpoint",
        "steps_per_report", "steps_per_eval", "steps_per_save", "val_batches")},
        iters=iters, adapter_file=str(adapter / "adapters.safetensors"))
    mlx_train(model=model, optimizer=optim.Adam(learning_rate=cfg["learning_rate"]), args=args,
              train_dataset=CacheDataset(training), val_dataset=CacheDataset(validation),
              loss=completion_loss)
    (adapter / "DONE.json").write_text(json.dumps({"recipe": cfg["recipe"], "iterations": iters,
                                                     "selection": cfg["selection"]}, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=HERE / "data")
    parser.add_argument("--adapter-dir", type=Path, default=HERE / "adapter-v1")
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--model", type=Path)
    parser.add_argument("--tokenize-check", action="store_true",
                        help="CPU tokenizer-only length/prefix check; never loads model weights")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text())
    model_path = args.model or ROOT / cfg["model"]
    check_model(model_path)
    if "sealed" in str(args.data_dir).lower():
        parser.error("sealed data may not be used for training")
    paths = [args.data_dir / "train.jsonl", args.data_dir / "valid.jsonl"]
    train_rows, valid_rows = [read_jsonl(path) for path in paths]
    manifest = validate_splits(train_rows, valid_rows)
    manifest["data_sha256"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    manifest["config_sha256"] = hashlib.sha256(args.config.read_bytes()).hexdigest()
    manifest["model_config_sha256"] = hashlib.sha256((model_path / "config.json").read_bytes()).hexdigest()
    manifest["iterations"] = iterations_for(len(train_rows), cfg)
    manifest["optimizer_updates"] = manifest["iterations"] // cfg["grad_accumulation_steps"]
    if args.tokenize_check:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True)
        tokenized = [TokenDataset(rows, tokenizer, cfg["max_seq_length"]) for rows in (train_rows, valid_rows)]
        manifest["token_lengths"] = {name: {"count": len(data),
            "max": max(len(x[0]) for x in data.items),
            "total": sum(len(x[0]) for x in data.items)}
            for name, data in zip(("train", "valid"), tokenized)}
        manifest["processed_tokens_epochs"] = manifest["token_lengths"]["train"]["total"] * cfg["epochs"]
        manifest["processed_tokens_upper_bound"] = (manifest["iterations"] * cfg["batch_size"]
                                                    * manifest["token_lengths"]["train"]["max"])
    if not args.execute:
        print(json.dumps(dict(manifest, config=cfg, device_loaded=False), indent=2))
        return
    run_train(train_rows, valid_rows, model_path, args.adapter_dir, cfg, manifest)


if __name__ == "__main__":
    main()
