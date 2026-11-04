"""C1 throughput model: GPU decode + CPU mask pool + checker selection → wall time per instance and per rung.

Per instance, one prompt, B sampled sequences decoded lock-step (one ``generate_masked`` call):

    prefill   = P / prefill_tok_s
    per action: GPU  τ · g(B),  g(B) = B / tok_s(B)     (τ tokens per action, one forward per token step)
                host B · h / W_eff                       (h = engine step per row at an action boundary, W workers)
      overlap (best case; prefetch + worker pool run during the forward):  max(GPU, host)
      serial  (worst case: no overlap at all):                             GPU + host
    decode    = A · per-action                            (A actions per plan)
    selection = c · k_complete / S                         (ballast + full check per complete candidate, S parallel)

The GPU is busy only for prefill + A·τ·g(B); utilisation = that / total. The model ignores dead-end attrition (it
shrinks the batch) and restow/discharge actions (they add steps). It is a planning estimate: inputs marked MEASURED
come from this folder's dry run or cited files; ASSUMED ones are labelled.

    uv run python experiments/eval-c1/model.py > experiments/eval-c1/schedule.md
"""

from __future__ import annotations

# tok/s by batch: docs/compute.md (measured 26–27 Oct, ~2k-token prompts): b8 100, b16 138, b32 125, b64 200.
TOK_S = {8: 100.0, 16: 138.0, 32: 125.0, 64: 200.0}
PREFILL_TOK_S = 435.0  # docs/compute.md: ~420–450 tok/s at batch 1
TAU = 9.4  # MEASURED: tokens per action incl. end-of-plan, dry run (604 tokens / 16 samples / 4 actions)
RUNGS = {
    # A = base PLACE steps (perf-1028 isolation report); h = C2-on engine step median / p95 s (same report);
    # P = prompt tokens: tiny MEASURED 573 (dry run); full ~11.6k (experiments/sft-pilot/README.md);
    # small/medium ASSUMED by interpolation on boxes. c = ballast+check per candidate: tiny MEASURED ~15–19 s
    # (dry run, sequential); larger rungs ASSUMED (no current-epoch measurement): scaled guesses, labelled.
    "tiny": {"A": 4, "h": 0.0024, "h95": 0.045, "P": 573, "c": 17.0},
    "small": {"A": 34, "h": 0.0775, "h95": 0.112, "P": 1800, "c": 30.0},
    "medium": {"A": 229, "h": 0.2055, "h95": 0.271, "P": 7000, "c": 60.0},
    "full": {"A": 414, "h": 1.428, "h95": 2.748, "P": 11600, "c": 120.0},
}
# ASSUMED, unverified: D64's in-port checker (P1-P6 at every lift) adds ~8-25 s per full check (logic review);
# added per candidate check on top of RUNGS[...]["c"] until the full pilot measures it.
D64_EXTRA_C = {"tiny": 2.0, "small": 8.0, "medium": 15.0, "full": 25.0}
HARNESS_CAP_W = 5  # run_c1.OFFICIAL_MASK_WORKERS: parent + 5 workers fit the harness cap of 6
DEEP_FULL_H = 6.8  # perf-1028: one full walk at depth ≥100 had ~6.8 s median per engine step


def instance_time(rung: str, B: int, W: int, *, k_complete: int | None = None, S: int = 1, h: float | None = None,
                  overlap: bool = True) -> dict:
    r = RUNGS[rung]
    h = r["h"] if h is None else h
    g = B / TOK_S[B]
    gpu_action = TAU * g
    host_action = B * h / max(1, min(W, B)) if W else B * h  # W=0: in-thread, serial over rows
    per_action = max(gpu_action, host_action) if overlap else gpu_action + host_action
    prefill = r["P"] / PREFILL_TOK_S
    decode = r["A"] * per_action
    k = B if k_complete is None else k_complete
    selection = (r["c"] + D64_EXTRA_C[rung]) * k / S
    total = prefill + decode + selection
    gpu = prefill + r["A"] * gpu_action
    return {"prefill_s": prefill, "decode_s": decode, "selection_s": selection, "total_s": total,
            "gpu_busy_s": gpu, "gpu_util": gpu / total, "host_bound": host_action > gpu_action,
            "cpu_s": W * decode + selection if W else decode + selection}


def table(n_per_rung: int = 75) -> str:
    lines = ["| rung | B | mask W | select S | per instance (min) | of which decode / select | GPU util | "
             f"{n_per_rung} instances (h) | CPU-h |", "|---|---:|---:|---:|---:|---|---:|---:|---:|"]
    for rung in RUNGS:
        for B, W, S in ((16, 0, 1), (16, HARNESS_CAP_W, 1), (8, HARNESS_CAP_W, 1), (16, HARNESS_CAP_W, "3a"),
                        (8, HARNESS_CAP_W, "3a")):
            # "3a": rank-order early stop (in planner.py since 28 Oct); ASSUMED 3 checks per instance (1 pass +
            # 2 failures or equal-move siblings). W is capped at 5 by the harness (6 processes incl. the parent).
            t = instance_time(rung, B, W, S=1, k_complete=3) if S == "3a" else instance_time(rung, B, W, S=S)
            lines.append(f"| {rung} | {B} | {W or 'in-thread'} | {S} | {t['total_s'] / 60:.1f} | "
                         f"{t['decode_s'] / 60:.1f} / {t['selection_s'] / 60:.1f} | {t['gpu_util']:.0%} | "
                         f"{n_per_rung * t['total_s'] / 3600:.1f} | {n_per_rung * t['cpu_s'] / 3600:.1f} |")
    deep = instance_time("full", 8, HARNESS_CAP_W, S=1, k_complete=3, h=DEEP_FULL_H)
    lines.append(f"| full, deep-prefix h={DEEP_FULL_H}s | 8 | {HARNESS_CAP_W} | 3a | {deep['total_s'] / 60:.1f} | "
                 f"{deep['decode_s'] / 60:.1f} / {deep['selection_s'] / 60:.1f} | {deep['gpu_util']:.0%} | "
                 f"{n_per_rung * deep['total_s'] / 3600:.1f} | {n_per_rung * deep['cpu_s'] / 3600:.1f} |")
    return "\n".join(lines)


def recommended(counts=None, time_limit_s: float = 3600.0) -> str:
    """The official C1 setting: B=8, W=5, rank-order early stop; per-instance time capped by --time-limit."""
    counts = counts or {"tiny": 75, "small": 75, "medium": 75, "full": 20}
    lines = ["| rung | instances | per instance (min) | rung total (h) | GPU busy | CPU-h |",
             "|---|---:|---:|---:|---:|---:|"]
    total = cpu = 0.0
    for rung, n in counts.items():
        t = instance_time(rung, 8, HARNESS_CAP_W, S=1, k_complete=3)
        per = min(t["total_s"], time_limit_s)
        total, cpu = total + n * per, cpu + n * t["cpu_s"] * per / t["total_s"]
        lines.append(f"| {rung} | {n} | {per / 60:.1f} | {n * per / 3600:.1f} | {t['gpu_util']:.0%} | "
                     f"{n * t['cpu_s'] * per / t['total_s'] / 3600:.1f} |")
    deep = min(instance_time("full", 8, HARNESS_CAP_W, S=1, k_complete=3, h=DEEP_FULL_H)["total_s"], time_limit_s)
    lines.append(f"| **total** | {sum(counts.values())} | | **{total / 3600:.1f}** | | {cpu / 3600:.0f} |")
    lines.append(f"| full if deep prefixes dominate (capped at {time_limit_s / 60:.0f} min) | {counts['full']} | "
                 f"{deep / 60:.1f} | {counts['full'] * deep / 3600:.1f} | | |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(table())
    print()
    print(recommended())
