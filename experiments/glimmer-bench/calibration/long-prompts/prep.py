"""Long-prompt conversion calibration inputs (task 0.1 extension, 29 Oct 2026): prompt-3.0 token IDs at ~4k, ~6k and
~12k prompt tokens, each followed by an answer continuation so the KL also covers action tokens.

- fixture_full_4k: w2-mask-perf feeder500 full timing fixture (experiments/eval-c1/timing-pilot/inputs).
- measure_full_6k: perf-1028 corpus measure-full instance (the format review's full-ship sizing source).
- dense_800_14k: the dense 800-box pilot (format review's 18,006-token example); continuation = its archived
  format-only 800-action completion re-encoded with the current codec (first ``--cont`` tokens).
For the first two the continuation is format-valid Place actions (load-list boxes into ship slots in order); legality
doesn't matter for a next-token KL. Positions scored per prompt: the whole continuation plus the last 1,024 prompt positions.

    uv run python experiments/glimmer-bench/calibration/long-prompts/prep.py OUT [--keep 2048]
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SOURCES = {
    "fixture_full_4k": ("experiments/eval-c1/timing-pilot/inputs/perf-full-feeder_500.json", None, 1024),
    "measure_full_6k": ("experiments/feasibility/perf-1028/corpus-instances/measure-full.json", None, 1024),
    "dense_800_14k": ("experiments/instances-v2/random_yard/realistic_1000/seed-00500011.json",
                      "experiments/sft-pilot/data/17k/train.jsonl", 2048),
}


def main():
    import stowage.interfaces  # noqa: F401
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.format_prompt import active_prompt_version, encode_prompt
    from stowage.actions.space import Place
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1

    out = Path(sys.argv[1])
    keep = int(sys.argv[sys.argv.index("--keep") + 1]) if "--keep" in sys.argv else 2048
    out.mkdir(parents=True, exist_ok=True)
    tokenizer = load_tokenizer()
    prompts, meta = {}, {}
    for name, (src, completion_src, cont) in SOURCES.items():
        with rule_freeze_v1(enabled=False):  # persisted sources predate activation (format review's loader)
            instance = load_instance(REPO / src)
        with rule_freeze_v1(enabled=True):
            prompt = list(encode_prompt(instance, tokenizer))
            version = active_prompt_version()
        codec = ActionCodec(tokenizer, [b.id for b in instance.containers])
        if completion_src:
            archived = json.loads((REPO / completion_src).read_text().splitlines()[0])
            assert archived["instance_id"] == instance.id
            actions = codec.decode_plan(archived["input_ids"][archived["prompt_len"]:])
            completion = list(codec.encode_plan(actions))[:cont]
            kind = "archived format-only completion (sft-pilot 17k), re-encoded"
        else:
            cells = sorted({c.id for c in instance.vessel.cells})
            actions = [Place(box, cell) for box, cell in zip(instance.load_list, cells)]
            completion = list(codec.encode_plan(actions))[:cont]
            kind = "format-valid Place actions (load list in order into ship cells in order)"
        ids = prompt + completion
        prompts[name] = ids
        meta[name] = {"source": src, "prompt_tokens": len(prompt), "continuation_tokens": len(completion),
                      "total_tokens": len(ids), "keep": min(len(completion) + 1024, len(ids)), "prompt_version": version,
                      "continuation": kind, "scored_continuation_positions": len(completion), "scored_prompt_positions": 1024}
        print(name, meta[name], flush=True)
    (out / "prompts.json").write_text(json.dumps(prompts))
    (out / "meta.json").write_text(json.dumps({"keep": keep, "prompts": meta}, indent=1))


if __name__ == "__main__":
    main()
