# Fresh multiport TRAIN cohort

Selection is frozen in `cohort-spec.json` before the first generator call: tiny
and small, three/four/five distinct discharge ports, six maps in each cell;
three additional medium maps at four ports. Seeds are the fresh contiguous
range 263310000–263310038. Every declared discharge port must have at least one
load-list box. No solver outcomes select or replace seeds or maps.

`cohort.py` calls the existing `stowage.generate.random_yard.generate_random_instance`.
It does not copy or modify source files. A scoped wrapper supplies a synthetic
open rotation starting at THLCH and continuing through the first three, four or
five of SGSIN, MYPKG, VNSGN, HKHKG and THBKK. All ports already have synthetic
profiles. POD probabilities are uniform; payload weight multipliers are 1.0.
This is a synthetic rotation experiment, not a measured commercial service.

The generator API has no explicit multiport argument. The wrapper temporarily
changes its parameter loader, capacity helper, cargo draw and completion helper,
then restores each in `finally`. The cargo helper rejection-samples the original
cargo generator until all declared PODs occur; singleton DG replacement draws
use the unmodified generator. An extra final-candidate coverage condition handles
the possibility that replacement removes a POD. Original generator C1 conditions,
cargo rules, physical background acceptance and vessel/yard generation remain.
The wrapper requires the current default rule-freeze activation to remain off;
it does not change catalogue activation or rules.

Tiny uses the original six physical decision cells and six dry20 boxes, rather
than the historical tiny three/four-box workload, so five actual PODs can occur.
Small uses the original mixed equipment profile at a requested 50% geometric
TEU budget. Medium explicitly conditions equipment to dry20 at a requested 60%
geometric fill. Using the original capacity helper without the optional full-hull
regional booking argument bypasses D36's separate 90% regional demand policy;
this changes synthetic demand, not legal capacity. Tiny and small retain original
loaded/empty mix, DG draws and special cargo sampling. Medium retains those
dry20 distributions. All use feeder_500 and laem_chabang_like, two-cohort yard
blocking ratio 0.2, C1 on and optional C2 off.

Every output round-trips through the unchanged `Instance` validator and preserves
the standard schema. Per-map `.generation.json` files retain generator rejection
audits plus extra POD-coverage draw counts. `cohort-manifest.json` records input
SHA-256, path, size, port count, seed, actual box count, TEU/cell fill and POD counts.
`cohort.txt` lists only instance paths. A small experiment-local runner is needed
because shared `lab/score.py` intentionally rejects previously unapproved paths.
It must use the same full `stowage.checker.check`; generation is not a plan pass.

Generation runs with OMP/BLAS/VECLIB numerical thread counts set to one and only
one CPU-busy process for this team while BENCH-RUNNING exists. `nice -n 5` was
requested, but this sandbox's macOS setpriority call reports Operation not
permitted; the command still runs. No unrelated process is signalled.

Completed generation took 383.617 seconds summed over all maps. Independent
verification round-tripped all 39 instances, checked their hashes, exact seed
grid and cohort cell counts, absence of provenance taint, and actual POD coverage.
Tiny: 18 maps, six boxes in six cells. Small: 18 maps, 20 boxes in 68 TEU, realised
41.18–50.00% geometric fill. Medium: three maps, 144 dry20 boxes in 240 TEU,
exactly 60% fill. `cohort-verification.json` contains these results and source
hashes. No planner was run during cohort construction or verification.
