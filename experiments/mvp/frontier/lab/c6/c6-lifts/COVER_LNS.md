# Cold cover-changing large-neighborhood search

`cover_lns.py:polish(instance, scored, *, problem, model, solver, deadline,
seed, metadata)` accepts a concrete full-checker-passing stow produced in the
same process. The caller supplies a solver at that incumbent's actual
reference departure motion. It returns a strictly cheaper complete full pass,
or the exact incoming `Scored` object. Diagnostics are stored under
`metadata['cover_lns']`. No archived plan, assignment, recipe or ballast is read.

The graph has box vertices connected to legal stack-pattern vertices, and
port vertices connected to hatch-cover vertices. The exact restricted master
chooses patterns so every moving box occurs once and every physical stack is
used at most once. A port-cover edge pays one fixed opening charge even if
many boxes use it. Deck blockers pay for the union of stack-order and
hatch-opening causes, so one box is counted once per future call. The objective
also prices current hatch use. Native replay recomputes actual yard work,
current hatch operations and the D15 projection before any candidate is kept.

Each neighborhood frees **every box on both selected covers**, including deck
cargo. The remainder of the assignment stays exactly fixed. It permits new
occupied pages, changed stack heights, different box ordering, and a closed
hold cover. Source covers with little hold cargo are tried first; partner
covers favor common discharge ports and spare space. Both forced closure and
unforced port consolidation are tried.

The pattern pool contains the incumbent, relocated and split incumbent
stacks, port-focused greedy stacks and all their accepted prefixes, plus
single-box insertions/exchanges around current stacks. It is a heuristic pool,
not the whole stack family. Each pattern must pass the original model's hard
page legality and concrete securing rows at its actual box heights. Its
securing limit is at most the corresponding incumbent page's reference ratio
(or 1 if the incumbent ratio is below 1), with a small numerical guard. There
are no securing slack variables.

The master keeps native body-height cargo moments hard: no vertical-moment
increase, longitudinal movement within 0.10 m and transverse movement within
0.02 m, measured using total cargo mass. Conservative integer rounding and
an exact floating recheck enforce these rows. These reference-motion and
cargo-moment rows guide local physical safety; they do not certify native
righting-area, arrival conditions, cross-page DG/OOG rules or crane crossing.
The unchanged full checker remains the acceptance gate for all rules.

A cheaper replay keeps the concrete incumbent ballast. If and only if its full
check fails exclusively on trim/draught, `cover_ballast.repair` may search
ballast within the same absolute deadline. That callback independently checks
eligibility and accepts only a cheaper complete full pass. After any retained
candidate, securing motions and row caches are rebuilt from its concrete
plan, before another neighborhood can use them. Setup, solver or callback
exceptions return the incumbent. The initial incumbent is preserved in memory.

The implementation follows the destroy-and-repair principle of David
Pisinger and Stefan Røpke (2010), *Large Neighborhood Search*, in *Handbook of
Metaheuristics*, 399–420, [DTU publication record](https://orbit.dtu.dk/en/publications/large-neighborhood-search-2/),
[author manuscript](https://backend.orbit.dtu.dk/ws/files/5293785/Pisinger.pdf).
In plain words: temporarily remove a related group of decisions, solve that
smaller problem together, and retain a better result. Here a related group is
cargo sharing a cover, so the neighborhood can actually close a cover.

Read-only reuse: `c4/c2-layout/lns.py` supplied the fixed-charge pattern-master
and blocker-union formulation; `pages.py` supplied the native body-height
moment convention. The code is a separate reimplementation with actual-height
securing gates and current-run input, not an invocation of a warm reference
driver. Prior `polish.py` and `polish_wide.py` remain unchanged.

Seven focused tests passed: actual CP cover closure with changed occupied
positions, hard vertical-moment exclusion, physical-stack disjointness,
checker-rejection fallback, concrete-ballast preservation, trim/draught repair
integration, and rejection when motion refresh fails. A numeric instance smoke
is coordinated by the root; no standalone heavy solver run was launched here.

CP `OPTIMAL` and any objective bound apply only to this restricted pattern
pool and graph objective. They are **not** a certified whole-map bound or a
proof of fewest lifts among full-checker-passing plans under D15.
