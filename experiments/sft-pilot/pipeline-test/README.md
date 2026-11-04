# PIPELINE_TEST SFT → C1 loop (fix-t8-sft, 28 Oct 2026 19:40–20:15 EDT) — never evidence, never shippable

**Data** (`build_dataset.py` → `data/`): stats say `dataset_kind: pipeline_test`.
- **Why not real gold:** no published 6.1 tiny gold exists (every tiny-gold manifest has 0 accepted). The production builder can't admit any today: it needs production mask replay with C2 on, and the linear policy epoch is stale.
- **What it uses instead:** the 24 pilot-d58 tiny plans that the exact solver proved OPTIMAL and the full checker passed in their own, earlier epoch.
- **How:** each goes through `sft_data.build_example` with stowlm-prompt@2.2, giving 20 train / 4 val (594 tokens per example; completions 29–41 tokens).

**Refusals, all observed:**
- `train.py` without `--diagnostic` refuses the data as "not verified production gold". With `--diagnostic` but outside `sft-pilot/` or `tiny-chain/` it also refuses. No folder is created either way.
- The adapter's sidecar lineage carries the stub `stowlm.train.dataset: non-gold SFT data (dataset_kind='pipeline_test'): diagnostic adapter, never shippable`.
- `require_clean(planner.provenance)` and `run_evaluation(tiny_run=False)` both raise StubTaintError before writing anything.

**Training** (`adapter/`, `train.log`): the D31 config (16 layers, rank 8, scale 20, attn-block 1024, cosine 5e-5 with 5 % warmup, grad accumulation 4) for 200 micro-steps.

| run | wall | throughput | peak memory | loss |
|---|---:|---|---:|---|
| 200 steps on ~600-token tiny examples | 489 s (461 s training) | ~258 processed tok/s | 17.7 GB | train 3.69 → 0.17; val 3.64 → 0.22 |
| probe: 3 steps on 17,668 tokens (`long-17k-probe/`) | — | 88 s/step ≈ 201 processed tok/s | 53.0 GB | — |

- The probe matches D31, so the D31 budget stands: 10–15M tokens at 200–258 tok/s is **≈ 11–21 GPU-h**, plus about 4 % for validation evals. The lower end applies only to a mix weighted to short examples.
- `run.json`'s `tokens_per_second_mean` (11.2) counts mlx-lm's *loss* tokens (completion only), not processed tokens. That is a reporting bug to fix in train.py; the training itself is unaffected.

**C1 loop** (`c1-loop/`, run_c1.py loose, C2 off, best-of-8, 1 pool worker) on 3 val tiny instances:
- **Mechanics all work:** the adapter loads with its sidecar, all 8/8 samples complete, the 5-row trace replays in 1.6–2.2 s, and the manifest, result.json and report.json are written.
- **Speed:** decode ~35 tok/s; selection 128–152 s (8 candidates checked); 210–251 s per instance; 16.7 GB peak.
- **Outcome:** every candidate and the repair fail the checker. **This is input staleness, not adapter quality.** Today's checker also rejects the exact-optimal gold plan for the same instance: "Fixed background cargo capacity evidence: stale versioned cell/stack audit" (stale D58 background binding). Quality needs the regenerated epoch.
