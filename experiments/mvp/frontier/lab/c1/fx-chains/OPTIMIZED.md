# Maximum-coverage pattern search

The original pattern master demands every cargo group exactly before it can
report any solution. When its slice ends with UNKNOWN, it supplies neither a
stow nor information about which cargo is difficult. This variant permits
each group to be undercovered and maximizes the number of covered boxes. One
extra box beats every secondary objective gain; among equal counts it favors
groups with fewer compatible pattern positions. Physical stack resource rows
still forbid overlapping patterns.

For legacy E6, selecting a heavy group on deck also requires occupancy of at
least one cell in every static-compatible hold opportunity for that group.
This is a sufficient restriction: it can exclude a legal stow whose empty hold
opportunities are incompatible for dynamic reasons such as weight or height.
The empty selection still satisfies these conditional rows.

The empty selection is an initial feasible hint. Each larger sampled pattern
pool retains the preceding best selection as a hint and a lower bound on
coverage. CP preprocessing is bounded to one presolve iteration with probing
and symmetry detection disabled, so a short slice can reach its supplied
incumbent. Single- and double-support units are interleaved before generation
to avoid spending the entire sampling window on twenties.

A partial incumbent is only diagnostic. It is never returned as a stow, passed
to `Problem.score`, finished, or counted as a pass. Diagnostics retain its
coverage count and the deficit of each cargo group. Only exact coverage enters
the unchanged `chain_model.validate` native mask, carrier geometry, E6 and move
replay gates. Every accepted candidate must still enter the shared finisher and
full checker. Rejected complete pattern selections receive search exclusions;
these are not map infeasibility certificates.

This model inherits `pattern_model.Patterns` restrictions: sampled patterns,
homogeneous length columns, fixed long support pairs and group maximum masses.
No frozen helper source is edited. The implementation changes search scheduling
and the master objective; it does not add a published-algorithm performance claim.

Entry point: `optimized_patterns.py:propose`; use `method.py:finish` for the same
V4 finisher and the shared runner's independent full checker. Source hashes of
all three imported model files accompany results. The implementation sub-agent
ran syntax compilation only; numerical experiments are delegated to the parent
because both team CPU slots were occupied.
