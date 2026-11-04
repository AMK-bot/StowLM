"""Size an 8.1 SFT run from a real dataset with the 5.9 slim config (config.json); refuse anything over the cap.

Predicted GPU-h = sum(prompt + completion tokens of train examples) * epochs / planning tok/s * (1 + overhead).
Examples longer than max_seq_length are counted and must be dropped by the dataset builder (train.py refuses them;
truncating would cut the end-of-plan token).

Usage: uv run python experiments/mvp/sft/plan_run.py --data-dir <dir> --adapter-dir <out> [--epochs N]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

CONFIG = json.loads((Path(__file__).resolve().parent / "config.json").read_text())


def predict(lengths: list[int], epochs: int, cap_h: float | None = None) -> dict:
    b = dict(CONFIG["budget"], **({"gpu_hours_cap": cap_h} if cap_h else {}))
    tokens = sum(lengths) * epochs
    hours = tokens / b["planning_tok_s"] / 3600 * (1 + b["overhead_frac"])
    return {"examples": len(lengths), "epochs": epochs, "processed_tokens": tokens, "predicted_gpu_h": round(hours, 2),
            "over_cap": hours > b["gpu_hours_cap"],
            "too_long": sum(n > CONFIG["train_args"]["max_seq_length"] for n in lengths)}


def command(data_dir: str, adapter_dir: str, n_train: int, epochs: int) -> list[str]:
    a = dict(CONFIG["train_args"])
    ga = a["grad_accumulation_steps"]
    a["iters"] = max(ga, (n_train * epochs // a["batch_size"]) // ga * ga)  # a multiple of grad accumulation
    cmd = ["uv", "run", "python", "-m", "llm.stowlm.train", "--data-dir", data_dir, "--adapter-dir", adapter_dir]
    for k, v in a.items():
        cmd += [f"--{k.replace('_', '-')}", str(v).lower() if isinstance(v, bool) else str(v)]
    return cmd


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    p.add_argument("--adapter-dir", required=True)
    p.add_argument("--epochs", type=int, default=CONFIG["epochs"])
    p.add_argument("--cap-h", type=float, default=None, help="tighter GPU-h cap (pilots); default config's 8 h")
    args = p.parse_args()
    rows = [json.loads(l) for l in (Path(args.data_dir) / "train.jsonl").read_text().splitlines() if l.strip()]
    out = predict([len(r["input_ids"]) for r in rows], args.epochs, args.cap_h)
    print(json.dumps(out, indent=1))
    if out["too_long"]:
        raise SystemExit(f"{out['too_long']} examples exceed max_seq_length; drop them in the dataset builder")
    if out["over_cap"]:
        raise SystemExit("over the GPU-h cap: drop examples (longest rung first) or epochs")
    print(" ".join(command(args.data_dir, args.adapter_dir, len(rows), args.epochs)))


if __name__ == "__main__":
    main()
