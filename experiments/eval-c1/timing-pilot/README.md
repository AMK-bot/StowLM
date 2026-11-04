# Masked-decode timing pilot for the 6.2 / C1 budget (29 Oct 2026). Timing only; never evidence.

**Setup:**
- `pilot.py`: untuned base Glimmer 4-bit, prompt 3.0 with the rule-freeze switch on, best-of-4/8 at T = 1.
- The mask pool has 4 workers and is started once.
- No exact legal count runs inside the decode (logic-mask High 2).
- Each plan is capped at 45 min or 300 actions.
- Numbers are in `results-medium.json` and `results-full.json`; the budget re-fit is `budget.py` → `budget.md`.

**Inputs:**
- Current-epoch medium/full instances can't be generated today: the generator stops on "missing or stale D58 background physics binding", and the physics source changed mid-run (`gen_inputs.log`).
- So the inputs are w2-mask-perf's deterministic feeder500 medium/full fixtures (60/146 boxes to load, 68/158 aboard), the same ones its profile used.

**Limits on what this measures:**
- **C2 off:** the linear policy epoch is still stale, so C2 can't be on for medium as it will be at C1. The C2 screen's cost on the current source is unmeasured.
- **Mask source changed mid-pilot** (w2-l5-mask was editing). `mask.py` / `mask_mlx.py` / `_engine.py` SHA-256 prefixes:
  - medium runs: `bb151d1867bc` / `2bdd59aea77a` / `c35adb357b81`;
  - full runs: `bb151d1867bc` / `2bdd59aea77a` / `6fff521e280f`, unchanged through the full runs. The engine changed between the two sets of runs.

| rung | k | stop | lock-step s/action | mask CPU s/action (all samples) | hidden by pool | tok/s | prompt tokens | prefill s | peak GB |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| medium | 4 | finished (183 actions) | 0.88 | 2.20 | 67 % | 23.5 | 2,116 | 6.0 | 16.5 |
| medium | 8 | finished (200 actions) | 1.03 | 3.38 | 71 % | 35.7 | 2,116 | 5.1 | 16.8 |
| full | 4 | 300-action cap | 1.01 | 3.08 | 70 % | 22.4 | 3,715 | 9.4 | 16.5 |
| full | 8 | 300-action cap | 1.88 | 6.55 | 72 % | 24.5 | 3,715 | 7.6 | 17.1 |

- **Full at best-of-8 is mask-bound.** Each sample-action costs about 0.82 s of mask CPU (w2-mask-perf measures 0.71 s without counting), and 8 rows share 4 workers. With the official 5 workers, the projection is about 1.5 s/action (unmeasured).
- **Untuned base Glimmer takes about 3× the load list on medium** (183–200 actions for 60 boxes: restows and relocations), and best-of-8 had 6 of 8 samples dead-end.

**Budget re-fit** (`budget.md`; per plan = prefill + actions × measured s/action + 3 checker checks [assumed 30/60/120 s + D64 8/15/25 s], capped at 60 min):

| run | full plan length 500 actions | 1,000 actions |
|---|---:|---:|
| C1 (best of 8; 75/75/75 + 20 full) | 21.4 GPU-h | 26.6 GPU-h |
| 6.2 subset (75/75/75 + 10 full), base loads once per box | 17.6 | 20.2 |
| 6.2 subset, base takes 3× the load list (as measured on medium) | 28.5 | 31.1 |

- **Total with SFT (13–24 GPU-h):** 52–82 GPU-h against the planned 49–72. The low end fits; the high end doesn't.
- **What drives the high end:** 6.2's medium rung when base Glimmer wanders (19.5 GPU-h).

**Proposal (no limit changed):**
- Run 6.2's medium rung on a stratified paired subset of 40 (instead of 75) at best-of-4. Its medium cost falls from about 19.5 to about 9 GPU-h, and the 6.2 subset to about 18–20 GPU-h.
- That puts the total at about 52–71 GPU-h.
- C1 keeps all 75 medium at best-of-8.
- Re-measure with C2 on before admission, once the linear policy is requalified.
