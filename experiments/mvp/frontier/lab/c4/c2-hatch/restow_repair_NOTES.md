# Bounded realization of current-restow witnesses

`restow_repair.py:repair` takes a final assignment, the abstract first-aboard
permutation, and its `ship_restowed` flags. It searches only a small operational
neighbourhood: at most three flagged boxes may initially use temporary unary-
eligible ship slots; between first loads they may move to their final slots.
Repeated `Place` actions express direct one-lift ship shifts. The expander can
also cause forced quay off/on operations, but every restowed box must be flagged.
No historical cargo assignment or ballast enters this search.

Each branch copies the production `Expander`, expands one action, and validates
every generated lift through production `apply_move`. First appearances obey
the supplied first-aboard permutation. A completed leaf must exactly match the
requested final assignment, then pass public full-plan replay and departure
static rules. It returns the actual `MovePlan`, including repeated cargo actions;
the root adapter must admit this full plan because ordinary scorecard proposals
allow each box only once. Cold ballast and independent full checker admission
remain external and mandatory. Replay/static success is not a full-check pass.

Temporary slots use the C3 safe unary mask, retaining DG widening. Departure
weight/heat/OOG rules are not imposed as intermediate operational rules; physical
move validation and final departure screening retain that source distinction.
The DFS has time, node, replayed-move, temporary-slot and extra-Place caps. A
failed or exhausted repair is **unknown**, never map infeasibility. Its result
contains no global lower bound. The separate bound's equality scope remains
**fewest lifts among plans the checker passes, counting later ports by our D15
projection (fixed stow, no later loads)**.

The synthetic test places a yard-top box temporarily on another deck column,
loads its future support box, then shifts it to the supported final slot. This
uses one ship shift and no yard relocation; direct load-once order needs one
yard relocation. Synthetic relocate/restow weights of two/one demonstrate a
strict weighted-objective improvement; default raw nonproductive counts tie
at one. Additional tests cover the load-once path and unknown limit outcomes.

Development read no validation outcomes. All **four tests passed** under root's
explicit sole CPU token after the medium run ended, with the paired comparator
acknowledged idle. The independent synthetic demonstration took **0.0218 s**,
four DFS nodes and eight replayed moves; the action sequence was upper box to
temporary `0030182`, lower box to final `0010182`, upper box shifted to final
`0010184`. It has exactly one current restow, zero yard relocations, and weighted
cost **one versus two** for load-once under the declared synthetic two/one
weights. The complete artifact is `results/restow-repair-synthetic.json`.
No ballast finishing/full check was attempted in this demonstration.

Repair source was frozen after these tests and before root's generic validation
realization experiment. No original constructor, fast constructor, proof module,
frozen adapter or interface was changed. The CPU token was explicitly released;
all processes started for these tests/demonstration exited normally.
