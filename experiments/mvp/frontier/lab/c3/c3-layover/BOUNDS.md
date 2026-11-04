# Arrival-aware certified lower bound

`bound.py` is a private copy of `lab/c2/c2-hatch/bound.py`, extended here;
production source and the original Cycle 2 files remain unchanged. Interface:

```python
lower_bound(instance, *, budget_s=10.0, seed=0, tables=None) -> BoundResult
```

The result retains the Cycle 2 fields. `hatch_lower_bound` is a historical name:
it now bounds the **combined current hatch, current arrival restow, future hatch
and future restow** cost. `witness_hatch`, `witness_current_restow` and
`witness_restow` separate a relaxation incumbent's costs; `moved_arrival` lists
its current movement flags. A witness is never a checker-passed plan.

## Graph and validity argument

Each departure box chooses a physical footprint. Native feasibility tables
already contain loads plus arrival boxes minus the current discharge list.
Discharge targets are removed from departure packing, but remain vertices in
the arrival-access graph. An arrival through-box has one Boolean saying it is
handled now. Selecting a final slot different from its arrival slot requires
that flag. Handling a lower arrival box requires handling each through-box
originally above it. Handling a hold box opens its original cover; an opened
cover requires handling all original through-boxes on its deck. These
implications propagate across both physical columns of a long box. Loading or
replacing any lower box below an original through-box also requires that upper
box's handling flag, even when the upper box returns to its original location.
Flags count boxes once, not conflict edges.

Every real current move history supplies a feasible flag assignment: mark each
through-box that moved at least once, mark each cover opened, and use the real
departure footprints. A true flag costs **one current restow lift**, because a
direct ship shift is legal; requiring two would be invalid in the general
Instance domain. Original unchanged boxes therefore act as fixed prefixes only
when their movement flag is false. Flags may be true while final coordinates
are unchanged. Original deck cargo already blocking a cover is charged when
that cover must open, including openings forced by new loading, rather than
only when a discharge target happens to lie beneath it.

Current cover costs count each closed cover opened at least once as two lifts,
and each initially off cover once for its departure closure. Current discharge
access and loading share an opening. A retained arrival box merely remaining
in a hold does **not** force a current opening. Original hold access and
handled final hold placements do. The model can leave extra open flags true;
nonnegative costs make this harmless.

For each later call, the copied D15 closure starts at that call's departure-stow
discharge targets, adds deck boxes over their opened covers, and propagates
upward through both halves of long boxes. It counts each surviving blocker
once per call. Existing arrival cargo participates just like a newly loaded
box. A moved arrival box's later costs use its **chosen departure slot**, not
its original location. Thus moving now and accepting future restows remain
competing choices; charging an unconditional arrival-stow projection in
addition would be invalid. Repeated PODs resolve to the first later visit.
Frozen crossing requirements remain relaxed.

Packing retains cell exclusion, bottom-up support, outward-rounded stack mass
and height envelopes, and the full checker's hard homogeneous-length hold
securing condition. Stage B tolerates inherited soft E1/E2 violations, so the
copied weight-order/closure and E6 cuts are disabled whenever arrival cargo
exists. Original through slots are admitted even if current handling reach
would reject them, since an untouched box need not be lifted at this call.
DG eligibility rows are widened to every matching-size footprint: the original
state-dependent unary DG test need not witness all legal supported placements.
These choices enlarge the relaxation and sacrifice strength for validity.

Every actual checked plan maps into this relaxed model with cost no greater
than its objective. CP-SAT's **best objective bound**, rounded down, certifies
the optimized combination; its incumbent does not. The mandatory non-load yard
blocker bound is disjoint and may be added. The result takes the maximum of
this combined bound and the production counting bound. It never adds the
counting current-restow or hatch components a second time. Forecast loads and
joint `plan_calls` retain the production counting fallback. No future executable
restow policy is claimed: validity concerns the checker's stated fixed-slot
D15 objective.

## Verification

The final implementation passed **19 tests**, including ten comparisons against
`stowage.bounds.bruteforce.brute_force_optimum` with static rules enabled, for
both rule-freeze modes, inherited-soft E1 and widened-DG regressions. Cases
include unchanged hold cargo, current discharge blockers,
blocked covers, loading below arrival cover cargo, initially off covers,
current-versus-future tradeoffs, zero weights and fractional weights. The root
executive owns the combined test run and full-checker benchmark. Bounds
are never reported as plan passes.

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src nice -n 5 .venv/bin/python -m pytest -q \
-o cache_dir=experiments/mvp/frontier/lab/c3/c3-layover/.pytest_cache \
experiments/mvp/frontier/lab/c3/c3-layover/test_bound.py
```

The environment refuses `nice`'s priority change, then executes normally. All
numerical work uses one thread and one CP-SAT worker. No sealed data or external
paper was used; new implications are direct derivations of local
`moves/_physical.py`, `moves/_projection.py`, `bounds/components.py` and
`checker/rules.py` semantics. The inherited Cycle 2 mathematical references and
limitations are recorded in `c2/c2-hatch/bound_NOTES.md`.

## Frozen TRAIN cohort measurement

`bound_cohort.py` reads only this folder's `cohort/MANIFEST.json` and its
digest-verified inputs; output paths must remain under this folder. It refuses
an incomplete six-map cohort or a manifest that changes during measurement.
The full frozen cohort uses predeclared seeds 331101000 through 331101005.
Results are in `bound-results/summary.json`, six per-map rows and
`bound-results/TABLE.md`. Each row retains input/source digests and complete
certificate/witness diagnostics.

| Size | Occupancy target | Certified lower | Counting lower | Solve status | Seconds |
| --- | ---: | ---: | ---: | --- | ---: |
| tiny | 30% | 5 | 5 | optimal | 0.085 |
| tiny | 50% | 6 | 6 | optimal | 0.063 |
| small | 30% | 13 | 10 | unknown | 3.001 |
| small | 50% | 19 | 16 | unknown | 3.001 |
| medium | 30% | 11 | 11 | unknown | 4.336 |
| medium | 50% | 15 | 15 | unknown | 4.566 |

Total bound wall time was 15.052 seconds. Tiny/small receive three seconds,
medium five seconds, **including setup**. Small CP-SAT best objective bounds
improve counting by three lifts without finding an incumbent at this budget.
Medium model setup/presolve consumes the budget and retains counting bounds.
These `unknown` rows are certified bounds, never feasibility or optimality
claims. No finite witness components exist for those four rows; JSON uses null
and the table uses an em dash. A longer medium solve is left to later work.

Reproduction, with the same single-thread environment above:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src nice -n 5 .venv/bin/python \
experiments/mvp/frontier/lab/c3/c3-layover/bound_cohort.py
```
