# Independent reference for the conversion tolerance (task 0.1): Meta's Muse Glimmer checkpoint in PyTorch bf16 via
# transformers (no MLX code, no mlx-lm sanitizer), last KEEP positions' logits per fixed-ID prompt.
# Run in the isolated env (same one as experiments/grpo-venue/pt_logits.py), never alongside an MLX job:
#   HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 experiments/grpo-venue/.venv-pt/bin/python pt_reference.py OUT_DIR [mps|cpu]
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import transformers
from transformers import AutoModelForImageTextToText

sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity import write_identity  # noqa: E402

REVISION = "cec94fcfd6512f2cfe708138c32533b346ea4f69"

KEEP = 64
out = Path(sys.argv[1])
device = sys.argv[2] if len(sys.argv) > 2 else "mps"
t0 = time.time()
model = AutoModelForImageTextToText.from_pretrained("meta-models/Muse-Glimmer-30B",
                                                    revision=REVISION,
                                                    dtype=torch.bfloat16, device_map=device, attn_implementation="sdpa")
model.eval()
print(type(model).__name__, f"loaded in {time.time() - t0:.0f} s", flush=True)
with torch.no_grad():
    for name, ids in json.loads((out / "prompts.json").read_text()).items():
        t = time.time()
        logits = model(input_ids=torch.tensor([ids], device=device), logits_to_keep=KEEP).logits[0]
        path = out / f"pt_bf16__{name}.npy"
        np.save(path, logits.float().cpu().numpy())
        write_identity(out, "pt_bf16", name, ids, path,
                       model={"source": "meta-models/Muse-Glimmer-30B", "revision": REVISION, "dtype": "bfloat16",
                              "implementation": "transformers AutoModelForImageTextToText"},
                       settings={"keep_positions": KEEP, "device": device, "attn_implementation": "sdpa"},
                       software={"torch": torch.__version__, "transformers": transformers.__version__})
        print(name, len(ids), f"tokens, {time.time() - t:.1f} s", flush=True)
