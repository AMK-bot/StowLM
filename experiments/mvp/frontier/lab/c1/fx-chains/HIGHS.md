# Independent mixed-pattern MILP backend

`highs_patterns.py` imports the same `MixedPatterns` generator as the CP-SAT
variant, interleaves long and short sampling units, and samples a bounded pool
with 64 attempts per unit. A sparse set-packing MILP then uses one binary per
pattern. Group counts may not exceed cargo demand, and each physical stack may
belong to at most one chosen pattern. The objective maximizes total box
coverage. There is no rarity objective or warm start.

Legacy E6 uses the same sufficient occupancy restriction: a selected heavy
deck group requires at least one occupied cell in every static-compatible hold
opportunity. This can exclude a legal arrangement whose empty opportunities
are dynamically incompatible. Deck-group binaries express the conditional
rows, so the empty selection remains feasible before rejected-cover cuts.

The backend is `scipy.optimize.milp`, using HiGHS primal heuristics and branch
and bound. `threads=1` and the random seed are forwarded as native HiGHS options.
The installed SciPy `_milp.py` explicitly forwards options outside its small
documented list, emitting a RuntimeWarning; the warning is captured in each
attempt's diagnostics. No options or warnings are silently discarded.

On a time limit, an integral incumbent may still exist. The method checks its
integrality and sparse constraint rows after rounding, then records exact group
deficits. Partial selections are never returned, scored as stows, finished or
counted as passes. A full group cover must pass unchanged native mask replay,
carrier geometry, E6 and physical move replay before becoming a candidate.
Rejected full selections receive whole-pattern-set search exclusions. Any
coverage bound applies only to this sampled, conservative pattern model.

Entry: `highs_patterns.py:propose`, with the existing `method.py:finish` and
shared runner's independent full checker. Complete accepted witnesses and
source hashes are retained. No existing witness is used as fallback. Numerical
evaluation is left to the parent when a team CPU slot becomes available.
