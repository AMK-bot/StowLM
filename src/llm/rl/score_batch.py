"""Score a batch of GRPO rollouts in parallel: ``python -m llm.rl.score_batch JOBS.json OUT.json --workers N``.

Runs in the reward's own environment. In the Modal training image the vLLM stack pins numpy 2.3.5 (via numba), and
the V4 ballast finisher fails under it (``unknown:required_calculation``, every plan dropped; reproduced locally with
numpy 2.3.5 on 29 Oct). The project's numpy 2.5.4 stack lives in a separate venv, and the trainer calls this CLI with
that venv's interpreter. Each job is ``[instance_json_text, tokens, seed, tokenizer_path]``; each result is
``modal_grpo.score_rollout_job``'s dict (replayed policy tokens, allowed sets, reward, reason, CPU seconds), in job
order.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import time


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    from llm.rl.modal_grpo import score_rollout_job
    jobs = [tuple(j) for j in json.loads(a.jobs.read_text())]
    started = time.perf_counter()
    if a.workers <= 1:
        results = [score_rollout_job(j) for j in jobs]
    else:
        with ProcessPoolExecutor(a.workers) as pool:
            results = list(pool.map(score_rollout_job, jobs))
    import numpy
    tmp = a.out.with_suffix(".tmp")
    tmp.write_text(json.dumps({"results": results, "wall_s": round(time.perf_counter() - started, 2),
                               "numpy": numpy.__version__}))
    tmp.replace(a.out)


if __name__ == "__main__":
    main()
