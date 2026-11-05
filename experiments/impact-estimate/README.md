# Impact estimate

`impact_v6.py` calculates global port-call cost exposure, a separate improvement
pool before capture, a capture matrix, and a forward-pilot sensitivity. Its plain Python API is
`calculate(inputs=None) -> dict`; `default_inputs()` returns a complete JSON-able
bundle with value, unit, provenance status, source and limitation for each input.
The result is labelled `projection`; measured pipeline results remain empty.

From the repository root, using Python 3.12, one numerical thread, no GPU,
network or paid calls:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab uv run --offline --no-sync python experiments/impact-estimate/impact_v6.py --check-report experiments/impact-estimate/impact-v6.md
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab uv run --offline --no-sync python -m pytest -q -p no:cacheprovider experiments/impact-estimate
```

For custom scenarios, edit a full `default_inputs()` bundle and pass it to
`calculate`, or use `--inputs path.json --json output.json`. Preserve the
provenance and update assumptions. Do not overwrite the recorded fixture when
exploring alternatives. The fixture at `fixtures/impact-v6.json` is a recorded
calculator projection for offline replay; it is not a recorded port outcome or
an execution of the stowage pipeline.

`impact-v6.md` is the report draft to move into `docs/impact.md`. Every marked
displayed calculator value is checked by `check_report_numbers`; source facts
and claim limitations are audited in `anchor-sources-v6.md` and
`scale-sources-v6.md`. Relative repository links will need adjusting if the
leader moves the draft. The tests also require the fixture to match the API.

What is measured: no StowLM operational benefit. The public anchors are dated
port activity, research context, published retail charges, energy measurements
and commercial market estimates. Task 3.1 pipeline metrics are `{{name}}`
placeholders. Local planner attention and actual cost savings need a separate
prospective study.

`prize` contains alternative low/mid/high gross opportunity pools for extra
ship and yard lifts, reducible berth time and carrier planning labor. `capture`
applies 1%, 5% and 20% once to each pool. Prize is not calibrated from pilot-map
savings. Nonmonetized operational CO2 is separate; it is not lifecycle CO2e.
The original throughput-adoption `scale` layer remains available in the API
for comparison but is not added to the prize, capture or pilot tables.

Scale means more compute for the search, a larger model for reading.

`exposure` combines the verified rounded $63–76B commercial terminal-market range with full vessel time
at berth valued using the mid-case assumptions. Its roughly $90B headline is
the lower reference total rounded to the nearest $10B, not waste or savings.
The improvement pool is inside this exposure; never add the two. The adopted
logic review set ship prices to $30/75/150 and yard prices to $10/25/50. D376
replaced the unsourced 5/10/15% restow shares with avoidable shares of
0.3/0.9/2.2%: all restows 1.5/3/5.5% of moves (SLPA Colombo measured 1.35-2.9%
of TEU) times a vendor-claimed 20/30/40% avoidable share. Still assumptions;
no Jensen calibration.

What is weak: the yard research cases are transferred globally as
assumptions, public tariffs are charge proxies, and selected charter/wage/time
figures are not measured global averages. The eligible feeder-call denominator
is unknown. Adoption,
workflow attention, rework, wages, box mix and avoidable rehandles are explicit
assumptions. A tariff is not marginal operating cost. Larger-vessel performance
and commercial adoption are unproved. The annual gross equivalents exclude
implementation and compute costs, are not revenue or net savings, and combine
value accruing to different parties. Berth-time and emissions in the prize are
conditional on work actually disappearing and departure advancing. No energy
credit is assigned to unmodeled yard equipment. Read the report's single
"What must be true" block beside any quoted projection.

The original `impact.py`, `scenario.py`, `ship_time.py`, their results and tests
remain archival v5 sensitivities. The new calculator imports none of them and
does not reuse a selected small-map move reduction.

The named-port projection allocates the mid pool by exact call share: Laem Chabang 9,325 / about 520,000 world calls. It assumes the same world-average workload and inputs; eligible feeder calls remain unknown. The about-$40M headline rounds the unrounded $41.373M allocation to the nearest $5M, after applying the exact 1.793269% share. Do not calculate from the displayed 1.8% or add this subset to the world pool.
