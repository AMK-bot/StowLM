# Long-sequence SFT pilot inputs (8.1 sizing): stand-in plans for a few full-size instances, serialised in format v2.
# STUB-TAINTED and timing-only: plans come from solver.scalable over the C2-off engine (same stub as
# experiments/tiny-chain/gold.py); they are never training data. One instance per process (CPU).
# Usage: uv run python experiments/sft-pilot/make_long.py <instance.json> <tag> [time_limit_s]
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments/tiny-chain"))
import stowage.interfaces  # noqa: E402,F401
from gold import STUB_REASON, c2_off_engine  # noqa: E402
from llm.stowlm.format import load_tokenizer  # noqa: E402
from llm.stowlm.sft_data import build_example, verify_replay, write_dataset  # noqa: E402
from stowage.frozen import stub_override  # noqa: E402
from stowage.io import load_instance  # noqa: E402
from stowage.model.provenance import Provenance  # noqa: E402
from stowage.solver.scalable import ScalableSolver  # noqa: E402

path, tag = sys.argv[1], sys.argv[2]
limit = float(sys.argv[3]) if len(sys.argv) > 3 else 600.0
inst = load_instance(path)
tok = load_tokenizer()
row = {"tag": tag, "instance": inst.id, "path": path, "n_boxes": len(inst.load_list)}
t = time.perf_counter()
with stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON):
    res = ScalableSolver().plan(inst, seed=0, time_limit_s=limit)
    row.update(status=res.status, solve_s=round(time.perf_counter() - t, 1), message=(res.message or "")[:200])
    if res.status == "ok" and res.plan is not None:
        plan = res.plan.model_copy(update={"provenance": res.plan.provenance.merge(
            Provenance(stubs={"sft_pilot.timing_only": "stand-in plan for sequence-length timing; never train on it"}))})
        ex = build_example(inst, plan, split="train", tokenizer=tok, allow_stubs=True)
        rep = verify_replay(inst, ex, tokenizer=tok)
        row.update(tokens=len(ex.input_ids), prompt=ex.prompt_len, completion=ex.completion_len,
                   n_actions=ex.n_actions, replay_ok=rep.ok)
        out = ROOT / "experiments/sft-pilot/data" / tag
        val = type(ex)(**{**vars(ex), "split": "val"})
        write_dataset([ex, val], out)  # same example as train and val: the val pass just exercises eval at this length
print(json.dumps(row), flush=True)
with open(ROOT / "experiments/sft-pilot/long_examples.jsonl", "a") as f:
    f.write(json.dumps(row) + "\n")
