# Coverage optimization with mixed deck patterns

`optimized_mixed.py` is a copy of this experiment's `optimized_patterns.py`.
It retains the coverage objective, rarity tie break, partial incumbent hints,
search budgets, sufficient E6 blocking and native acceptance gates. The only
algorithmic substitution is `mixed_patterns.MixedPatterns` for the homogeneous
pattern generator. Metadata and witness filenames identify this variant.

A mixed deck pattern places independently chosen twenties in both support
columns, with equal height on each paired base level, then places long boxes
above them. Hold patterns remain homogeneous. Each pattern reserves its two
whole physical stacks; actual slot footprints determine E6 occupancy, and its
group counts count boxes rather than physical tiers. This distinction matters:
two twenties on one paired base tier are two boxes but one occupied tier per
support. The optimized master does not use a pattern's box count as stack fill.

Both optimized entry points now record their own PID, parent PID and current
instance under `results/processes`. A `stop-<PID>` marker requests cooperative
exit at the start of the next map, before constructing cargo. This addition
does not change the original variant's solver algorithm or candidate claims.

Syntax compilation passed for both optimized entry files. No numerical solver
run was launched by the implementation sub-agent. The parent runs this variant
through `optimized_mixed.py:propose` and `method.py:finish`; the shared full
checker remains mandatory. Existing stows are not imported as fallback or
counted as new construction.
