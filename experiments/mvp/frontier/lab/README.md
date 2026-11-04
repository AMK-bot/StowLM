# Shared Frontier scorecard

Every pass requires the full `stowage.checker.check` on a completed plan. The
default finishing stage is Frontier's `_finish`: the V4 ballast finisher and full
checker. The runner checks plug-in finishers independently as well. Candidate
scores alone never establish a pass. See [CONTRACT.md](CONTRACT.md) for the lab's
data and compute rules.

Run from the repository root:

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab \
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/score.py \
  --set quick --method experiments/mvp/frontier/lab/ref/twostep.py:propose \
  --budget-s 60 --finalize-s 300 --tries 3 --workers 2 --seed 0 \
  --out experiments/mvp/frontier/lab/c1/my-method/results/quick
```

Use at most two CPU-busy processes across your whole team, one numerical thread
per process. The runner accepts `--workers 1` or `2`. `--budget-s` is the method's
construction/search budget; it does not include the separately budgeted finisher.
Methods must respect it themselves; the runner does not interrupt a method that
ignores its budget. `--finalize-s` defaults to 300 seconds per candidate, and
`--tries` defaults to 3. A finisher timeout or exception is a failure, never a
pass.

## Fixed sets and baseline

`--set` accepts `quick`, `quick_tiny`, `quick_small`, `quick_medium`, `medium12`,
or a path to a text file of repository-relative instance paths. `quick` combines
the three quick sets. Blank lines and comments in custom lists may be omitted;
use one instance path per line. Every input is checked against the sealed-data
rules before being opened.
Custom lists may use the approved tiny/small validation inputs and TRAIN inputs
recorded by the D185 cohort or V6 metadata. Unknown paths fail closed.

The selection rules are fixed independently of experimental outcomes:

| Set | Selection |
| --- | --- |
| `quick_tiny` | Every sixth line of validation's 48 tiny maps; eight maps. |
| `quick_small` | Sort instance IDs in `runs/val-0.9`, take every fifth ID until ten are selected. |
| `quick_medium` | First V6-certified feeder_500 and feeder_800 map, then D185 cohort map `random.medium.185100000.0.2`. |
| `medium12` | All twelve maps in the D185 medium cohort. |

Ordinal strides are one-indexed: tiny lines 6, 12, ..., 48, and small sorted
positions 5, 10, ..., 50. V6 summaries are ordered lexicographically by path.

`sets/MANIFEST.json` records each selection and its input SHA-256. `baseline.json`
records stored 0.9.0 outcomes, lift parts, and assignments, with evidence paths.
The tiny and small `run_maps.py` rows preserve final `MovePlan` JSON. These allow
the stored reference to preserve the original container loading order instead of
guessing it from an unordered assignment. A missing stored run or plan remains
null; medium V6 evidence does not become a 0.9.0 pass. Baseline unknowns must not
be interpreted as zero lifts or failed 0.9.0 runs.

## Plug-in signatures

Plug-ins are imported from a file with `path.py:function`; no installation or
source changes are needed. The same mutable context dictionary is passed to all
stages for one map:

```python
def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    # Return candidate complete stows. Each candidate is either
    # {box_id: slot_id} or an ordered tuple of Place(box_id, slot_id).
    ...

def order(instance, assignment, *, seed: int, budget_s: float, context: dict):
    # Return ordered Place actions, or None when construction fails.
    ...

def finish(instance, plan, *, seed: int, finalize_s: float, label: str,
           context: dict):
    # Return PlannerResult, MovePlan, None, or (result_or_None, metadata_dict).
    ...
```

Use `--order path.py:function` to replace assignment sequencing and `--finish
path.py:function` to replace finishing. `plan` is the expanded `MovePlan` from the
ordered candidate. The runner ranks legal candidates by `context['problem'].score`
and tries the cheapest first. Up to `--tries` candidates enter finishing; the
first independently checked pass is retained. No finisher metadata can override a
failed full checker.

Context supplies `problem` (`alns.Problem`), `stored` (stored assignment or None),
`stored_plan` (stored final plan JSON or None), `stored_lifts`, `baseline` (the
baseline row), `instance_path`, `finalize_s`, `tries`, and a mutable
`method_metadata` dictionary for JSON-serializable diagnostics. Private context
values may hold process-local objects, but only JSON-serializable diagnostics
belong in `method_metadata`.

## References

`ref/stored.py:propose` returns the stored plan's ordered container actions when
available, otherwise its assignment, otherwise no candidates. It still goes
through normal scoring, V4 finishing, and the full checker. Re-finishing can
change ballast, operational timing, or hatch handling; compare measured lifts
with the stored row and report differences.

```bash
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab \
.venv/bin/python experiments/mvp/frontier/lab/score.py \
  --set quick_tiny --method experiments/mvp/frontier/lab/ref/stored.py:propose \
  --budget-s 0 --workers 2 --out experiments/mvp/frontier/lab/c1/fx-lab/stored-tiny
```

Repeat with `--set quick_small` and a separate output directory.
`ref/twostep.py:propose` calls `TwoStepPlanner.plan` using the supplied method
budget and the same `global_screen=0` feasibility configuration as Frontier.

`ref/frontier09.py:propose` wraps `planner.plan_and_check` itself. That function
already includes internal finishing. Use the explicit accompanying finisher to
reuse that result and measure the portfolio only once:

```bash
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab \
.venv/bin/python experiments/mvp/frontier/lab/score.py \
  --set quick_small --method experiments/mvp/frontier/lab/ref/frontier09.py:propose \
  --finish experiments/mvp/frontier/lab/ref/frontier09.py:finish \
  --budget-s 60 --workers 2 --out experiments/mvp/frontier/lab/c1/fx-lab/frontier09
```

The adapter verifies the actions still match the internally finished result;
the runner then independently checks it. Without this explicit `--finish`, the
default V4 stage finishes again, so its wall time includes both finishing
stages. For this reference, `propose_s` includes internal finish time and
`finish_s` is the outer stage only. The adapter forces the portfolio's master
solver to one worker process-locally and restores the imported function in
`finally`; original source is unchanged. This respects lab compute limits but
means wall-clock-bounded search may differ from stored two-worker 0.9.0 runs.

## Outputs and comparisons

Each map writes `<instance_id>.json` and the directory also contains
`summary.json`. These are the per-map fields:

| Fields | Meaning |
| --- | --- |
| `instance_id`, `input`, `size`, `n_boxes` | Map identity, repository-relative input path, tiny/small/medium group, number of load-list boxes. |
| `passed` | Independent full check succeeded on the finished plan. |
| `lifts`, `weighted` | Checker's `weighted_nonproductive` objective; `weighted` is a compatibility alias. Null without a pass. |
| `parts` | Raw `relocations`, `hatch_now`, `hatch_later`, `restow_now`, `restow_later` lift counts from the checked report. Weights in the input objective determine their contribution to `lifts`. |
| `assignment`, `actions`, `plan` | Passing candidate's box-to-slot dictionary, ordered Place actions, and full finished plan JSON. Null/absent without a pass. |
| `propose_s`, `order_score_s`, `finish_s`, `wall_s` | Wall seconds in method, sequencing/scoring, all finish attempts including independent checks, and total evaluation. |
| `status` | `passed`, `failed`, `no_candidates`, `method_error`, `input_error`, or initialization `error`. |
| `failure_reason` | Final failure explanation; null on a pass. |
| `tries` | Per-candidate score, finish result, timing, pass/failure evidence, and finisher metadata. |
| `candidate_errors` | Candidates rejected during complete-assignment validation, ordering, or scoring. |
| `method_metadata` | JSON-serializable diagnostics supplied through context. |
| `baseline_passed`, `baseline_lifts` | Stored 0.9.0 comparison; unknown evidence is null. |
| `input_sha256`, `config`, `run_key` | Input digest, run settings/code digests, and cache identity. |
| `complete` | Atomic row write completed, including when this map failed. |

Reuse an output directory to resume. Only a complete row whose run key matches
the input, baseline, settings, plug-in file hashes and shared source hashes is
reused. A failed map is also a completed measurement and resumes unchanged; use
a new directory or remove that map's row to retry it. Changed budgets or code
invalidate the cached measurement automatically. Interrupted temporary writes
are never counted as completed rows.

Summaries report tiny, small, and medium separately. Pass counts compare all
selected maps with known stored 0.9.0 evidence. Mean lifts and wins/ties/losses use
only maps both methods passed. `paired_mean_change` is mean candidate lifts minus
baseline lifts in absolute lift units. `paired_mean_change_ci95` is a percentile
95% interval using 10,000 bootstrap samples of whole map pairs and the run seed.
Lower lifts are better; negative changes and wins mean an improvement. Wall time
includes failures.
There is no combined lift mean across sizes, and no paired estimate when there
are no mutual passes.

`summary.json` places size-specific results under `by_size`: `maps`, `passes`,
`baseline_passes`, `baseline_measured`, `baseline_unmeasured_ids`, `mutual_passes`,
`mean_lifts`, `baseline_mean_lifts`, `paired_mean_change`,
`paired_mean_change_ci95`, `wins`, `ties`, `losses`, and `mean_wall_s`. Its
`bootstrap` object records the sampling convention. A tie permits floating-point
difference up to `1e-9` lifts. Unknown baseline evidence is reported separately
and excluded from the baseline pass denominator.
Unparseable inputs whose size cannot be determined appear under
`unclassified_errors` and do not enter any size mean.

Run the scorecard tests with:

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 \
PYTEST_ADDOPTS='-o cache_dir=experiments/mvp/frontier/lab/c1/fx-lab/.pytest_cache' \
.venv/bin/python -m pytest experiments/mvp/frontier/lab/tests -q
```

## Stored-reference verification

The initial end-to-end sweep reproduced every available stored plan's assignment
and lift count, with no finisher lift differences:

| Set | Passes vs stored 0.9.0 | Paired mean lifts vs stored | Mean wall seconds/map |
| --- | --- | --- | --- |
| `quick_tiny` | 8/8 vs 8/8 | 6.375 vs 6.375 | 95.53 |
| `quick_small` | 9/10 vs 9/10 | 13.889 vs 13.889 | 167.97 |
| `quick_medium` | 0/3; 0 baseline maps measured | No paired maps | 0.66 (no candidates) |

Both paired mean changes and bootstrap intervals are exactly zero. One small
historical failure has no stored plan. All three medium inputs have no stored
0.9.0 plan; the medium timing measures that no-candidate path, not solving.
The full suite passed 25 tests, including a real full-checker rejection of an
illegal finished plan. The tiny sweep also resumed entirely from cached rows.

See [REFERENCE.json](c1/fx-lab/REFERENCE.json),
[verification](c1/fx-lab/VERIFICATION.json), and
[notes](c1/fx-lab/NOTES.md) for details. Per-map rows and size-specific summaries
are in `c1/fx-lab/results/stored-quick-{tiny,small,medium}/`.
