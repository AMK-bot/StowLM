# Production epoch requalification — 27 October 2026

The source of truth is the production method-.1 liquid-transfer reference qualified in `experiments/stability/fallback/FINAL.md`. All three current KCS production hulls and all 26 tanks (including DO/FW) are included. Cargo cases use 20%, 60% and 100% of each production booking ladder; their exact displacement/draught bands and full input hashes are retained per case.

The public model defaults to a separate reserve for every direction and strength station: `1.1 × (measured unsafe envelope + explicit reference-transfer allowance)`. The envelope includes the hull-family calibration and physical training populations. Safe-side GM gaps are reported separately and never charged again as unsafe error. Reference transfer includes paired production-checker comparisons and the qualified method-.1 GM allowance at a possible criterion crossing.

LIST uses certified transverse-moment boundary errors rather than converting a worst angular error into an empty feasible interval. The lower-GM reserve remains in the restoring moment. The boundary experiment covers both ±2° limits, with 64 calibration and 64 independent holdout conditions per direction and ship; it also checks unrestricted equilibrium roots on the stable branch. Explicit floating-point roundoff is charged to transfer reserves. This is empirical qualification, not a uniform proof over all loading conditions; departure plans still require the exact checker.

## Reproducibility and adaptive enrichment

Each hull has 192 raw family conditions (seed 3701), then three 128-condition physical training populations (seed 3793). Initial 64-condition holdouts (3794) exposed station SF and trim/draught tails. Their original records remain unchanged and are explicitly retained as additional training evidence. New 64-condition holdouts (3795) passed for the 500 and 1700. The 1000 exposed three further trim/draught tails; these also remain unchanged and are explicitly retained as a second enrichment. Seed 3796 exposed a further trim/draught failure and remains archived. Per-state replay located the largest upper-trim error at about 9.57 million kg with partly filled tanks; low-displacement coverage alone was a misleading aggregate correlation. Targeted training samples interior displacement strata and both trim frontiers with L1 projection retaining partial-fill diversity, Seed 3797 passed trim/draught but exposed a 28.6 kN station SF reserve excess near midships. Per-state replay isolated bow-down trim and asymmetric partial DB-M fills. Two 256-condition structured DB-M/HEEL/WING-A knot sweeps (seeds3823/3824) covered all twelve pair/pattern classes; all known previous failures were retained explicitly as training. The final independent1000seed3798 then passed all metrics/stations. No final holdout seed participates in that hull's training envelope, and the multiplier stays 1.1.

`failure-history-final.json` retains all failed promotion reports and their SHA256 identities. `current/`, `confirmation/` and `final/` retain all earlier observations. `qualified/` retains the failed3797 round. `production/` contains the final independent observations and promoted report/table. Certificates pin input content, numerical builder versions, the independent holdouts, boundary evidence, reference qualifications and fresh-coefficient equivalence checks. An absent or stale epoch fails closed before any expensive recalibration. Background fixed loads are instance constants and do not invalidate coefficients or hull-family calibration.

## Commands

The D30 hash scheme is now version `2`: every declared numerical dependency
contributes its whole parsed AST, with leading module/class/function docstrings
removed and source positions excluded. Comments are absent from the AST. No
selector narrows the hashed file; code, constants, imports and execution order
remain bound, and invalid Python fails closed. This migration revokes all old
epochs. New runs use `freeze.py` (copied to `<run>/closure/guard.py`) for the same
live source hash. Parameter/offset bytes and recorded evidence checksums remain
exact; its temporary catalogue consistency guard hashes parsed TOML values.
Historical run guards and observations are preserved unchanged. See
[`hash-scheme-1028/costing.md`](hash-scheme-1028/costing.md) for frozen-copy costs
and the final launch preparation; no epoch was run for that costing.

The review follow-up adds a separate application profile binding margin policy,
boundary calibration, holdout qualification and row construction to certificates
and bank entries. There are now nine profiles over 36 files; the numerical
profiles retain their own scope. Policy application is version 1.0, margin policy
1.5, and FSM/coefficient builders 1.1 (the flat-geometry input coverage fix).
Capture alone is a bootstrap state. Public live checks require both the immutable
producer manifest and its finalization receipt, and signal ABORT if either is
missing or changed. The final launcher also requires L4's D64 in-port acceptance
command before capture; see the command audit for its report contract.

Recorded scheme-1 regression data keeps its original hashes and is replayed in
an explicit archive namespace. It cannot supply a current production certificate.
Live scheme-2 admission and analytic rows are checked separately. After real
FINAL qualification, the assertion-preserving test migration explicitly selects
scheme 2 and the fresh evidence root, invoking the production builders and guards
again; there is no fallback based on whether fresh evidence happens to exist.

All numerical commands use single-threaded BLAS and `UV_CACHE_DIR=$PWD/.uv-cache`. The ledger reservation covers at most three CPU workers and four GB during final confirmation/warm-up; no GPU is used. The pilot measured 10.305 s for two raw states and 487 MiB peak RSS, before admitting the larger sweep.

- `uv run python experiments/linear-model/epoch_requalification/driver.py --ship SHIP --output OUTPUT --phase raw|train|holdout`
- `uv run python experiments/linear-model/epoch_requalification/boundary.py --ship SHIP --output OUTPUT --samples 64 --holdout-samples 64`
- `uv run python experiments/linear-model/epoch_requalification/promote.py --input experiments/linear-model/epoch_requalification/production --first-failure-report experiments/linear-model/epoch_requalification/qualification-provenance.json --apply`
- `uv run python -m stowage.cache_warm`
- `uv run python experiments/linear-model/epoch_requalification/public_validate.py --root experiments/linear-model/epoch_requalification/production --output experiments/linear-model/epoch_requalification/public-validation.json --latency-variants 10`
- `uv run pytest -q tests/linear`

Results and execution statuses are recorded in `FINAL.md` after validation.
