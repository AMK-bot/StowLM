# D27: capacity-derived random-yard demand

Generator **1.2.0** uses task 2.2's current `slice_preset` geometry directly.
Tiny keeps its six-cell restriction: four hold cells, two deck cells, one hatch,
six dry20 targets, with the source loaded/empty mix and DG sampling retained.
Other rungs reserve capacity for compatibility and cargo constraints instead of
requesting a fixed number of boxes from every vessel.

For non-tiny regions, let T be the physical TEU cell count, S_l the compatible
slot count for length l, p_l its normalized probability in task 2.5's equipment
mix, and E the expected TEU per box. Forty/forty-five-foot slots count disjoint
physical pairs. The target count is the minimum of:

- the rung's box ceiling;
- floor(floor(0.5 × T) / E);
- floor(floor(0.5 × S_l) / p_l), for positive p_l and a positive length-slot budget.

Tiny uses fill fraction 1.0 and dry20 conditioning, giving six targets. Realised
cargo must also fit the TEU and every per-length budget; expected-mix sizing alone
does not authorize an over-budget draw. `SizeRung.boxes` values **6/36/150/260**
remain maximum ceilings for task 4.3's explicit-volume adapters. This change from
fixed counts to capacity-derived defaults is a semantic specification change.
Family keys and SplitMembership's canonical identity selection are unchanged;
provenance records generator 1.2.0.

Candidates must pass **depth-0 C1 completion checks**, configured with
`lookahead=0` and `global_screen=0`. C2 is off, and no next-action search runs.
C1a uses compatibility matching: each group of remaining boxes needs sufficient
compatible capacity (Hall's matching condition; see `feasibility/capacity.py`).
A pass is a necessary condition, not a feasible plan. Audits record failed
`c1a`–`c1f` keys and a catalogue aggregate-capacity rejection tag, alongside the
TEU/per-length, reefer and mass rejection counts.

APPROXIMATION: sequential shipment conditioning avoids redrawing an entire large
load list repeatedly because one DG shipment has no compatible slot. After C1a
fails, only DG targets are probed individually with a single-box load list.
Individually impossible DG shipments are resampled from task 2.5 on the next
candidate, retaining other bookings and container identities. No sampled DG fact
is edited or waived. All whole-list budgets and C1 bounds are then rechecked.
`shipment_redraws` counts replacement draws. This conditions the accepted cargo
distribution; it does not retain independent whole-list rejection sampling.

The executive measured these **geometry-only** seed-1 cases:

| Vessel | Rung | Physical cells | Targets | Candidate attempts | Shipment redraws |
|---|---|---:|---:|---:|---:|
| feeder_500 | tiny | 6 | 6 | 1 | 0 |
| feeder_500 | small | 44 | 13 | 1 | 0 |
| feeder_500 | medium | 180 | 53 | 1 | 0 |
| feeder_500 | full | 372 | 111 | 4 | 7 |
| feeder_1000 | tiny | 6 | 6 | 1 | 0 |
| feeder_1000 | small | 60 | 17 | 1 | 0 |
| feeder_1000 | medium | 328 | 98 | 3 | 5 |
| feeder_1000 | full | 592 | 176 | 2 | 7 |
| feeder_1700 | tiny | 6 | 6 | 1 | 0 |
| feeder_1700 | small | 116 | 34 | 1 | 0 |
| feeder_1700 | medium | 880 | 150 (ceiling) | 2 | 6 |
| feeder_1700 | full | 1604 | 260 (ceiling) | 2 | 12 |

The feeder_1700 full case took **2.914 s**; all other cases took **0.014–0.427 s**.
These measurements exclude full-physics/checker acceptance. A single seed per
case does not establish production screening rates or solver feasibility.
Full-physics seed-1 generation subsequently passed clean save/load round-trips
and all mandatory C1 checks for feeder_500:

| Rung | Realised cargo TEU | Candidate attempts | Cached elapsed seconds |
|---|---:|---:|---:|
| tiny | 6 | 1 | 0.309 |
| small | 20.25 | 1 | 0.058 |
| medium | 88.25 | 1 | 0.072 |
| full | 183.0 | 4 | 0.311 |

The full case recorded two C1a candidate rejections and seven shipment redraws.
These C1 checks are necessary conditions; no full-checker-approved plan is claimed.

The requested generate+yard test run initially had one failure:
`tests/generate/test_instances_v2_random.py::test_generator_restores_original_module_and_determinism`
at line 44 asserts `attempts == 1`. Mandatory C1 correctly rejects the first
candidate and accepts the second; determinism and module restoration pass.
Rerunning that case confirmed the stale task 4.3 assertion. It was left unchanged
because it is outside this task's scope. The final requested run returned
**197 passed, 1 failed in 36.46 s**, with the same stale assertion the sole
failure, using:

`UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run pytest -q tests/generate tests/yard -o addopts=--import-mode=importlib`

Targeted random-yard + yard tests returned **47 passed in 9.07 s**;
`compileall` passed.

**Task 4.3 owner action required:** update both the stale attempt-count test and
the capacity integration. `instances.py` / `instances_v2_random` currently read
`SizeRung.boxes` as a fixed `n_boxes`, but it is now a ceiling. Use
`capacity_load_size` or the actual returned count before tagging explicit
`n_boxes`; otherwise old specifications can mislabel output quantities. Task
5.3's canonical split keys are unchanged. Its `GenerationReport` must also
propagate raw `completion_rejections`, `shipment_redraws` and `c2_screened`;
the current adapter drops these new audit fields. No task 4.3 files were edited.

Proposed decision text: **APPROXIMATION:** use sequential shipment conditioning
after C1a rejects individually impossible DG targets, resampling through task
2.5 while preserving every sampled DG fact. Recheck the complete candidate's
capacity budgets and all C1 bounds before acceptance. Report shipment redraws
and completion rejections; explicitly record C2 as off. This conditions demand
without relaxing constraints and does not certify a feasible plan.

No new heavy job was needed for this documentation update. An earlier
whole-list-rejection diagnostic was stopped through its own recorded session
with Ctrl-C after pathological runtime; sequential shipment conditioning
replaced it with the measured 2.914 s largest geometry case above.

Historical evidence stays versioned: `kcs-migration.md` describes generator
1.1.0/D20 regions and fixed demand, while `verification.json` and
`geometry-diagnostic.json` describe generator 1.0.0/pre-D20 geometry. None of
those measurements substitutes for D27 verification. No checker-approved plan
is claimed here.
