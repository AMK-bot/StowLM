# Loaded whole-vessel exports (random-yard 1.4.0; adapters 2.4.0)

Full is the entire vessel, with no omitted-region cargo background. Its former
half-fill default could therefore leave a near-ballast departure. Tiny, small
and medium keep their D29 sizing, background, initial tanks and split keys.

The full policy targets 85% of actual TEU cells, capped by the per-length slot
budgets and 90% of the cargo mass budget. That budget is design displacement
minus lightship, fixed stores, live consumables and planned ballast. Source:
task 2.2's fleet parameters and C3 condition metadata, and task 2.5's configured
equipment/POD/empty/triangular-mass distributions. Analytic expectations size
the list without changing any weight distribution or DG prevalence. Actual
candidates must fit 75–90% TEU, 75–90% cargo mass budget and 90–100% design
displacement, plus existing physical screens and depth-0 C1. C2 remains off.

APPROXIMATION: initial export ballast targets 93% design displacement, bounded
by actual adjustable-tank capacity with at least 2% of that capacity reserved.
All adjustable tanks receive a common mass fill fraction, preserving source
symmetry. This is a scenario initializer: trim, free-surface effects, strength,
stability and the weather criterion still require the finisher/full checker.
The tank masses used in screening are the masses saved in the Instance. C3's
no-ballast `achieved_design_band` metadata remains truthful and separate from
the new `full_export` audit, which includes booking-dependent ballast.

Seed 1, blocking ratio 0.3, actual full-physics models and live tanks:

| Ship | Profile | Boxes | TEU fill | Cargo/budget | Departure / design (t) | Design fill |
|---|---|---:|---:|---:|---:|---:|
| feeder_500 | mixed | 189 | 83.94% | 81.71% | 9,046.2 / 9,727.1 | 93.00% |
| feeder_500 | dense dry20 | 316 | 84.95% | 87.78% | 9,046.2 / 9,727.1 | 93.00% |
| feeder_1000 | mixed | 300 | 83.40% | 79.20% | 14,953.7 / 16,211.3 | 92.24% |
| feeder_1000 | dense dry20 | 503 | 84.97% | 87.20% | 15,076.5 / 16,211.3 | 93.00% |
| feeder_1700 | mixed | 815 | 83.51% | 86.91% | 26,026.1 / 27,985.1 | 93.00% |
| feeder_1700 | dense dry20 | 1,057 | 65.90% | 89.26% | 26,026.1 / 27,985.1 | 93.00% |

The dense feeder_1700 is explicitly `mass_limited`: 75% TEU would require
1,203 dry20 boxes, about 18.76 million kg at the unchanged 15,595 kg mean,
exceeding the available 18.46 million kg even before reserving ballast. Its
TEU lower bound is consequently waived; its mass and displacement bounds are
retained. Meeting both requested lower bounds requires a changed cargo mix or
ship design; this implementation changes neither.

APPROXIMATION: existing sequential DG shipment conditioning now also handles
adapter IMDG screens that reject a declaration before C1 is reached. Only
individually rejected shipments are redrawn through task 2.5. Every complete
candidate must then pass all screens and C1 again; declarations are never
edited. This affects the accepted population and is counted by shipment_redraws.
Synthetic full-call yard storage scales with demand and preserves source block
and equipment restrictions. It is not a claim about a real terminal footprint.

Specification change: full's ceiling increases from 260 to 2,000 and its TEU /
per-length upper fill from 50% to 90%; default actual counts change to the table.
Explicit small diagnostic lists retain their previous initialization and are
not advertised as loaded export departures. Explicit counts exceeding the new
capacity/mass budget raise an error rather than silently shrinking. Family key
names/values used by 5.3's canonical identity remain unchanged, including all
existing profile labels; output versions and n_boxes are not split-key fields.
Existing full shards require regeneration and must not be mixed with old output.
The 4.3 campaign specification owner must also update `instances_v2.py`: its
hard-coded realistic defaults of 400/800 boxes exceed the corrected 500/1000
ship budgets. Use capacity-derived quantities (316/503 for these dense
profiles), and rebuild manifests/shards. This separate campaign-spec file was
left to its owner; the generator rejects those invalid explicit old demands.
The pre-existing dense_dry20_dg adapter label is still unsupported by 5.3's
strict InstanceSpec; no split policy was changed here.

Reproduce: `UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
uv run python experiments/instances/verify_full_export.py`. Six runs took
38.42 s total, one CPU thread, no GPU, with cached fleet physics. Per-case
times and candidate rejection counts are in [full-export.json](full-export.json).
The tiny-run baseline was 1.33 s; the bounded matrix estimate was under a few
minutes and under 4 GB. One earlier dense diagnostic was interrupted through its
own session when whole-list unsupported-DG rejection repeated. No heavy job or
paid compute was launched. Sandbox restrictions prevented ps/nice diagnostics.

These are generated instances, not checker-approved loading plans. Designing
the initial displacement into a band is not evidence of weather-rule acceptance.

Verification: `UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1
OMP_NUM_THREADS=1 uv run pytest -q tests/generate -o addopts=--import-mode=importlib`
returned **220 passed in 51.52 s**. This includes all 12 vessel/rung split-key
regressions, actual-tank dense export auditing, bounded-ballast/mass-limited
arithmetic and early-IMDG conditioning. The 65 adapter/integration tests also
passed independently in 6.04 s. No full-checker-approved departure is claimed.
