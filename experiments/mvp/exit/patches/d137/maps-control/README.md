D137 map mix and a genuinely trained gold control — STAGED ONLY

Apply mapmix-trained-control.diff after D132; runner-control-hook.diff is a
minimal integration guide for the executive's combined runner patch. Apply
regression-tests.diff to install tests against the actual llm.exit modules.
No live src/ or tests/ files were edited.

Scheduler API: next_train(tiny, small, generation, gold_root=...,
seen_adapter_hashes=..., gold_fraction=.5). The gold quota is rounded once for
the complete generation, apportioned across rungs, with deterministic rotating
ship selection inside both strata. A quota shortage refuses atomically.

Teacher maps require a completed historical full-check report with every
required part run, no errors or violations, a matching input path/hash, matching
plan bytes/hash and a published case.lock. Plan-only or incomplete gold files
qualify for neither stratum. Historical evidence is pinned in each reservation;
it explicitly requires current-epoch verification by the candidate pipeline.
Scheduling does not claim those historical reports remain current or admit any
SFT targets. Current teacher pass coverage is the combined runner's responsibility.

Teacher novelty is always new_to_loop. No-gold maps receive
unseen_by_any_adapter only with a complete inherited training inventory proving
absence. Known-seen maps cannot fill that stratum; unknown inventories retain
new_to_loop labels. Seed/hash/identity held-out exclusions and fresh-map
reservations apply to both strata. On a new deployment directory, import the
old deployments' used/used_seeds/used_ids inventories while clearing generation
number reservations, so a restart does not repeat prior loop maps.

Control API: ensure_gold_control(..., fitness=fitness). Its compared_adapter
remains the selected parent. If that is the unchanged baseline, the control
borrows the fittest actually evaluated trained child's real SFT schedule,
using paired pass count then mutual-pass mean moves, and trains on current
checked gold only. The schedule_reference_adapter is recorded separately.
Missing actual SFT ancestry, missing paired fitness or zero training phases
refuses; the baseline is never called a trained control. The runner must copy
schedule_reference_adapter, compared_adapter_schedule_matched and
trained_control into audit metadata alongside the existing schedule fields.

APPROXIMATION: existing 64-map cap and fresh Adam per inherited SFT phase remain
explicit; DPO updates are separately disclosed as unmatched. The controlled
schedule is actual SFT ancestry, not a claim that the elite inherited updates.
Any attribution claim still requires the measured loop audit to beat this
trained gold-only control and the held-out baseline gate.

Verification (CPU, native threads 1):
UV_CACHE_DIR=$PWD/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 uv run pytest experiments/mvp/exit/patches/d137/maps-control/tests -q
30 passed.
git apply --check experiments/mvp/exit/patches/d137/maps-control/mapmix-trained-control.diff experiments/mvp/exit/patches/d137/maps-control/runner-control-hook.diff experiments/mvp/exit/patches/d137/maps-control/regression-tests.diff
Passed against immutable base-d132 and current live D132 source. The map scheduler hunk does not touch the
D134 dataset builder; root composes the runner hook with candidate-pool work.

Gold-control build parallelism uses D132 choose_workers(args), persists its
admitted capacity before each phase's build, and runs BOTH stale gold
verification and action replay with bounded ordered spawn pools. Native threads
remain 1. The chosen count is also recorded in the completed control receipt.
Injected test builders receive workers= alongside existing keyword arguments.
The data.py template is explicitly rebased on immutable base-d132; its entire
layered_replay and training-builder tail is byte-identical to that D132 base.
