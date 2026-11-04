# Arrival-aware page implementation

`page_base.py` is a local copy of `lab/c2/c2-layout/pages.py` (1 November
2026); four local hooks filter empty pages, distinguish fixed prefixes in
page types, and dispatch page-specific restow prices. `pages.py` subclasses
that snapshot. The authorized C1 E9 lookup wrapper is imported process-locally.
`method.py` adapts C2 layout enumeration and its LP/diving construction;
no code in the mutable engine package is imported by the method itself.
`alns.Problem` and `Sequencer` resolve to the runner's frozen Frontier copy.

Every existing compatible bottom-up stack segment becomes a fixed prefix.
A new column covers only load/quay boxes above it. Its slots begin after the
prefix and its mass-share and height capacities subtract the actual fixed
boxes. E1 heavy-below, E2 weight-class order, DG-count and stack-closing OOG
checks include the prefix; pricing also removes boxes that cannot legally
stand above it. Identical-page pricing cannot share across different prefixes.
The inherited knapsack rounds weights upward and remains a constructor,
not a certified lower bound. Prefix-inclusive securing weights/checks are
supported when an oracle is supplied; the current low-fill cohort method
uses the plain physical page family and full checking/finishing as its gate.

A pattern's heuristic price includes future stack crossings against prefix
PODs, future crossings against existing hold cargo under the cover, and
opening a cover already blocked by arrival deck cargo. Cover layout ranking
includes fixed hold/port edges and retained deck PODs. The per-page blocked
cover price can overcharge several pages using one cover; it only orders
construction and is never reported as a bound. Actual replay gives the score.

The original action prefix explicitly discharges every current-call target.
Any resulting retained quay-buffer boxes enter pricing through a temporary
Instance copy with an augmented load list. This working copy is never used
for final validation. The load-order graph contains fixed arrival placements
as support/cover dependencies. A fixed placement already aboard is skipped;
if a hold access has cleared it, its ordinary Place action restores it.

Optional branches buy a whole occupied stack prefix: RestowOff on its bottom
box clears its actual upward closure, including hatch blockers if necessary.
Those boxes become free pricing items in the working copy. Real original-
instance off/on moves charge their price; no checker rule or objective is
modified. The portfolio tries all prefixes first after the preserve-prefix reference,
then deck-only, per-cover and two single-prefix choices, deduplicated by root
set. Repeated roots already removed by a closure are skipped. The preserve
reference receives at most20% of construction time, and the all-prefix
fallback receives70% of remaining time, with separate layout deadlines.
A one-worker CP-SAT solve of the complete generated pattern pool precedes
diving; its incumbent is still only a candidate. The current constructor
uses pure pages conservatively; incompatible mixed-length arrival bases are
excluded, which says nothing about their actual feasibility.

Ten tests pass with one numerical thread: residual slots/mass/height,
prefix weight-class and heavy rules, future crossings, explicit discharge
and forced-buffer pricing, retained deck cargo off/on restoration after hold
loading, type separation, optional prefix's exact two now-restow lifts, and
a multi-prefix off/on restoration pass, and unchanged full-checker passes for
the fresh tiny30%TRAIN map. Tiny maps also enumerate a bounded one-direct-shift
choice before discharge: shifting the retained blocker to a free supported
slot can cost one lift instead of a forced off/on pair. The explicit tiny30%
shift witness passes the full checker at5lifts (one now-restow lift). The
original preserve/one-prefix pilot returned6lifts in approximately0.5seconds. The
legacy `data/fixtures/layover.json` yields no pure-page candidate; its specials
and mixed geometry remain a limitation, not a full-checker pass or proof.


Second construction pilot (`method-pilots-v2.json`): at20construction seconds,
small30% returned no candidate; all-prefix pricing covered the LP but dives
stranded6boxes. Small50% returned one149-lift replay-legal candidate, with
66now-restow lifts, failing unballasted full-check GM-second-check, draught
and trim; it was not sent to a finisher in this pilot. After adding restricted
pool integer selection, small30% still returned no candidate in a30-second
pilot (`method-pilot-integer.json`). These are diagnostic construction runs,
not full benchmark rows or full-checker passes. The final90-second/full-
finisher measurements are owned by the cohort runner.
