# Freeze (validation) → 9.1 (test, once) → 9.2 → 10.2: final evaluation and showcase swap (fix-t8-sft, 30 Oct 2026)

**Test never chooses the adapter or the recipe (D110).**
- The final adapter is picked on **validation maps only**.
- 9.1 then runs **once** on test v2.1 with that adapter and this recipe. Its numbers are reported as they come out:
  they are never used to change the adapter, the recipe, the finisher budget or any threshold. A 9.1 rerun is allowed
  only to recover a crashed or incomplete run, with an unchanged command (per-instance resume).

**One recipe everywhere (D128, D128.5):**
- Greedy, sequential.
- Mask + D95 securing + look-ahead layers, ≤ 32 bounded rewinds.
- V4 ballast finisher scoped to finalize, with `FINALIZE_S` = 120 s for StowLM and two-step alike (no last resort).
- MLX cache limit 2 GB.
- 9.1 allows 5.7 repair but reports it separately. 9.2 runs **no repair**: the demo must never show a solver-repaired
  plan as StowLM's.

Times are measured on pilot 2 / val15 (small greedy with the recipe: mean 139 s, max 361 s per map; tiny ≈ 25–45 s).
They are wall times on a loaded machine; GPU-busy time is the decode share only (≈ 20–30 %).

## 1. Freeze: choose the final adapter on validation (~17:00 EDT 30 Oct; mvp-exit's loop)

- **Who:** the loop's final-audit phase, `src/llm/exit/runner.py` → `llm.exit.final_recipe.evaluate_recipe`.
- **What it compares:** the 8.1 parent (`experiments/mvp/sft/stowlm-v1/adapter`) and the final children, all with the
  9.1 recipe, on the validation maps only:
  - `experiments/mvp/valsplit/audit_small30.txt` (30 maps);
  - the 25 reserve maps of `frozen_val_small.txt` (70 val small minus ablation15 and audit30; `SPLIT.json`).
- **Output:** the loop's `<out>/final-audit/` (`baseline-row-d/`, `member-<i>-<key>/`). Never a test map.
- **Expected time:** ≈ 55 maps × 139 s ≈ **2.1 h wall per adapter**, of which ≈ 0.5 h is GPU busy. For the parent plus
  two final children that is ≈ 6.4 h wall if run one after another. With the 2 GB MLX cache limit, 2–3 adapters can
  share the machine (≈ 17 GB each) and cut this to ≈ 2.5–3 h. ASSUMPTION: val small maps cost what val15 did; the
  loop's own measured phase times govern.
- **The leader records the chosen adapter.** Its folder must contain `adapters.safetensors` +
  `adapters.safetensors.provenance.json` with clean lineage (`mvp_gold`, no stubs); otherwise the demo labels it
  UNVERIFIED.

## 2. 9.1 evaluation, once, on test v2.1 (15 tiny + 15 small) with the frozen adapter

```
FINALIZE_S=120 zsh experiments/mvp/sft/eval91/run_91.sh <frozen_adapter> experiments/mvp/runs/eval91-final
```

| step | what | expected time |
|---|---|---|
| `stowlm/` | the D128 recipe, 30 maps (`run_c1` loose; per-instance resume) | ≈ 45 min wall (≈ 10–15 min GPU) |
| `twostep/` | two-step, same pipeline and finisher budget (CPU) | ≈ 20 min CPU |
| `combine91` | `SUMMARY.md` / `summary.json`: pass, repaired pass, finisher time limit, dead end, checker fail; paired vs two-step | seconds |
| `polish91` | "StowLM + 15 s solver polish (hybrid)", a separate row (CPU) | ≈ 5 min CPU |

- **Optional best-of row:** `SAMPLES=3` adds a best-of-4 row (≈ +3× GPU decode). Decide before the run, never after
  seeing 9.1.
- **Machine load:** run StowLM and two-step one after the other, not concurrently (D128.5; `eval91/FINISHER_BUDGET.md`).
  Note `uptime` at the start and end.

## 3. 9.2 final showcase runs (the three small Laem Chabang calls)

```
zsh experiments/mvp/sft/eval92/run_92.sh <frozen_adapter>    # writes experiments/mvp/runs/stowlm-showcase/
```

- **Expected time:** ≈ 8–10 min wall (3 maps × ≈ 2.5 min; ≈ 2–3 min GPU), plus ≈ 1 min for the verify step (CPU) and
  ≈ 3 min for the optional instruction run below.
- **Inputs:** `random.small.29000201.0.7`, `random.small.29000022.0.7`, `random.small.29000102.0.7`, resolved by
  `showcase_files.py` from `experiments/mvp/corpus/showcase/feeder_{1700,500,800}-small.json`.
- **Output:** the layout mvp-ui's loader reads:
  - `manifest.json`, with `planner_kind: stowlm_adapter` and the adapter lineage;
  - `config.json`, whose `inputs` list each file and its SHA-256: the loader's instance binding;
  - `instances/<id>/{result.json,trace.jsonl,outcome.json,report.json}`.
- **`verify92.py` gate:** every stored `ok` plan gets a fresh full check and the result goes to `verify.json`. The
  command exits 1 if any shipped plan fails or was repaired.
- **Dead ends and finisher time limits stay recorded as such.** The demo shows them honestly (RECORDING.md "FINAL SWAP"
  step 4).
- **Two-step rival:** already in `experiments/mvp/runs/showcase-v2-twostep/instances/<id>/` for all three calls; the
  script checks this and flags any that are missing.
- **Optional instruction step:** after the main run, `run_92.sh` also decodes the feeder_1700 call under the D144
  instruction ("Keep class 8 at least 1 bay from bay 3"; `experiments/mvp/ui/STOWLM-INSTRUCTION-RUN.md`), writing
  `experiments/mvp/runs/stowlm-instruction-dg/`. Same recipe, same finisher budget, one finisher attempt per candidate
  (D139), no repair. A failure prints "not shown", adds a line to `verify.log`, and never fails 9.2. Expected: ≈ 3 min wall,
  ≈ 1 min GPU (one small map).
- **Folder name:** don't create any other `experiments/mvp/runs/stowlm-showcase*` folder. The video launcher registers
  every folder matching that glob.

## 4. 10.2 demo assembled (mvp-ui's contract: `experiments/mvp/ui/RECORDING.md` "FINAL SWAP", `CONTRACT.md`)

```
uv run python -m app --video-showcase --port 8851       # registers the shipped stowlm-showcase instances by itself
```

1. Shots 1, 3, 4, 5, 8 and 9 switch to the StowLM plan automatically once the run passes `FINAL_RULE`
   (`app/stage_stowlm.py`); their stand-in labels disappear.
2. Hand edits in `app/static/stage-shots.js`:
   - shots 2 and 6: `plan` → `{"run": "stowlm-showcase", "instance": "random.small.29000201.0.7", "method": "stowlm"}`,
     and delete `standin_tag`;
   - fill in the `measured` values / `results_card` StowLM row from 9.1's `SUMMARY.md` (with `source` + `match`).
3. `uv run pytest app/tests/test_stage_director.py`. Then record (RECORDING.md).

**Expected time, total, after the last generation:**

| step | GPU busy | wall |
|---|---|---|
| freeze on validation | ≈ 0.5 h per adapter | ≈ 2.1 h per adapter (≈ 2.5–3 h for three adapters sharing the machine) |
| 9.1 on test, once | ≈ 10–15 min | ≈ 45 min |
| 9.2 (+ optional instruction run) | ≈ 3–4 min | ≈ 13 min |

Recording and 10.2 need no GPU beyond the live small-instance path.

## Tested (CPU only; no showcase decoding, no test v2.1)

`tests/test_showcase_contract.py` copies a pilot-2 val15 run folder (row c, one shipped map and one dead end) into a
scratch `runs/stowlm-showcase-test` (a temp dir, not the real path). It checks:
- `verify92` re-checks the shipped plan (41 lifts) and records the dead end;
- `app.inputs.load_inputs` verifies the run (`stowlm_adapter`, clean lineage) and gives the dead end its card with no
  plan;
- the app finds the folder with its `stowlm-showcase*` glob (`showcase_run_folders`), and `shipped_entries` lists only
  the shipped plan;
- `FINAL_RULE` returns PREVIEW on a val map, and returns "final StowLM showcase run" once that id is treated as a
  showcase call. In that case the preview label disappears.
