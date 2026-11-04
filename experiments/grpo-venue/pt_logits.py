# PyTorch bf16 side of the adapter-conversion check: load Meta's Muse Glimmer checkpoint with transformers, attach
# the PEFT adapter produced by convert_adapter.py, and dump logits with the adapter on and off (disable_adapter()).
# ~60 GB in bf16 (text + vision tower). Run in the isolated env, never alongside an MLX job:
#   experiments/grpo-venue/.venv-pt/bin/python experiments/grpo-venue/pt_logits.py PEFT_DIR OUT_DIR [mps|cpu]
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from peft import PeftModel
from transformers import AutoModelForImageTextToText

KEEP = 64
peft_dir, out = sys.argv[1], Path(sys.argv[2])
device = sys.argv[3] if len(sys.argv) > 3 else "mps"
t0 = time.time()
model = AutoModelForImageTextToText.from_pretrained("meta-models/Muse-Glimmer-30B", dtype=torch.bfloat16,
                                                    device_map=device, attn_implementation="sdpa")
model.eval()
print(type(model).__name__, f"loaded in {time.time() - t0:.0f} s", flush=True)
model = PeftModel.from_pretrained(model, peft_dir)
wrapped = [n for n, m in model.named_modules() if hasattr(m, "lora_A") and "vision" in n]
assert not wrapped, f"adapter leaked into vision tower: {wrapped[:3]}"
n_lora = sum(1 for n, m in model.named_modules() if hasattr(m, "lora_A") and len(getattr(m, "lora_A")))
print("LoRA-wrapped modules:", n_lora, flush=True)
prompts = json.loads((out / "prompts.json").read_text())
with torch.no_grad():
    for name, ids in prompts.items():
        x = torch.tensor([ids], device=device)
        t = time.time()
        on = model(input_ids=x, logits_to_keep=KEEP).logits[0].float().cpu().numpy()
        with model.disable_adapter():
            off = model(input_ids=x, logits_to_keep=KEEP).logits[0].float().cpu().numpy()
        np.save(out / f"pt_bf16_adapter__{name}.npy", on)
        np.save(out / f"pt_bf16_base__{name}.npy", off)
        print(name, len(ids), f"tokens, {time.time() - t:.0f} s", flush=True)
