# Model implementation notes

Copied `c1/fx-scout/pages.py`; all per-pattern `legal` and `lash_ok` gates remain.
`Pattern` still contains a page and concrete box identities. No mixed-length patterns,
relaxed mass limits, or changed full-checker rules are introduced.

Pricing caches immutable item eligibility, integer weights and equal-weight tier groups.
It updates reachable NumPy slices and saves predecessor flags. If an all-high-cube
stack fits the height limit, high-cube count ceases to be a constrained resource and
its DP axis is eliminated; count remains an exact tie-break. Binding-height stacks
retain the full DP. This preserves the original integer knapsack's rounded weights,
item order, and per-height results, including deterministic ties tested against the
original. The original warning still applies: fitted securing weights and rounded-up
mass weights do not prove map infeasibility.

The master caches its sparse incidence matrix and extends it only for new columns.
Active-box/stack filtering uses a sparse product against an inactive-row indicator;
box rows remain equalities, stack rows remain upper bounds, and uncovered slacks
retain their original cost. A phase-1 objective of zero stops immediately, since
zero is the master objective's lower bound.

Before the cold all-slack master, deterministic and seeded first-fit attempts add
fully checked columns; failed attempts retain individually valid partial columns.
The first pricing round also prices four disjoint identity batches per subset.
Later rounds retain the original exact pricing exhaustion test. These changes target
the equal-dual symmetry without changing which patterns are admissible.

Exact identical-attribute aggregation was considered, but the first 428-box TRAIN
map has 345 exact classes: only 19.4% fewer box rows. Doing it safely also requires
mapping class patterns back to currently unused identities during diving, warm starts
and tail solves. It was deferred while retaining the existing identity semantics.

Explicit Wentges smoothing, box penalties and in-out separation were considered but
not enabled. The measured first 428-box baseline spent about 80% of root time in the
restricted master, and a stabilized dual path would require a raw-dual pricing fallback
before any exhaustion claim. This implementation instead removes repeated sparse
rebuilds and adds useful cold columns. No claim is made that the three stabilization
methods have been experimentally compared.

`verify_model.py` records deterministic all-type original/optimized pricing comparisons
for random and constant duals, phase 1 and phase 3, both height branches, and a synthetic
tier-varying securing oracle. It also compares same-pool master objective/uncovered
results on full/reduced active sets, banned columns, and soft LCG rows. Full replay and
full-checker measurements remain the benchmark and score runner's responsibility.

The first-fit complete incumbent is retained as `seed_chosen`. Diving immediately
returns it after rechecking column legality, lashing, banned columns, unique box/stack
use, and exact all-box coverage. This avoids discarding a complete solution while
fractional LP polishing consumes the remaining budget.

E8 initialization now calls the checker's own `special_violations` after the exact
`_geometry_index` / `_usable_ship` candidate preparation used by
`placement_violations`. Only that function emits the `away_from_heat` violations
being filtered; the expensive unrelated core and IMDG checks cannot affect those
violations. The shortcut is restricted to empty valid-geometry arrival states;
other states retain the original probe. Nothing in the checker is patched.

Saved verification (`results/verify-model.json`): **passed**. It compared **5,784**
exact pricing patterns and values in 16 cases, plus eight same-pool LP objective /
uncovered comparisons. Ten evenly spaced real heat-map probes matched complete E8
heat violation dictionaries exactly, including passing and failing placements.
The optimized full model initialized in **7.27 s** on TRAIN seed 185100210 with
2,110 eligible heat-box/slot pairs; the raw baseline had already exceeded 160 s.
This is a sampled checker equivalence test, not a full original static-table rerun.
Wrapper regressions: `test_method.py`, **3 passed**. The final frozen `pages.py`
SHA-256 is `bb3dff1d78afdba7afdf861bced3986febdd32d3f8145d2826664d3c3f657632`.
