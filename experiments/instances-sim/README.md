# Historical simulated-yard Stage A instances — generator 1.0.0

This directory preserves **historical generator 1.0.0 evidence**. Its 125 shards,
audit files, manifests, benchmarks, screening rates and verification results
below describe that version only. They do not establish current generator or
physics behavior. Current code correctly refuses to resume these incompatible
shards; do not run the old starter command against this directory. New fix-wave
evidence belongs under `fix-wave/`, without overwriting this archive.

125 clean, synthetic planning inputs are retained in seed shards:
50 tiny, 50 small, 20 medium, 5 full. Each has its matching `.audit.json`; root
and rung `manifest.json` files give counts, checksums and screening rates.
These are inputs, not checker-certified loading plans.

Historical commands (require the original generator 1.0.0 and its pinned
dependencies; **do not run against this archive with current code**):

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python -m stowage.generate.instances --starter --without-dg
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/instances-sim/probe_screening.py
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run pytest -q
```

The CLI also supports `--benchmark` (five instances per rung), `--size`,
`--count`, `--seed`, `--noise`, `--vessel`, `--port`, `--output` and
`--max-attempts`. Default generation keeps the full feeder-export cargo profile.
This starter explicitly requests a **non-DG service**: all current fleet spaces
lack recorded compliance classes and checker 2.4 fails closed for their DG
placements. The alternative service is pinned in `family.cargo_profile` and the
audit configuration; resuming with a different profile is refused. It changes
synthetic demand, never any physical limit or DG permission. The original full
mix can exhaust the retry budget on medium/full, as expected from these missing
fleet data. Synthetic recorded DoC classes belong to the fleet owner.

| Rung | Target boxes per instance | Shards | Candidates | Screened out | Rate | Reasons (candidate counts) |
|---|---:|---:|---:|---:|---:|---|
| tiny | 6 | 50 | 50 | 0 | 0% | none |
| small | 36 | 50 | 50 | 0 | 0% | none |
| medium | 150 | 20 | 30 | 10 | 33.33% | physical capacity 9; on-deck positions 1 |
| full | 280 | 5 | 5 | 0 | 0% | none |

Medium reason rates: `expert.physical.slot_occupancy` 30%, `expert.e8.on_deck`
3.33%. All other reasons, including provision rejections, were zero in this
explicit non-DG starter. Reasons can overlap; their denominator is candidate
load lists. `screening-probe.json` separately audits five **raw full-mix** draws
per rung (including unconditioned tiny demand). Rejection rates were 100%, 80%,
100%, 100% respectively; IMDG reasons occurred in 60%, 80%, 100%, 100%.
Unsupported-provision candidate rates were 0%, 0%, 40%, 40%. The probe records
UN frequencies and all other reasons; five samples do not estimate population
rates reliably. UN0336 and UN2915 have explicit regression coverage even though
these particular five-draw samples did not select them.

`benchmark.json` preserves the required five-per-rung pilot: cached mean shard
times were approximately .04 s tiny, .07 s small, .95 s medium and 2.31 s full.
The initial cold real-hull build took 56.74 s, separately recorded in its audit.
Each process caches full vessels; slices retain whole-hull physics. `compute.md`
records admission and the sandbox's refusal of priority changes / process-list
access. Every worker is CPU-only with BLAS/OpenMP limited to one thread; the
starter used one process. No cloud jobs or paid calls were made.

## Historical 1.0.0 semantics and approximations

A necessary condition is an impossibility test, not a feasibility proof. The
screen counts physical cells, per-length candidate positions, mass/stack bounds,
reefer plugs, special stowage and top capacity, OOG envelope conflicts, DG
classes/categories/provisions, and a conservative displacement/draught bound.
It relaxes joint packing and leaves equilibrium, strength, segregation and full
plan certification to the solvers/checker. OOG pair work is bounded and may defer
checks; missing physical hull data rejects production draws. Every rejection
uses a catalogue rule ID. Passing a missing shipment-specific SW condition is
not shipment approval; checker verification remains mandatory.

APPROXIMATION: two overlapping export calls start from an empty yard. Their gate
windows use the port parameter bins; later-call background consists of half as
many empty dry20 boxes. Both calls' gates interleave; the target starts at time
zero and the later ship has not yet arrived. Placement uses simulator clustering
and retrieval-order preferences with explicit noise. The simulator validates
all gate lifts via the move engine and captures the yard before target loading.
The retrieval hook then defers loading to the eventual planner; no greedy target
loading plan is generated. All targets must be present or the candidate is
rejected. Missing background targets may queue externally and are not silently
claimed present. This is an export-window scenario, not calibrated steady-state
traffic, import flow, or crane-throughput measurement.

APPROXIMATION: tiny uses the exact random-yard six empty dry20 decision cells
(four hold, two deck); other rungs reuse `random_yard.SIZE_LADDER` and fleet slice
presets. Whole-hull and synthetic slice background loads remain intact.
APPROXIMATION: fixed consumables are half full; adjustable ballast is empty for
the ballast finisher. Explicit `--without-dg` changes only demand prevalence.
APPROXIMATION: the hull screen uses an inflated admissible draught and upward
hydrostatic-table mass bound; it is not a replacement for equilibrium.

Family tags include vessel, port archetype, noise, size rung, seed, generator
version, and cargo profile. Routes are sampled by configured service shares and
contain four Thai-context regional calls (within the requested 3–5 range).
Provenance pins generator, cargo and simulator versions, and clean `save_instance`
is used. Timings live only in sidecars so instance bytes stay deterministic.
Resume validates configuration, provenance cleanliness and SHA-256 checksums;
a missing audit triggers deterministic regeneration, and mismatched artifacts
are refused. Shards checkpoint independently; manifests refresh after each one.

Sources: `params/cargo/README.md`, `params/ports/README.md`, simulator module
docstring; Wolsey (1998), *Integer Programming*, chapter 1, for necessary-condition
relaxations; Biran & López-Pulido (2014), *Ship Hydrostatics and Stability*,
chapter 2, for displacement/draught interpretation.

Verification: all 42 scoped generator tests pass. Two full `uv run pytest -q` runs
failed outside this task: first 7 failures; required repeat 4 failures in 415.13 s
(frozen linear.build_model source inspection and three vessel hull tests). Full
suite acceptance remains open. Details: HANDOFF.md and full-suite-repeat.log.
