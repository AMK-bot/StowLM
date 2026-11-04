"""CPU check for patch 02: pooled lookahead+securing decoders give the SAME allowed sets as in-thread ones.

Replays complete samples from decode_sweep.jsonl token by token through both backends and compares every allowed
set, the final status and the final actions. No model, no GPU.

Run: uv run python experiments/mvp/speed/check_pool_equiv.py <dir holding the patched pool.py> [n_samples]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "out"


def main() -> None:
    pool_dir, n = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 2
    sys.path.insert(0, pool_dir)
    sys.path.insert(1, str(ROOT / "experiments/eval-c1"))  # decode_trace (worker import)
    from pool import MaskWorkerPool
    from llm.exit.selection import decoder_factory
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.sft_data import codec_for
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance

    tok, space = load_tokenizer(), get_action_space()
    cfg = FeasibilityConfig(extra={"global_screen": 0})
    rows = [json.loads(l) for l in (OUT / "decode_sweep.jsonl").read_text().splitlines()]
    picks = [(r, s) for r in rows if r["B"] > 1 for s in r["samples"] if s["status"] == "complete"][:n]
    ok = True
    with MaskWorkerPool(2, c2=True, layers="exit") as pool:
        for r, s in picks:
            inst = load_instance(ROOT / r["map"])
            codec = codec_for(inst, tok)
            toks = list(codec.encode_plan([space.decode(a) for a in s["actions"]]))
            local = decoder_factory()(inst, make_feasibility_engine(inst, space, config=cfg), codec, None, 1)[0]
            remote = pool.decoders(inst, 2, engine_config=cfg)[1]  # row 1 lives on worker 1
            t0 = time.perf_counter()
            mism = 0
            for t in toks:
                a, b = tuple(local.allowed_token_ids()), tuple(remote.allowed_token_ids())
                mism += a != b
                local.advance(t)
                remote.advance(t)
            same_actions = [space.encode(x) for x in local.actions] == [space.encode(x) for x in remote.actions]
            print(f"{r['instance_id']}: tokens={len(toks)} allowed-set mismatches={mism} status local={local.status} "
                  f"remote={remote.status} actions_equal={same_actions} ({time.perf_counter() - t0:.1f}s)", flush=True)
            ok &= mism == 0 and local.status == remote.status == "complete" and same_actions
    print("EQUIVALENT" if ok else "MISMATCH")


if __name__ == "__main__":
    main()
