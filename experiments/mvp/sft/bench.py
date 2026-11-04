"""0.5 SFT throughput bench: QLoRA (llm.stowlm.train, D31 config) on 4-bit Glimmer at real tiny/small/medium lengths.

Inputs are the length probe's serialised examples (probe/<rung>-<ship>.example.json, switch OFF, format stowlm@1.0,
prompt 2.2). They are timing-only (unchecked constructor plans), so every dataset carries a stub and trains only with
``--diagnostic true`` under a ``sft-pilot/`` folder (train.py's taint fence); nothing here is a quality result.

Each config trains ``steps`` micro-steps on copies of one example per ship of that rung (cycled), batch B, with
``--steps-per-report 1``; step time = 1 / iterations_per_second, processed tok/s = real (unpadded) tokens per step /
step time. The first step (compile/warm-up) is reported apart.

Usage: uv run python experiments/mvp/sft/bench.py <rung> <batch> [steps] [layers] [rank]
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROBE = HERE / "probe"
WORK = HERE / "sft-pilot"  # the folder name train.py admits diagnostic (stub-tainted) adapters under
STUB = {"mvp.sft.timing_only": "unchecked constructor plan serialised for 0.5 throughput timing; never train on it"}


def make_dataset(rung: str, n_train: int, n_val: int, out: Path) -> list[int]:
    from llm.stowlm.sft_data import DATASET_VERSION, SFTExample, write_dataset
    from llm.stowlm.format import FORMAT_VERSION
    from llm.stowlm.format_prompt import PROMPT_VERSION

    seqs = []
    for path in sorted(PROBE.glob(f"{rung}-*.example.json")):
        d = json.loads(path.read_text())
        seqs.append((path.stem.split(".")[0], d["input_ids"], d["prompt_len"]))
    if not seqs:
        raise SystemExit(f"no probe examples for {rung}")
    meta = {"format": FORMAT_VERSION, "prompt": PROMPT_VERSION, "dataset": DATASET_VERSION,
            "gold": {"published": False, "data_mode": "timing_only"}}

    def ex(i, split):
        name, ids, plen = seqs[i % len(seqs)]
        return SFTExample(instance_id=f"{name}.copy{i}", split=split, input_ids=list(ids), prompt_len=plen,
                          n_actions=0, n_boxes=0, plan_id=f"{name}.timing", stubs=dict(STUB), meta=dict(meta))
    rows = [ex(i, "train") for i in range(n_train)] + [ex(i, "val") for i in range(n_val)]
    write_dataset(rows, out)
    return [len(s[1]) for s in seqs]


def main() -> None:
    rung, batch = sys.argv[1], int(sys.argv[2])
    steps = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    layers = int(sys.argv[4]) if len(sys.argv) > 4 else 16
    rank = int(sys.argv[5]) if len(sys.argv) > 5 else 8
    tag = f"{rung}-b{batch}-l{layers}-r{rank}"
    data, adapter = WORK / "data" / tag, WORK / "adapters" / tag
    lengths = make_dataset(rung, steps * batch, batch, data)
    if adapter.exists():
        subprocess.run(["rm", "-rf", str(adapter)], check=True)  # our own bench folder; a bench never resumes
    cmd = ["uv", "run", "python", "-m", "llm.stowlm.train", "--data-dir", str(data), "--adapter-dir", str(adapter),
           "--rank", str(rank), "--scale", "20", "--num-layers", str(layers), "--attn-block", "1024",
           "--max-seq-length", "18432", "--batch-size", str(batch), "--grad-accumulation-steps", "1",
           "--grad-checkpoint", "true", "--learning-rate", "5e-5", "--iters", str(steps), "--steps-per-report", "1",
           "--steps-per-eval", "100000", "--steps-per-save", str(steps), "--val-batches", "1", "--diagnostic", "true"]
    t = time.perf_counter()
    log = WORK / f"{tag}.log"
    with open(log, "w") as fh:
        rc = subprocess.run(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT).returncode
    wall = time.perf_counter() - t
    metrics = adapter / "metrics.jsonl"
    rows = [json.loads(l) for l in metrics.read_text().splitlines()] if metrics.exists() else []
    train = [r for r in rows if r["kind"] == "train"]
    step_s = [1.0 / r["iterations_per_second"] for r in train]
    mean_len = statistics.mean(lengths)
    steady = step_s[1:] or step_s
    # train.py's own processed tokens (every prompt + completion token fed) over the step time, steady steps only
    tok_s = [r["processed_tokens_per_second"] for r in train[1:] or train if r.get("processed_tokens_per_second")]
    out = {"tag": tag, "rung": rung, "batch": batch, "layers": layers, "rank": rank, "steps": len(train), "rc": rc,
           "example_tokens": lengths, "mean_tokens": round(mean_len, 1),
           "first_step_s": round(step_s[0], 2) if step_s else None,
           "step_s_median": round(statistics.median(steady), 2) if steady else None,
           "processed_tok_s": round(statistics.median(tok_s), 1) if tok_s else None,
           "examples_per_h": round(3600 * batch / statistics.median(steady), 1) if steady else None,
           "peak_gb": round(max(r["peak_memory"] for r in train), 2) if train else None,
           "wall_s": round(wall, 1), "log": str(log.relative_to(ROOT))}
    print(json.dumps(out), flush=True)
    with open(HERE / "bench.jsonl", "a") as fh:
        fh.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    main()
