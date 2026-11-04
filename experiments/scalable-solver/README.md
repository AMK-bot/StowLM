# Exact-window quality follow-up — scalable 2.6.3

Latest fix-wave C2-on evidence: [bounded per-rung pilot](fix-epoch-report.md), [matched search-effort comparison](fix-search-efficiency.md), and [fix log](../review/fixes/fix-t4-scalable.md). Small is **0/1 approved** under a15s search, versus historical1/30 under120s; the epochs/budgets differ, so this is descriptive only. No current approved production plan or independent exact optimum was established. Narrow search reached11/34 loads versus2/34 on the same input, but neither completed and its pipeline was slower. The earlier **5.4 v3 / C2-on G1 pilot** had one approved small plan and incomplete full ships; see [G1-README.md](G1-README.md). Older C2-off quality measurements below remain a separate historical campaign.

At its recorded historical revision, the pinned tiny plan improved **12 → 6 weighted lifts**, matching the earlier4.6 exact optimum, and passed that revision's full checker. Search took **8.61 s**. This is diagnostic C2-off evidence, **not current production quality or approved gold**. The pinned small incumbent was **40 lifts**, checker passing at that revision: five time-capped exact windows found no replacement, and the final safety gate rejected ten cheaper beam candidates that violated container securing. Its search took121.42s for a120s budget, including indivisible validation calls. The fresh tiny16.7% comparison uses3cases and prior-source same-input proofs; zero optima were independently proved for that revision.

Previous feasibility-index campaign: [README-feasibility23.md](README-feasibility23.md). Individual inputs, split audits, checksums, costs, checker violations and component versions are retained beside these results. No test specifications were generated or used.

## Implementation and admission

LNS releases a related group and searches alternative placements. Hatch groups, adjacent bay pairs, POD groups and yard-block retrieval sequences choose the cut; the entire dependent suffix is released so supports and retrieval dependencies remain valid. This is a bounded suffix neighbourhood, not arbitrary fixed-interior optimisation. The public 4.6 A* solver receives the residual snapshot with **num_workers=1**; the waiting parent adds no active search worker. Whole-plan windows on small manifests remove their first greedy commitments. Sources and the plain-language explanation are in `scalable_exact.py` and `scalable.py` (Shaw 1998; Hart et al. 1968).

Every exact replacement must be strictly cheaper on the **original** instance, replay its public feasibility mask, complete the manifest, and pass its full checker with ballast. A residual optimum is never advertised as a global certificate. A whole-root optimum is labelled as belonging to the configured engine domain. Search stops at the counting lower bound or a matching whole-root exact optimum. `complete` and `improve` signatures are unchanged.

Default ordinary windows receive 10 seconds and at most 16 boxes; whole-plan windows receive 90 seconds for manifests of at most 16 boxes. Each cap is clipped to the remaining overall budget. Native checker calls are indivisible and can slightly overrun the limit. Failed/unknown repairs preserve the incumbent. A supplied incumbent with ballast is verified by both the public mask and checker; cheaper beam replacements must also pass the original checker using that ballast. Different-ballast replacements use the exact-window path. Metrics expose checker rejections and the exact certificate's scope.

The required-data adapter was updated for the current checker wording for invalid heat/OOG clearances; no metadata or physical limit is invented. Custom test oracles retain their existing beam-only path.

## Fresh post-trim cohort

Three frozen train/validation specifications per rung were selected before generation, after fleet condition version `c3-fixed-background-5` landed. Generator inputs and backgrounds are unchanged during each paired comparison. Measurements use one active CPU core per process. Construction limit 60 s; improvement 120 s for tiny and 150 s for small; independent exact comparisons up to 90 s. Check qualification is `planner.finalize` followed by the full checker. All costs below exclude checker-failing departures.

| Rung | Constructed | Checker pass after improvement | Valid paired mean lifts | Mean gap to counting LB, before → after | Mean improvement time among valid pairs | Exact comparison |
|---|---:|---:|---:|---:|---:|---|
| tiny | 2/3 | 2/3 | 18 → 8 | 86.15% → 75.00% | 16.03 s | 2/2 match proved optimum 8 |
| small | 2/3 | 1/3 | 18 → 18 | 66.67% → 66.67% | 150.00 s | timed out; no proved optimum |
| medium | 0/3 inputs generated | unavailable | unavailable | unavailable | unavailable | background-balancing generation failure |

Tiny had one 60-second construction timeout. Small had one timeout and one checker-failing candidate; the latter's 34 lifts are **excluded** from quality averages. Medium failed before solver admission, including after the upstream trim fix; this is not a solver infeasibility proof. See `quality-posttrim-*/results/` and [quality-final-report.json](quality-final-report.json).

Diagnostic full-checker throughput, charging construction, improvement and validation time for **all attempted cases**, is **69.12 tiny** and **8.77 small checker-passing plans/core-hour**. Input generation and independent exact research comparisons are excluded and recorded separately. These three-case estimates are too small for reliable capacity planning. Production-approved throughput is **zero** under C2-off.

A separate earlier three-input tiny repeat improved 12/18/10 → 8/8/8, matching three independent exact optima, but all rows flag source changes during execution. It is retained as exploratory evidence, separate from the stable post-trim cohort. The final-source 120-second repeat (`quality-final-v263-tiny/`) returned **12/8/8**, all checker passing: mean 13.33 → 9.33 lifts and 84.07% → 77.78% counting gap. Two whole-root calls proved optimum 8; the first timed out at 90 s with a 12-lift incumbent. Mean improvement time was 46.75 s. Against the prior same-input optima of 8, mean excess is 16.67%, so this setting misses the 0–10% target. These timing-sensitive rows are retained in full. A separate all-three-input comparison with a 180-second root cap and 210-second improvement budget is in `quality-final-v263-tiny-long/`; budgets are never pooled or best-of selected. That longer run also returned **12/8/8** with two root optima and one timeout; mean improvement time rose to **93.53 s** without quality improvement. Final-source diagnostic throughput was **62.41** checked plans/core-hour at the shorter setting versus **34.64** at the longer setting.

## Remaining blockers and interpretation

C2 is enabled by default. Although cache warming reported completion, strict C2 rejects both a fresh tiny and small input at the root with stability/strength completion rows; the pinned tiny plan still passes the full checker. Evidence: `quality-posttrim-c2-probes.json` and `quality-pinned-strict.json`. Diagnostic measurements explicitly disable C2 and export **no approved gold plans**. This discrepancy requires the feasibility/linear owners; no physical constraint was weakened here.

The counting lower bound is loose: fresh tiny optima are **8**, while `lower_bound` returns **2**, leaving a **75% gap at the exact optimum**. The pinned small lower bound is zero. Thus a large reported counting gap alone does not establish poor tiny-plan quality. Small/medium quality and the requested production completion rates remain unestablished; this report does not claim those targets were met.

Search truncation, suffix restriction and reusing incumbent ballast are labelled algorithmic restrictions, not approximations to physics. No new physical approximation is introduced. Proposed leader status: exact-window LNS implemented and pinned tiny optimum restored; fresh final-source tiny mean excess 16.67% misses the 0–10% target, small quality improvement and production G1 remain open, and medium measurement is blocked by background generation.

## Reproduction and verification

`quality_windows.py` is path-parameterised and checkpoints every result. `quality_final_report.py` regenerates the separate-cohort aggregation; invalid costs cannot enter its paired averages. `pinned_small_recheck.py` reproduces the verified-incumbent regression on the frozen train input. All exact jobs explicitly use one worker and all numerical-library thread counts are one.

Focused final-source command: `uv run pytest -q tests/solver/test_scalable*` — **105 passed**, including original-mask/checker admission, bounded windows, timeout retention, root certificate scope, and rejection of cheaper checker-invalid beam plans. Logs: `pytest-quality-v263.log`. Compilation passed for the changed modules and experiment drivers. Whole-suite outcomes and compute closure are recorded below when the running checks finish.


Final verification: whole `uv run pytest -q` completed **exit 0** (`pytest-quality-final-full.log`), with upstream deprecation warnings only. Its log lifetime was 1,164.52 s. The earlier whole run had six foreign generator failures; all six passed on the required fresh-process repeat. The final whole suite started before the last safety edit; final-source focused tests afterward passed all 105 cases. No frozen signature changed. Aggregation assertions and compilation passed. Compute: **60 minutes estimated / 47.05 actual**, no GPU/cloud; own reservation released. The brief corrected pilot overlap and per-job accounting are disclosed in [compute.md](compute.md) and [quality-compute-report.json](quality-compute-report.json).
