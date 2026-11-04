# Layout-search implementation

Read CONTRACT.md in full including Cycle 2 and lab/README.md; scout NOTES.md,
GRAPH-MODEL.md, method.py and pages.py; exact bound.py. The original mathematical
sources are Gilmore and Gomory (1961), https://doi.org/10.1287/opre.9.6.849,
and Roberti, Bartolini and Mingozzi (2015),
https://doi.org/10.1287/mnsc.2014.1947, as discussed in scout's notes. No new
literature claim or external downloaded implementation is used here.

Copied `lab/c1/fx-scout/pages.py` into this directory. The only page-model change
is `Model.port_of`: layout_deck='no_restow' permits either port on deck over a
late-port hold. Over an early hold it permits only early-port deck cargo. Scout's
True setting unnecessarily requires late deck cargo over late hold cargo. False
permits arbitrary deck and therefore incurs cross-cover restows. The new mode
retains the early-hold constraint while recovering capacity on late-hold covers.
Within-stack inversions are still possible and still priced by scout's page
model; the name refers to cross-cover restows only.

`method.py` imports the existing scout securing oracle; it calls the authorized
leader E9 mask fix and root's independent local actual-clearance repair. No rule
is modified. The inherited knapsack rounding and fitted securing weights remain
heuristic construction filters, so the page LP **is not a certified lower bound**
on unrestricted lifts or on map feasibility. An LP screen rejection means only
that the current priced pattern pool and settings did not cover the boxes.

Enumerate every two-port pure cover assignment when there are at most 16 covers.
Keep the scout greedy assignment first for a reproducible anchor. Rank the others
by optimistic TEU, length and mass capacity shortage, plus softer heavy-hold and
capacity-balance penalties. These numbers only order the experiments; they have
no lower-bound status. The first 64 layouts are retained. Over 16 covers search
single-cover neighbours instead. Try strict, the new asymmetric deck mode, then
soft/fully-free fallbacks so restrictive designs cannot consume every attempt.
A phase-1 page LP screens each design, and surviving designs enter the existing
phase-3 packing/dive. Every returned action tuple is replay legal. The independent
score runner and its full checker are the pass gate. Internal checks learn sight,
securing, E6 and trim settings. All tuning is generic by box count and observed
rule violations, with no map-id branch and no stored assignment in cold propose.

The stopping target is forced yard relocations plus four lifts per available
hold cover. This is a useful target for the pure-cover family, **not an
unrestricted lower bound**: an unrestricted plan can leave a cover unused. The
separate certified whole-map lower bound is owned by bound.py and NOTES.md.

Syntax parsed for method.py and pages.py before handoff. Three lightweight
`test_layout.py` tests pass (1.48 seconds): asymmetric deck port acceptance,
complete/unique binary enumeration with the scout anchor first, and deterministic
limited search. They do not run an optimizer or checker solve. Numeric experiments,
score measurements and final gate counts are recorded by root in NOTES.md.
