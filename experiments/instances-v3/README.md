# Canonical Stage A instances v3

**Verified accepted subset released for solvers and task 6.1:** 595 instances from
2,240 completed requests; 1,645 requests were rejected. Use only each root or
profile manifest group's `shards` list as solver/gold-plan inputs.
`rejected_shards` are audit evidence and contain no usable instance. The inventory
is complete and `verification.json` has `passed: true`; accepted coverage is
incomplete. These necessary-screened inputs are not checker-approved plans.

There are **no accepted tiny instances**. The 132 medium instances are all
feeder_1700 and therefore all in the frozen test split; medium has no training
or validation coverage. Small accepts only feeder_500. Requested families cover
the scheduled axes, while accepted and rejected family coverage is recorded
separately in `verification.json`. Missing accepted groups are not filled with
rejected audits. See `RESULTS.md` for per-reason screening and timings and
`C2-YIELD.md` for the sampled tiny-yield diagnosis.

| Profile, both generators | Requested | Accepted | Rejected |
|---|---:|---:|---:|
| tiny | 800 | 0 | 800 |
| small | 800 | 240 | 560 |
| medium | 400 | 132 | 268 |
| full, ordinary profile | 120 | 112 | 8 |
| realistic_500 | 60 | 59 | 1 |
| realistic_1000 | 60 | 52 | 8 |
| Total | 2240 | 595 | 1645 |

Accepted frozen splits are **264 train / 20 val / 311 test**; rejected splits
are 938 train / 52 val / 655 test. The root `full` rung includes both realistic
profiles: 223 accepted total, comprising 112 ordinary full, 59 realistic_500 and
52 realistic_1000. Random-yard contributes 309 accepted instances and the
simulator contributes 286. Accepted targets contain 3,052 DG bookings across
438 instances.

Across 33,640 candidate attempts, 98.23% were screened out. C2 failures numbered
18,998 (56.47% of attempts), fail-closed provision rejections 1,936 (5.76%), and
background-balancing rejections 2,814 (8.37%). Reason counters can overlap and
are separate from the 1,645 rejected whole requests.

`uv run pytest -q tests/generate` and `uv run pytest -q` both exited 0, in
194.400 s and 1205.256 s respectively. Complete artifact verification passed in
6.720 s; main generation took 5780.958 s (96.35 min). Test evidence is in
`release-test-results.json`; full estimate/actual accounting is in `COMPUTE.md`.

This campaign uses current fleet geometry and booking-dependent D29 backgrounds,
retains DG sampling, and assigns every seed with the frozen `eval.splits` v1 API
before generation. Scheduled regular profiles cover three feeders, four port archetypes
and three noise/blocking levels. Realistic profiles use feeder_500/feeder_1000
full-export policies with dry20 equipment conditioning; counts follow capacity
and mass budgets rather than legacy 450/800-box targets. The mass-limited random
profiles contain 348/575 boxes on feeder_500/feeder_1000. The simulated profiles
realize 342–356 / 565–588 boxes under the same export-policy assumptions.

C1 checks are necessary bounds: they reject bookings that cannot fit, but passing
does not prove a loading plan exists. Single-shipment DG redraws preserve all
other bookings and are counted separately. The solver and full checker still
own feasibility. See `src/stowage/generate/random_yard.py`,
`src/stowage/generate/random_export.py` and `src/stowage/vessel/fleet_conditions.py`
for the sampling and loading-condition sources and approximations.

C2 qualification evidence is in `c2-qualification.json`. A C2 run requires a
successful whole-fleet cache warm-up and explicit per-instance model availability;
unqualified campaigns are labelled **C2 unscreened** in every manifest.
The policy-bank JSON has a supplemental pin in `policy-bank-pin.json`, captured
after main generation started. Its matching checksum was verified at completion;
the earlier unobserved bank history is not certified, and mtime is supporting
evidence only. This limitation is retained in `verification.json` and `RESULTS.md`.

One incompatible scheduled combination is excluded: ordinary full/feeder_1700
at small_feeder. Its 815-box mixed booking expects 1363.09 TEU, but the crane
reaches only 1260 cells; fitting the unconditioned length draw has probability
at most 1.33e-13. This exclusion does not relax any ship, cargo or port limit.
All individual scheduled family axes remain covered in the requested inventory.
Root manifests record the exclusion; accepted coverage is assessed separately.

Use the root and per-profile manifests as the canonical inventory. Each shard
has an instance JSON and an audit commit marker with checksum, physics source
pin, capacity/mass information, rejection counts and frozen split assignment.
Resuming rejects mismatched configurations or active physics. C1-only runs do
not depend on the unqualified linear source epoch; C2 runs pin it as well.
Prototype pilot epochs, if retained, are benchmark evidence, not canonical data.
