# Long-sequence SFT pilot inputs, FALLBACK (27 Oct): every full-size v2 instance is a depth-0 dead end in the engine
# (C1a rejects all root candidates even with C2 off; make_long-*.log), so no engine-legal stand-in plan exists.
# For sequence-length TIMING ONLY, build format-v2 examples from the real prompt (encode_prompt of the real instance)
# and a format-valid but NOT engine-legal completion: load-list box i -> the i-th vessel cell, encoded through
# ActionCodec (same 7 tokens per Place action as a real plan) + end-of-plan. Never training data; tainted.
# Usage: uv run python experiments/sft-pilot/make_long_format_only.py <instance.json> <tag>
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from llm.stowlm.format import load_tokenizer  # noqa: E402
from llm.stowlm.format_prompt import encode_prompt  # noqa: E402
from llm.stowlm.sft_data import SFTExample, codec_for, write_dataset  # noqa: E402
from stowage.actions.space import Place  # noqa: E402
from stowage.io import load_instance  # noqa: E402

path, tag = sys.argv[1], sys.argv[2]
inst, tok = load_instance(path), load_tokenizer()
cells = [c.id for c in inst.vessel.cells]
actions = [Place(box, cells[i % len(cells)]) for i, box in enumerate(inst.load_list)]
prompt = encode_prompt(inst, tok)
completion = codec_for(inst, tok).encode_plan(actions)
stubs = {"sft_pilot.format_only": "completion is format-valid but not engine-legal; sequence-length timing only"}
ex = SFTExample(instance_id=inst.id, split="train", input_ids=[*prompt, *completion], prompt_len=len(prompt),
                n_actions=len(actions), n_boxes=len(inst.load_list), plan_id=f"{inst.id}.format-only", stubs=stubs)
write_dataset([ex, SFTExample(**{**vars(ex), "split": "val"})], ROOT / "experiments/sft-pilot/data" / tag)
row = {"tag": tag, "instance": inst.id, "path": path, "n_boxes": len(inst.load_list), "tokens": len(ex.input_ids),
       "prompt": ex.prompt_len, "completion": ex.completion_len, "plan": "format-only (not engine-legal)"}
print(json.dumps(row))
with open(ROOT / "experiments/sft-pilot/long_examples.jsonl", "a") as f:
    f.write(json.dumps(row) + "\n")
