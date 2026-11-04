The Cycle 2 map-wide bound is copied from `lab/c2/c2-hatch/bound.py` into
`hatch_restow.py`; `map_bound(..., restows=False)` retains the 0.10 hatch-only
route. The restow closure counts each active blocker once per projected port.
The old route preserves its inactive-DG counting-only guard. The new route
uses `unary.optimistic_dg_tables`: every legacy DG row is widened to all physical
slots with its one/two-cell footprint. The new static mask is independent and
never changes native search tables. This drops every DG unary restriction
(including cell acceptance flags); it can admit extra impossible placements,
but includes every legal supported DG placement. Physical slot enumeration,
mass shares, heights and support constraints remain unchanged and necessary. Only strict
bound equality to a fresh full-checker-passed weighted lift count proves an
optimum; neither the relaxed witness nor restricted repair objective proves it.

`repair.py` copies the Cycle 2 witness-biased greedy repair and restricted CP
candidate generator. Its dependency `chain_model.py` is copied from
`lab/c1/fx-chains/chain_model.py`. The engine imports those local copies, never
mutable laboratory code. The copied fixture in `fixtures/` is only a regression
input and never supplies production starts. The CLI and saved-incumbent loading
from the experiment were removed. Partial witnesses now return no candidates
instead of raising while indexing an absent box.
