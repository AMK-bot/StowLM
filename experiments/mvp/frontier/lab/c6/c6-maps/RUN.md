C6 sealed holdout generation uses the copied D192 derivation and an unmodified,
byte-verified local corpus-builder/runtime snapshot. The planned grid is twelve
parents and seventy-two derivatives. Reps are 0 and 1; port/noise are fixed to
D192's laem_chabang_like / 0.5 for both generators. A failed parent is recorded
and the next predetermined rep is attempted without loosening checks or
requesting replacement seeds.

All generation runs serially with OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
VECLIB_MAXIMUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 and
PYTHONDONTWRITEBYTECODE=1. Commands are launched with nice -n 5, but this sandbox
rejects setpriority (Operation not permitted); effective nice remains 0.
execution-*.json records the owned PID, effective nice and benchmark marker.
No unowned process is inspected or controlled. Runtime and caches have local
independent copies; temporary files are directed inside this folder.

Commands, from repository root, with the environment above:

    nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c6/c6-maps/generate.py generate
    nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c6/c6-maps/test_seed_disjoint.py
    nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c6/c6-maps/generate.py verify

requests.json freezes derivation, builder, source snapshot, registration,
amendment, helper and pre-audit. The manifest contains only accepted maps.
Rejected parents/derivatives remain in cohort.json and parent row reports.
completion_failures is the inherited diagnostic, not a selection criterion.
No planner, assignment solver, scorer or plan-checking run is invoked.
The builder's mandatory physical background gates are retained unchanged.

The seed audit excludes this folder and never enters testv3/ or valsplit/.
Those explicitly forbidden directory contents are not certified; external
request/cohort/split/manifest metadata referencing them is covered.
The raw inventory includes ignored/untracked files and skips tooling directories
listed in the audit. The final executable test performs a fresh metadata scan.
Catalog registration is intentionally left to the leader. No commit is made.
