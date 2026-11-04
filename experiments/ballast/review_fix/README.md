# Ballast review qualification, 2026-10-28

Six frozen feeder_500 G1 small stows (20 export boxes each), with both 5-second process-cold and second-call measurements. The before and after calls receive byte-identical saved instances and plans. The six historical G1 candidate move sequences and bookings are preserved; their obsolete vessel snapshots are rebuilt using the current public small preset and `recondition_slice` (D40/D42/D48), with declared D50 consumable groups and voyage identities. Every source input is train/validation, never a held-out test input. These adapted snapshots are not represented as unchanged historical G1 inputs.

| Frozen case | Before cold / second | After cold / second | Initial exact hard-failing parts |
|---|---|---|---|
| small-100100015-2a1913beea | not_found 4.952s / not_found 4.953s | not_found 4.951s / not_found 4.954s | stability, visibility, lashing, arrival/stability |
| small-100100036-14a29a0f3c | not_found 4.952s / not_found 4.954s | not_found 4.953s / not_found 4.952s | stability, visibility, lashing, arrival/stability |
| small-100100051-63b32eb7c3 | not_found 4.955s / not_found 4.953s | not_found 4.952s / not_found 4.953s | stability, lashing, arrival/stability |
| small-100100075-7c468d575e | not_found 4.952s / not_found 4.953s | not_found 4.952s / not_found 4.954s | lashing, arrival/stability |
| small-100100240-5719de3306 | not_found 4.952s / not_found 4.953s | not_found 4.952s / not_found 4.953s | stability, lashing, arrival/stability |
| small-100100018-5df70044df | not_found 4.952s / not_found 4.953s | not_found 4.952s / not_found 4.953s | stability, lashing, arrival/stability |

**Result: before 12/12 `not_found` (100%); final after 12/12 `not_found` (100%).** No unsafe plan or infeasibility proof was returned. Same bounded six cases, no expanded campaign. Final after source fingerprints are unchanged across each captured checker, linear, model, vessel, and ballast source file (`after-final-v2/identity-start.json`, `identity-end.json`). The initial before campaign predates source-fingerprint instrumentation; the complete original ballast module family is frozen under `before_source/`, and all input hashes are recorded. Historical G1 failure rates are not pooled with this adapted-input campaign.

Exact initial diagnostic failure counts: arrival/stability 6/6, lashing 6/6, stability 5/6, visibility 2/6. All six complete exact checks; no endpoint diagnostic timed out or failed for missing data. None is independently known feasible, so 100% inconclusive is not proof of a 100% missed-feasible rate.

On case `small-100100015-2a1913beea`, the unchanged initial condition has departure containership total GZ-area clearance −0.01456 m·rad, blind distance 485.17 m against 232.8 m, and fitting/racking/guide ratios 1.709/1.294/1.101 (all require ≤1). The 30-second search repairs the departure GZ-area clearance to +0.01453 m·rad, but its arrival GZ-area clearance remains −0.01418 m·rad, visibility clearance −253.61 m, and fitting ratio 1.716. Those are actual checked candidate failures, not missing-data artefacts. No claim that all possible ballast fills fail these constraints is made.

The intermediate 30-second profile (`profile-small-100100015-2a1913beea.json`, before context deferral and the shorter LP sub-budget) completes 11 candidates. LP construction stops at its local deadline after 3.40 seconds; `solve_ballast_lp` is not reached. Thus this profile provides no evidence of conservative-margin LP infeasibility. Context construction takes 1.34 s; stability 15 calls/4.84 s, strength 10/6.71 s, visibility 11/9.13 s, lashing 10/1.16 s. Pressed branching takes 11.87 s and successive repair 5.91 s, including their checker calls (these nested totals must not be added). Large exact-check costs plus multiple coupled hard failures limit coverage. No tank branch, regulatory threshold, conservative LP reserve, or acceptance check is weakened.

## Independently feasible synthetic witness

`synthetic-witness/` freezes one complete ordinary dry 20-foot, 12-tonne export with legal hatch-off/load/hatch-on moves. Its declared zero-ballast witness passes the unchanged full checker and independently pure-derived arrival stability/strength checks (`witness-diagnostics.json`). This uses the integration test’s labelled synthetic submerged non-cargo balancing mass and broad synthetic strength ratings; it is a search regression, not a production small-slice throughput observation. Consumables are empty in this witness, so separate D50 regressions cover nonzero consumption.

- witness-before: cold not_found 4.954s, second ok 1.499s.
- witness-after: cold not_found 4.953s, second ok 3.877s.
- witness-after-context: cold not_found 4.952s, second ok 2.500s.
- witness-final: cold ok 4.568s, second ok 1.083s.

The original cold/second discrepancy reproduces the review’s suspected wall-clock outcome nondeterminism on an independently feasible case. Deterministic search order does not guarantee identical outcomes under cache-sensitive wall-clock termination. The final source finds the same independently feasible witness cold and warm: **synthetic witness `not_found` rate improves from 1/2 (50%) to 0/2 (0%)**; both returned plans independently pass the full checker. Final witness source hashes are unchanged (`witness-final/identity-start.json`, `identity-end.json`). This improvement is reported separately from the unchanged six-production-slice result.

## Limits and required follow-up

- The universal “find exact ballast or prove none exists for any stow” criterion remains unproved. A complete certified nonlinear search is a separate algorithmic project; alternatively, the leader must obtain approval for an explicitly bounded/inconclusive completion criterion.
- Production slice effectiveness remains unqualified on independently verified feasible inputs. The vessel/generator owners need a current, independently joint-checker-passing D42/D48/D50 small cohort (including background securing and visibility). Reusing infeasible or obsolete witnesses cannot establish missed-feasible coverage.
- Arrival uses the declared next-call LOCODE and density. Current-port berth draft/rules are cleared for the D50 seagoing endpoint; vessel loading limits remain hard. No next-berth acceptance is implied. The campaign declares the same synthetic seawater density at both endpoints. The model’s pure arrival function sets the 10% remaining consumables; ballast stays fixed.

## Reproduce

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/ballast/review_fix/campaign.py prepare --count 6
BALLAST_REVIEW_BEFORE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 10 uv run python experiments/ballast/review_fix/campaign.py run --label before-replay --count 6 --limit 5 --repeat 2 --check-limit 8
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 10 uv run python experiments/ballast/review_fix/campaign.py run --label after-replay --count 6 --limit 5 --repeat 2 --check-limit 8
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/ballast/review_fix/diagnose.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/ballast/review_fix/summarize.py
```

Resource estimates and actuals: `compute.md`. No cache warm, GPU, cloud, external network or login. `nice` priority adjustment and `ps` inventory are sandbox-denied; owned workers alone are terminated.
