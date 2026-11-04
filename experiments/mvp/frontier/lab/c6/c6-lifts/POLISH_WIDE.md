# Optional broader rank neighborhoods

`polish_wide.py` copies `polish.py` into a separate module, so it can be
integrated after an exploratory narrow run without mutating the running code.
It keeps the same current-run-only input, concrete ballast, hard securing,
capacity and moment rows, native replay, full-check acceptance and exact
incumbent retention. Its model and citations are in `POLISH.md`.

The original neighborhood preserved each position's E2 rank category. This
broader neighborhood lets rank categories exchange positions: only length,
height, heavy status and stack-closer status remain common to a group. Because
an upper heavy category can now move, the CP model directly requires each
position's assigned rank to be at most every physical predecessor's assigned
rank plus the fixed E2 tolerance. It includes transitive predecessors, rather
than checking adjacent positions alone: positive tolerance on every adjacency
could otherwise accumulate and violate E2's whole-stack minimum rule.

Each group has at most 64 boxes, interleaved by discharge port. Its Hamming
radius remains 32 changed box-slot assignments. The exact topology, height
categories and heavy statuses still stay fixed. A full check is still needed:
securing reference motions and cargo-moment proxies cannot certify native
stability or the final voyage conditions.

This is an optional wider local-branching family in the sense of Fischetti and
Lodi (2003), not a global optimality algorithm. It can be called after the
original `polish`, using that function's passing result as the incumbent.
Metadata must be saved separately by the caller if both are called; each
module writes its stage diagnostics to `metadata['polish']`.
