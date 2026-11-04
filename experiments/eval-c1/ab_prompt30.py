"""DIAGNOSTIC A/B, base Glimmer 4-bit + mask: stowlm-prompt@2.2 (rule freeze off) vs stowlm-prompt@3.0 (rule freeze
v1 on), on the same tiny/small instances. Never evidence: C2 off (stub, tainted), base model, stale-epoch inputs.

What it measures (generation only; no ballast/checker selection, which doesn't depend on the prompt):
- mask override rate over all samples (model's unmasked top-1 token outside the allowed set), at action starts and
  over all tokens, plus the engine's per-screen rejection mix at overridden vs all decision states;
- complete / dead-end / max-token samples; prompt and decode tokens; prefill, decode and total wall time.

Both arms run in one process (one model load); the switch is ``stowage.rules.catalogue.rule_freeze_v1``, which also
switches the D61/D62/D64 rules in the engine, so the arms differ in rules as well as prompt (the 3.0 path is the
switch-on path). Decoders run in-thread: a context override would not reach spawned pool workers.

    uv run python experiments/eval-c1/ab_prompt30.py --n-tiny 12 --n-small 12 --n-samples 4 --out experiments/eval-c1/runs/ab-prompt30
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parents[1]
INPUTS = REPO / "experiments/solver-diag-1028/pilot-d58/generation/instances"


def live_instances(n_tiny: int, n_small: int) -> list[Path]:
    """The first tiny/small pilot-d58 inputs (sorted) whose C2-off engine has legal actions under both switches."""
    from stowage.actions import get_action_space
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1

    from pool import c2_off_engine

    picked = []
    for rung, n in (("tiny", n_tiny), ("small", n_small)):
        files = sorted(Path(p) for p in glob.glob(str(INPUTS / f"*/{rung}/seed-*.json")) if "audit" not in p)
        chosen = []
        for f in files:
            ok = True
            for on in (False, True):
                with rule_freeze_v1(enabled=on):
                    ok &= c2_off_engine(load_instance(f), get_action_space()).dead_end() is None
            if ok:
                chosen.append(f)
            if len(chosen) == n:
                break
        picked += chosen
    return picked


def run_arm(model, files, *, enabled: bool, n_samples: int) -> list[dict]:
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.format_prompt import active_prompt_version, encode_prompt
    from llm.stowlm.mask_mlx import generate_masked
    from llm.stowlm.sft_data import codec_for
    from stowage.actions import get_action_space
    from stowage.feasibility import make_feasibility_engine
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1

    from decode_trace import ArgmaxLog, RecordingFactory, override_stats

    rows = []
    with rule_freeze_v1(enabled=enabled):
        tokenizer, space = load_tokenizer(), get_action_space()
        for f in files:
            t0 = time.perf_counter()
            instance = load_instance(f)
            engine = make_feasibility_engine(instance, space)
            factory = RecordingFactory()
            decoders = factory(instance, engine, codec_for(instance, tokenizer), None, n_samples)
            prompt = encode_prompt(instance, tokenizer)
            log = ArgmaxLog(model)
            try:
                results, stats = generate_masked(log, prompt, decoders, temperature=1.0, max_tokens=20_000, seed=0)
            except Exception as exc:  # e.g. a mask-allowed action the move engine can't execute: record, continue
                rows.append({"instance": instance.id, "rung": instance.size_class,
                             "prompt_version": active_prompt_version(), "prompt_tokens": len(prompt),
                             "error": f"{type(exc).__name__}: {exc}", "wall_s": round(time.perf_counter() - t0, 3),
                             "decode_tokens": sum(len(d.tokens) for d in decoders),
                             "depths": [len(d.steps) for d in decoders]})
                print(json.dumps({"instance": instance.id, "error": rows[-1]["error"]}), flush=True)
                continue
            statuses = [r.status for r in results]
            rows.append({"instance": instance.id, "rung": instance.size_class, "prompt_version": active_prompt_version(),
                         "prompt_tokens": len(prompt), "decode_tokens": sum(len(r.tokens) for r in results),
                         "complete": statuses.count("complete"), "dead_end": statuses.count("dead_end"),
                         "max_tokens": statuses.count("max_tokens"),
                         "prefill_s": round(stats.prefill_s, 3), "decode_s": round(stats.wall_s, 3),
                         "wall_s": round(time.perf_counter() - t0, 3),
                         **override_stats(decoders, log.rows())})
            print(json.dumps({k: rows[-1][k] for k in ("instance", "prompt_version", "prompt_tokens", "complete",
                                                       "dead_end", "mask_override_rate_decision")}), flush=True)
    return rows


def summarise(all_rows: list[dict]) -> dict:
    rows = [r for r in all_rows if not r.get("error")]  # errored instances are listed, not averaged
    def total(key):
        return sum(r.get(key) or 0 for r in rows)

    over = {}
    for key in ("override_state_screen_rejections", "decision_state_screen_rejections"):
        mix: dict[str, int] = {}
        for r in rows:
            for rule, n in (r.get(key) or {}).items():
                mix[rule] = mix.get(rule, 0) + n
        over[key] = dict(sorted(mix.items(), key=lambda kv: -kv[1]))
    dpos, tpos = total("mask_override_decision_positions"), total("mask_override_token_positions")
    return {"instances": len(all_rows), "errors": {r["instance"]: r["error"] for r in all_rows if r.get("error")},
            "samples": total("complete") + total("dead_end") + total("max_tokens"),
            "complete": total("complete"), "dead_end": total("dead_end"), "max_tokens": total("max_tokens"),
            "override_rate_decision": sum((r.get("mask_override_rate_decision") or 0) * r["mask_override_decision_positions"]
                                          for r in rows) / dpos if dpos else None,
            "override_rate_all_tokens": sum((r.get("mask_override_rate_all_tokens") or 0) * r["mask_override_token_positions"]
                                            for r in rows) / tpos if tpos else None,
            "prompt_tokens_mean": total("prompt_tokens") / len(rows) if rows else None, "decode_tokens": total("decode_tokens"),
            "prefill_s": round(total("prefill_s"), 1), "decode_s": round(total("decode_s"), 1),
            "wall_s": round(total("wall_s"), 1), **over}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-tiny", type=int, default=12)
    ap.add_argument("--n-small", type=int, default=12)
    ap.add_argument("--n-samples", type=int, default=4)
    ap.add_argument("--model-path", default=str(REPO / "models/glimmer-30b-4bit"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--arms", nargs="+", default=["prompt-2.2", "prompt-3.0"])
    a = ap.parse_args()
    import stowage.interfaces  # noqa: F401
    from mlx_lm import load
    from stowage.frozen import stub_override

    from pool import STUB_REASON, c2_off_engine

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON):
        files = live_instances(a.n_tiny, a.n_small)
        model, _ = load(a.model_path)
        result = {"label": "DIAGNOSTIC A/B, base Glimmer 4-bit + mask, C2 off (tainted); never evidence",
                  "instances": [str(f.relative_to(REPO)) for f in files], "n_samples": a.n_samples}
        for name, enabled in (("prompt-2.2", False), ("prompt-3.0", True)):
            path = out / f"{name}.jsonl"
            if name in a.arms or not path.exists():
                rows = run_arm(model, files, enabled=enabled, n_samples=a.n_samples)
                path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            else:  # an arm completed earlier with the same inputs: reuse it
                from stowage.io import load_instance

                rows = [json.loads(line) for line in path.read_text().splitlines()]
                if [r["instance"] for r in rows] != [load_instance(f).id for f in files]:
                    raise SystemExit(f"saved {name} arm does not match the selected inputs; rerun it")
            result[name] = {"all": summarise(rows),
                            **{rung: summarise([r for r in rows if r["rung"] == rung]) for rung in ("tiny", "small")}}
    (out / "summary.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: {r: {x: v[r][x] for x in ("errors", "complete", "dead_end", "override_rate_decision",
                                                    "prompt_tokens_mean", "decode_tokens", "wall_s")}
                          for r in ("tiny", "small")} for k, v in result.items() if k.startswith("prompt")}, indent=1))


if __name__ == "__main__":
    main()
