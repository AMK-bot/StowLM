# Shared scorecard implementation notes

This is infrastructure for the other Frontier experiments. No solver sources,
checker rules, sealed inputs, or stored solver rows were modified.

Assumptions and decisions:

- "Every sixth/fifth" uses one-indexed positions: tiny lines 6, 12, ..., 48;
  small sorted IDs at positions 5, 10, ..., 50. The manifest records the rule.
- Sealed lists are themselves forbidden to open. Input safety therefore fails
  closed using explicit approved validation lists and TRAIN inputs recorded in
  the cohort/V6 metadata. This checks provenance without inspecting sealed data.
- Historical tiny/small passing rows do retain final plans. Preserve their
  ordered actions when replaying the stored reference, since the assignment
  alone does not determine yard relocation cost.
- Missing 0.9.0 medium evidence remains null, distinct from a measured failure.
  V6 certificates and earlier 0.8.2 rows are evidence annotations, not substitutes
  for the 0.9.0 comparator.
- Bootstrap intervals resample paired maps and estimate the absolute mean lift
  change (candidate minus baseline), separately for each size.
- Every custom finisher still goes through the full checker. The default V4
  finisher already checks, so the independent gate adds checking time.
- Reference commands requested `nice -n 5`; the sandbox returned
  `setpriority: Operation not permitted`. They continued at the permitted
  scheduling priority, with at most two active numerical workers and numerical
  thread environment variables fixed to one.

Reference measurements and verification are recorded in `REFERENCE.json` and
the `results/` subdirectories after the end-to-end runs complete.

The small sweep began while final runner plumbing was being completed. Subsequent
fixes covered finished-plan assignment extraction, unknown-input error reporting,
bytecode-cache suppression, and unknown-size summary errors; the stored proposal,
scoring, V4 finisher and checker gates were unchanged. Its row configuration hash
is the source digest at launch. The tiny sweep and full test suite use the frozen
final runner. Re-running small with the final source conservatively invalidates
its older cache keys; no stored keys were rewritten to disguise that difference.

Verification outcome: 25 tests passed. Stored reference reproduced all 8 tiny
and all 9 available small plans exactly, including assignments and lift counts;
the tenth small map has no historical plan. Paired mean lifts are 6.375 tiny and
13.8889 small, with zero change and degenerate [0, 0] bootstrap intervals. Mean
wall time over every selected map is 95.5312 s tiny and 167.9727 s small (186.5819 s
over the nine passing small maps). Medium is 0.6588 s/map on the no-candidate
path; this is not a medium solving measurement. No 0.9.0 medium row exists.
