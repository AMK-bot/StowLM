# Fixed-stow yard relocation model

## Actual replay semantics

The relevant implementation is `src/stowage/actions/expander.py`, imported
unchanged, together with `src/stowage/moves/_physical.py`. Frontier scoring
expands Place actions with `Expander`; it does not call the separate
`yard/dig_out.py` nearest-stack policy.

Each Place retrieves its requested yard box. Boxes above it move top down to
`Expander.yard_target`: a legal clean stack first, otherwise the stack with the
fewest pending loads, then geometric preferences. The default stays in the
same yard block. A clean destination contains no still-pending load and thus
cannot cause a later vertical obstruction to a required export. Without a clean
destination, a relocated box can block again and need another lift. With rule
freeze active, full-height yard stacks along the crane lane can force additional
relocations even without vertical blockage. Therefore cycle cuts alone are not
a complete description of the real yard.

## Graph and exact relaxation

Vertices are required load boxes, with a rank variable for loading time. Ship
arcs retain every support-before-upper and hold-before-deck condition returned
by the unchanged `precedence_sets`. Every original yard above/below pair gives
an upper-before-lower arc. A Boolean relocation variable on the upper box may
disable its outgoing yard arcs, while all its ship arcs remain mandatory.
Every non-load box above at least one required export is counted once as an
unavoidable relocation.

CP-SAT minimizes relocation Booleans plus unavoidable non-load inventory. Strict
rank inequalities enforce acyclicity; all-different ranks are unnecessary.
Every original above/below pair is needed, since switching off adjacent arcs
must not erase another original obstruction. This implementation was written
here, not copied from an external solver. Ordinary deletion of a graph vertex
can erase a ship precedence that remains physically necessary after yard
relocation, so that deletion operation is not the operational decision.

For any monotone fixed-stow load order, every upper box loaded after a box
originally below it must first relocate. This proves the relaxation's lower
bound. Conversely, with unlimited private legal relocation buffers, retrieve
the computed order and give each relocated blocker its own buffer: each such
box moves once and never blocks again. The relaxation is exact for that
idealized buffer model. A legal real replay reaching its lower bound proves
the minimum for monotone fixed-stow orders under the actual yard rules.

Arbitrary ship staging/restows can replace yard relocations, so the conditional
arc result is not generally a global map bound when these are allowed. The
unavoidable non-load inventory count *is* a global yard-relocation bound:
each such box must leave its original position before the required export
below can leave, and it is not eligible to load onto the ship. When real replay
attains that count, global minimum yard relocations are proved regardless of
assignment or order. This alone does not prove the minimum total D11 objective.

## One real small graph

Input: `experiments/mvp/corpus/val/small/feeder_800/random_yard/seed-040100011.json`,
instance `random.small.40100011.0.8`. Assignment and original order come from
the authorized stored Frontier 0.9.0 validation evidence through
`lab/baseline.json`.

There are 18 load vertices, 45 ship arcs, **zero load-to-load yard arcs**, and
14 distinct unavoidable non-load blockers. For example, ship support requires
`QZXU0000138` at slot `0020202` before `QZXU0000056` at `0020204`.
Yard stack `B024-007-04`, bottom to top, is
`[QZXU0000056, QZWU0000047]`: non-load inventory `QZWU0000047` must relocate.
`QZXU0000138` alone occupies yard stack `B024-019-02`.

The exact relaxation is 14 relocations, solved in 0.0114 seconds. The actual
expanded/replayed graph witness has 14 relocations; the stored order also has
14. Thus this map already attains its global minimum yard relocations; there
are no load-to-load cycles to remove. This is an operational replay result,
not a fresh finished-plan/full-checker pass.

## One real medium graph

Input: `experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_500/f60-high/seed-192200000.json`,
instance `D192.medium-feeder_500-f60-high-s192200000`.
The fixed stow is extracted from the authorized V6 certificate
`experiments/mvp/scale/runs/v6/maps/medium-feeder_500-f60-high-s192200000/finished.json`.
This is V6 evidence, not a missing Frontier 0.9.0 baseline pass.

There are 88 load vertices, 351 ship arcs, **zero load-to-load yard arcs**, and
47 distinct unavoidable non-load blockers. For example, support requires
`QZXU0000821` at slot `0100102` before `QZXU0000098` at `0100104`.
Yard stack `B001-001-06`, bottom to top, is
`[QZXU0000502, QZWU0000290]`, so `QZWU0000290` must relocate.

The exact relaxation is 47 relocations, solved in 0.0156 seconds. The actual
expanded/replayed witness has 47 relocations, matching V6's stored count.
This again proves global minimum yard relocations, without proving minimum
total D11 or re-running the ballast finisher/full checker.

## Exact replay search and verification

`exact.py:search_replay` additionally explores real Expander states with a
wall-time and state cap. Branches are all ship-precedence-ready Place actions;
state identity retains the loaded subset, complete yard contents, length and
footprint indexes, covers and buffer. It prunes using forced current-yard pair
inversions and memoized equal states. Each complete candidate undergoes full
operational expansion/replay including final cover closure and future-port
projection. Exhaustion proves the best monotone order under the deterministic
default relocation policy; matching an independently valid lower bound proves
optimality immediately. The departure checker/ballast finisher is still the
scorecard's responsibility.

Five independent unit tests passed, including 35 random five-vertex graphs
checked against enumeration of every load permutation. Tests cover mandatory
non-load blockers, retaining original nonadjacent yard pairs and retaining
ship precedence after relocation. The real small-map search recognized the
stored order's lower-bound equality immediately without branching.

Assumptions: all assigned actions are the complete required export stow, and
the fixed-stow exact-order family loads each once without ship restows or
explicit planner-chosen relocations. No original source or sealed data was
modified or opened. More cohort rows are needed to quantify how often yard
relocation variation is entirely unavoidable non-load inventory.
