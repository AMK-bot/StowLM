"""Record one real Glimmer reader answer (GPU owner only; imports MLX lazily)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import sha256_file

ROOT = Path(__file__).resolve().parents[4]
PROTECTED_CONTEXT_ROOTS = (
    "experiments/mvp/testv3",
    "experiments/mvp/reader/v3/sealed",
    "experiments/mvp/pipeline/maps/sealed",
)


def _public_context_path(path: Path, root: Path) -> Path:
    """Reject aliases into protected sets before opening any context bytes."""
    supplied = path.expanduser().absolute()
    resolved = supplied.resolve(strict=True)
    protected = tuple((root / name).resolve() for name in PROTECTED_CONTEXT_ROOTS)
    if (any("sealed" in str(candidate).lower() for candidate in (supplied, resolved))
            or any(candidate.is_relative_to(directory)
                   for candidate in (supplied, resolved) for directory in protected)):
        raise ValueError("sealed contexts are outside this task")
    return resolved


def _wait_admission(root):
    while True:
        try:
            state = json.loads((root / "experiments/fleet/core-state.json").read_text())
            age = time.time() - float(state["t"])
            mask = state["mask"]
            mask = int(mask, 0) if isinstance(mask, str) else int(mask)
            if 0 <= age < 120 and mask.bit_count() >= 8 and 0 <= float(state["load1"]) <= 45:
                return
        except (OSError, ValueError, KeyError, TypeError):
            pass
        print("Waiting for fleet CPU admission", flush=True)
        time.sleep(10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=Path("models/glimmer-30b-4bit"))
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--sentence", default="Close bay 7.")
    parser.add_argument("--context", type=Path, help="public synthetic ship context JSON; default fixture context")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; refusing to overwrite a recording")
    try:
        context_path = _public_context_path(args.context, ROOT) if args.context else None
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))
    _wait_admission(ROOT)
    context = json.loads(context_path.read_text()) if context_path else None
    # GPU admission is controlled by the fleet leader; this CLI never starts it itself.
    import mlx.core as mx
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler
    from experiments.mvp.reader.evaluate import render_prompt
    from experiments.mvp.reader.schema import messages
    from experiments.mvp.reader.v3.decoder import make_logits_processor
    from .fixture import CONTEXT
    from .recorder import RecordingLogitsProcessor
    if context is None:
        context = CONTEXT
    model, tokenizer = load(str(args.model), adapter_path=str(args.adapter))
    prompt = render_prompt(tokenizer, messages(args.sentence, context))
    tokens = tokenizer.encode(prompt, add_special_tokens=False)
    stops = set(tokenizer.eos_token_ids)
    if not stops:
        raise ValueError("tokenizer has no EOS token")
    processor = make_logits_processor(tokenizer, context, len(tokens), min(stops))
    import hashlib
    recorder = RecordingLogitsProcessor(processor, sentence=args.sentence, top_k=args.top_k, tokenizer=tokenizer,
                                        model="muse-glimmer-30b",
                                        adapter_sha256=sha256_file(args.adapter / "adapters.safetensors"),
                                        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
    mx.random.seed(199)
    chunks, generated = [], []
    for response in stream_generate(model, tokenizer, prompt=tokens, max_tokens=args.max_tokens,
                                    sampler=make_sampler(temp=0), logits_processors=[recorder]):
        chunks.append(response.text)
        generated.append(int(response.token))
    trace = recorder.finish(''.join(chunks), final_token_ids=generated, require_complete=False)
    # Metadata remains covered by StageRecord's output hash.
    from .trace import seal_trace
    trace["recorded_utc"] = datetime.now(timezone.utc).isoformat()
    trace = seal_trace(trace)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        stream.write(json.dumps(trace, indent=2, ensure_ascii=False) + "\n")
    print(f"Recorded {len(trace['steps'])} tokens; complete={trace['complete']}; {args.output}")


if __name__ == "__main__":
    main()
