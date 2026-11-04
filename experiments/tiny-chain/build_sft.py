# Historical tiny-chain diagnostic: serialise unchecked stand-ins into SFT examples.
# C2-off replay never establishes task 7.1 production gold acceptance.
# The engine behind the mask runs with C2 off (same stub as gold.py: the real engine dead-ends at depth 0 on these
# instances), so the dataset is stub-tainted and lives only under experiments/tiny-chain/.
# Usage: uv run python experiments/tiny-chain/build_sft.py [--gold experiments/tiny-chain/gold/standin] [--out DIR]
# Clean gold: use experiments/sft-data/build.py, never this stand-in adapter.
import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import stowage.interfaces  # noqa: E402,F401
from gold import STUB_REASON, c2_off_engine  # noqa: E402
from llm.stowlm.format import load_tokenizer  # noqa: E402
from llm.stowlm.sft_data import build_example, verify_replay, write_dataset  # noqa: E402
from stowage.frozen import stub_override  # noqa: E402
from stowage.io import load_instance, load_plan  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--gold", default="experiments/tiny-chain/gold/standin")
ap.add_argument("--out", default="experiments/tiny-chain/sft")
ap.add_argument("--no-stub", action="store_true",
                help="removed: use experiments/sft-data/build.py for published clean gold")
args = ap.parse_args()
if args.no_stub:
    ap.error("this builder is diagnostic only; published clean gold requires experiments/sft-data/build.py")

rows = [json.loads(l) for l in (ROOT / args.gold / "index.jsonl").read_text().splitlines()]
tok = load_tokenizer()
examples, failures = [], []
t0 = time.perf_counter()
with stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON):
    for r in rows:
        if not r.get("plan_file"):
            continue
        inst, plan = load_instance(ROOT / r["path"]), load_plan(ROOT / r["plan_file"])
        ex = build_example(inst, plan, split=r["split"], tokenizer=tok, allow_stubs=True)
        ex.meta.update(gap_to_lb=r.get("gap_to_lb"), weighted_nonproductive=r.get("weighted_nonproductive"),
                       lifts_now=r.get("lifts_now"), checker_passed=r.get("checker_passed"), path=r["path"])
        ex.meta.update(dataset_kind="diagnostic", production_verified=False)
        rep = verify_replay(inst, ex, tokenizer=tok, make_engine=c2_off_engine)
        (examples if rep.ok else failures).append(ex if rep.ok else {"instance": ex.instance_id, **asdict(rep)})
stats = write_dataset(examples, ROOT / args.out)
stats["dataset_kind"] = "diagnostic"
stats["gold"] = False
stats["diagnostic"] = True
stats["shippable"] = False
stats["replay"] = {"verified": len(examples), "failed": len(failures), "failures": failures,
                   "input_records": len(rows), "candidate_plans": len(examples) + len(failures),
                   "production_verified": 0,
                   "verify_s": round(time.perf_counter() - t0, 1),
                   "mask": "llm.stowlm.mask.MaskedDecoder over injected C2-off engine (diagnostic only)"}
(ROOT / args.out / "stats.json").write_text(json.dumps(stats, indent=1))
print(json.dumps(stats, indent=1))
