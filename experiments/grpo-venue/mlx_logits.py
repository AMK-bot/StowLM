# Dump Glimmer logits in MLX for the adapter-conversion check, with and without a LoRA adapter.
# The prompts are built once here as token IDs (prompts.json) and reused verbatim by pt_logits.py, so both stacks
# see identical inputs (never re-tokenise; D9). Only the last KEEP positions are saved (float32).
# Usage: uv run python experiments/grpo-venue/mlx_logits.py MODEL ADAPTER_DIR|none OUT_DIR TAG
import json
import sys
from pathlib import Path

import mlx.core as mx
import numpy as np
from mlx_lm import load

KEEP = 64
model_path, adapter, out, tag = sys.argv[1], sys.argv[2], Path(sys.argv[3]), sys.argv[4]
out.mkdir(parents=True, exist_ok=True)
model, tok = load(model_path, adapter_path=None if adapter == "none" else adapter)

pfile = out / "prompts.json"
if not pfile.exists():
    plan_lines = Path("data/glimmer-bench/valid.jsonl").read_text().splitlines()
    english = ("A feeder vessel arriving at Laem Chabang must load export boxes for Singapore and Port Klang. "
               "The planner keeps heavy containers low, never stacks a heavier box on a lighter one, and keeps "
               "dangerous goods apart as the IMDG Code requires. Every extra crane move costs time and money, so "
               "the plan should avoid restows at later ports. ") * 4
    prompts = {
        "plan_in_dist": tok.encode(json.loads(plan_lines[0])["text"])[:256],    # the adapter was trained on this format
        "plan_in_dist_2": tok.encode(json.loads(plan_lines[1])["text"])[:256],
        "english": tok.encode(english)[:256],
        "chat": tok.apply_chat_template([{"role": "user", "content": "What is a restow in container shipping?"}],
                                        add_generation_prompt=True),
        "plan_long_3000": tok.encode(" ".join(json.loads(l)["text"] for l in plan_lines))[:3000],  # > sliding window
    }
    pfile.write_text(json.dumps(prompts))
prompts = json.loads(pfile.read_text())
for name, ids in prompts.items():
    logits = model(mx.array(ids)[None])[0, -KEEP:].astype(mx.float32)
    np.save(out / f"{tag}__{name}.npy", np.array(logits))
    print(tag, name, len(ids), "tokens, peak", round(mx.get_peak_memory() / 1e9, 1), "GB", flush=True)
