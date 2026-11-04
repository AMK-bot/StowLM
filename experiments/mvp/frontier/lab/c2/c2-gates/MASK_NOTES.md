# Demand-driven exact mask and prefix reuse

The cargo graph has one vertex per assigned box, support edges from each lower
box to the next box above in either physical 20-foot column, and hatch-access
edges from every hold box to every deck box over its hatch. An edge says which
load must happen first. Long containers participate in both columns. A lift is
a crane action: yard blockers move before retrieval, hatch covers move to grant
hold access, and upward/deck dependency closures determine future restows.
The exact census is in `results/mask_graphs.json`: small map
`random.small.40100011.0.8` has 18 vertices/slots, 29 occupied physical
cells, 9 support edges and 36 hatch edges (45 unique). Medium
`D192.medium-feeder_500-f60-high-s192200000` has 88 vertices/slots, 143
physical cells, 51 support edges and 300 hatch edges (351 unique).

The source profile of `_mask_ok` shows an avoidable mismatch: it asks one
membership question per action, but the legacy engine materializes *every*
remaining-box/open-slot pair. The implementation here imports the existing
owner evaluator unchanged and requests its existing singleton box/slot domain.
It then performs exactly the owner's `_child` state update and completeness
query. This is an experimental wrapper in this folder, with no source edits.

An LRU of exact ordered action prefixes shares immutable engine snapshots.
After a swap the common prefix is unchanged; only the suffix is replayed.
Both successful and rejected prefixes are memoized. Each child retains the
owner's dirty-stack versions and C1 residual-flow repair. Prestate capacity,
DG, E6 and envelope caches from the singleton probe are transferred back to
the immutable prefix snapshot so those existing incremental owner mechanisms
remain available. Replaying the common prefix zero times is safe because its
box identities, slots, loading order and complete inherited state are identical.

The supported domain is a single immutable Problem lifetime, native
IncrementalEngine at its original root, legacy rules with freeze disabled,
no active frozen stubs, global screen disabled and no discharges, and exactly
one Place per load-list box. Initial onboard cargo is allowed. Root/config
changes, incomplete actions, duplicate boxes, other engine implementations,
discharges, global screening and freeze/stub activation use the original
`alns._mask_ok`. Unknown slots inside otherwise complete sequences are rejected
by the same engine index lookup. The work engine has a private cursor/history;
the caller's cursor and history remain at depth zero. Immutable memo dictionaries
may be shared by the owner's copy implementation. Instances and owner rule
implementations must not be mutated during an oracle's lifetime.

## Equivalence argument

For a fixed immutable state, `_evaluate` constructs each candidate's result
from the same static tables, open-slot membership, liftability, L1/L2 placement
rules, DG interactions, OOG envelopes, heat rules, C1 completion conditions and
last-placement E6 rule. Restricting the candidate axis to one pair changes no
prestate or rule inputs for that pair. Global C2 and discharges are excluded.
The E9 `all_boxes` flag selects all cargo *already aboard*, not all trial
candidates, so both original E9 branches still see the identical environment.
C1 shared flow/Hall summaries depend on the immutable prefix, while candidate
post-state checks are indexed per candidate. In particular, global Hall, DG,
OOG and E6 checks still execute: merely checking the two moved stacks would
not be sound. `_child` is precisely the transition called by `push`, without
its redundant second membership query. Induction over the suffix therefore
preserves every action decision and the final completeness predicate.

This argument applies to the current owner code and supported configuration;
its empirical differential evidence is `results/mask_differential.json`.
Those are mask-legal counts, never full-checker passes. All candidates still
require real scoring/replay and full finished-plan checking.

## Dynamic topology assessment

David J. Pearce and Paul H. J. Kelly (2006), *A Dynamic Topological Sort
Algorithm for Directed Acyclic Graphs*,
https://www.doc.ic.ac.uk/~phjk/Publications/DynamicTopoSortAlg-JEA-07.pdf,
maintain an order after graph edge insertion/deletion. It motivates updating
only changed dependencies, but no downloaded code was used. A same-length box
swap relabels vertices in the fixed **slot** support/hatch graph; it changes no
slot edge. Cycle 1's `CachedSequencer` already caches this graph by sorted
occupied slots. Its yard-priority tie-break must still execute: reusing an
arbitrary valid topological ordering would change yard relocations and search
behavior. A Pearce–Kelly implementation was therefore not added to this mask
wrapper. Relocate-top graph changes could use one in future if sequencing,
rather than mask evaluation, becomes the measured bottleneck.

## Reproduction and limitations

Use the command in `test_mask.py` with PYTHONPATH containing src, Frontier,
and lab, PYTHONDONTWRITEBYTECODE=1 and all numerical thread limits set to 1.
The script calls the required Cycle 2 E9 fix in-process before building either
arm. Cases are official `quick_small` stored-pass starts and the first two
`quick_medium` TRAIN stored raw plans; random swaps, column swaps, relocations,
reverse loading orders, bad cells, collisions and unknown slots exercise both
accepted and rejected sequences. Prefix caching is cold per map. Native and
fast calls are interleaved; the first native call per map is profiled, so that
microbenchmark includes profiling overhead on that call. Cold independent-engine
benchmarks are separate in `benchmark_mask.py`. `nice -n 5` was requested but
this sandbox reports `setpriority: Operation not permitted`; jobs still run.
Only one numerical-thread, CPU-busy process belongs to this mask subtask.
No sealed input was opened. No finisher, checker or acceptance rule is changed.
The root's paired fixed-work search and `lab/score.py` results determine the
3× keep decision; a faster membership microbenchmark alone is insufficient.

## Recorded differential result

`results/mask_differential.json` records **2,220 differential comparisons,
zero disagreement**: 1,980 on nine official `quick_small` stored-pass starts,
and 120 each on the 88- and 162-load-box official TRAIN medium starts.
There are 1,086 mask-legal small sequences and 63/61 mask-legal medium
sequences among these attempted comparisons. These counts include repeated
sequences and deliberate invalid action sequences. They are not independent
plan or full-checker-pass counts. Incomplete/duplicate-action fallback checks
are additional and excluded from the 2,220 total; every per-case cursor-depth
assertion passed. Activating freeze correctly disabled the supported domain.

| Differential microbenchmark | Native total s | Fast total s | Ratio |
| --- | ---: | ---: | ---: |
| Nine small maps, 1,980 attempts | 157.816 | 51.587 | 3.06× |
| Medium 88, 120 attempts | 31.874 | 18.013 | 1.77× |
| Medium 162, 120 attempts | 100.550 | 5.508 | 18.25× |

Per-small-map ratios vary from 0.835× to 39.12×. This is explicitly **not**
an end-to-end search result: repeated prefixes, no-op neighborhood proposals,
and invalid slots are included; normal search filters duplicate assignments.
Calls are interleaved fast-first and share source-owned static/exact caches,
so expensive cold work can be charged to fast and reused by native. The first
native call on each map is profiled. Only the root's independent fixed-work
search comparison should be used to decide whether search reaches 3×.

The eleven-call native profile spends 2.985/3.169 s (94.2%) in `_evaluate`,
with 1.291 s cumulative in C1, 0.848 s in hatch-restow subprobes and 0.491 s
in final E6 checks; nested entries overlap. The profile supports reducing the
candidate domain, while confirming that support/topology alone cannot replace
the mask's global completion and special-cargo checks.

The validated `mask.py` SHA-256 is
`a42b1a930ee5c9f4718c29a7957eb5068151d91a4d36f3cfeae16429d1f5d415`.

## Design-only follow-up after paired search

The first search rows show that residual accelerated mask work still dominates
on some maps: `random.small.41100002.0.2` evaluates 1,890 singleton pairs in 156
mask calls and reuses 691 prefix actions. A profile of the **accelerated** path,
not the original-mask profile, is required before choosing another optimization.

Two exact possibilities were inspected, with neither enabled or benchmarked:

* Restrict liftability to the requested box by calling the owner `_liftable`
  on a local snapshot whose `rem` is the original bitmap intersected with a
  one-box bitmap, then discard that snapshot. The original remaining-box bitmap
  must still feed C1 and every completion rule. Inspection shows `_liftable`
  reads `rem` only to initialize its result and iterate queried boxes; each
  retrieval test reads the unchanged full yard/expander. This restriction can
  preserve queried-box legality, but liftability is only roughly 6% in the
  recorded native profile, so it is unlikely to recover the missing speed alone.
* After several distinct next-action queries from one exact immutable prefix,
  materialize and cache its full owner mask. Future queries intersect that full
  sorted index array with the requested pair. Returning bool(full_mask) would
  be unsound: another pair can be legal while this pair is illegal. Such a cache
  must be keyed by the immutable state, retain the no-discharge/global-off
  guards, and keep hatch-clearing subprefixes in their separate state caches.
  It could amortize fixed C1 overhead but might evaluate thousands of irrelevant
  alternatives. Prefix hits alone do not measure branching fanout or justify a
  promotion threshold. The current owner's C1/E6 prefix caches already reuse
  shared state, further reducing the potential incremental gain.

No hybrid production code or rule change was retained. The next justified step
is a small accelerated-path profile on the slow 22-box map, then an isolated
new differential experiment only if that profile identifies a removable cost.
