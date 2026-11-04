# Optional exact-moment balancing; separate method version

`pages_balanced.py` extends frozen `pages.Model` without changing its pricing
geometry types or coefficients. One pricing type must have identical signed
TCG and vertical/longitudinal geometry; this correctly prevents inherited
shape-only `balance()` from using mirrors. The separate operator restores legal
physical moves while evaluating their real column moments individually.

It considers whole-column swaps between occupied compatible pages and moves to
an unused compatible page. A move is evaluated by its reduction in absolute
cargo transverse moment relative to the configured TCG interval's centre (zero
if no interval). Concrete box identities and column order stay unchanged.
Every candidate passes the existing `legal` and `lash_ok` gates, capacity is
checked before geometry indexing, physical stack overlap is excluded, banned
patterns are not reintroduced, and chosen columns enter through `model.add`.
Fixed outside columns and all their physical stacks are protected.

Vertical and fore-aft changes stay within configured target limits or the
incumbent's existing violation, plus explicit small search allowances: default
cargo vertical CG +0.01 m and LCG ±0.03 m. With no configured target this bounds
the change relative to the incumbent. These are search preferences, never
necessary physical inequalities or checker acceptance rules. Native replay,
all voyage phases, securing and intact-area checks remain mandatory.

Work is bounded by at most 1,200 examined operations, three accepted moves and
1.5 seconds by default; the caller's requested wall limit is capped at two
seconds. Metadata reports examined/accepted counts and elapsed time. Pricing
geometry remains unchanged. Import this wrapper only from a new method arm;
do not replace the frozen cohort's `pages.py` or source hashes.

Integration: `BaseModel.dive()` maintains `fixed_columns` as a live list while
fixing columns. That list remains the final selected stow after a successful
dive. Before a **whole-map post-dive** balance, clear `model.fixed_columns=[]`;
otherwise preserving it correctly freezes every column. During a restricted
LNS, retain the explicitly supplied outside fixed columns.

Nine synthetic tests pass in `test_balance.py`, covering mirror swaps,
fixed outside pages, lashing refusal, fore-aft guards, an empty-page move,
banned patterns, a zero operation budget, too-short destinations, and incomplete
coverage. Balance and sight tests ran together after the benchmark marker
cleared and the parent granted the second CPU slot: **17 passed**, exit 0,
0.95 seconds, one numerical thread, scoped vessel cache and pytest cache disabled.
No numerical process remains. New stows still require replay/full checking.

## Visibility diagnosis from recent saved reports

JSON-only inspection during the ongoing A cohort found large sight violations
even for capped `(3,5,5)` profiles. Numbers below are the observed departure
blind distance versus the checker limit; no new physics evaluations were made.
The cohort was still running, so this is an interim selection of existing
reports, not a final pass count. Report files use shared per-map/trial names and
can be replaced by later arms; use the parent arm's recorded row/provenance for
final accounting.

| TRAIN map suffix | Lifts | Caps | Blind distance / limit (m) | Minimum clear sector (rad) |
| --- | ---: | --- | ---: | ---: |
| 500 f60-high 192200000 | 129 | 3,5,5 | 370.8 / 232.8 | 0.1292 |
| 500 f60-low 192200000 | 99 | 3,5,5 | 508.6 / 232.8 | 0.0325 |
| 500 f60-low 192200000 | 111 | 3,5,5 | 341.3 / 232.8 | 0.0863 |
| 500 f70-low 192200001 | 133 | 3,5,5 | 419.6 / 232.8 | 0.0322 |
| 500 f70-low 192200001 | 131 | 3,5,5 | 399.0 / 232.8 | 0.0010 |
| 500 f70-low 192200002 | 137 | 3,5,5 | 331.2 / 232.8 | 0.0010 |
| 800 f60-high 192200100 | 181 | 3,5,5 | 610.6 / 268.88 | 0.0579 |
| 800 f60-high 192200101 | 186 | 3,5,5 | 771.5 / 268.88 | 0.0191 |
| 800 f60-high 192200102 | 200 | 3,5,5 | 528.7 / 268.88 | 0.0356 |
| 800 f60-low 192200100 | 145 | 3,5,5 | 507.2 / 268.88 | 0.0355 |
| 800 f60-low 192200101 | 124 | 3,5,5 | 682.5 / 268.88 | 0.0346 |

The minimum clear angle requirement is 0.08726646 rad. Some plans fail only
blind distance among the visibility tests; others fail angular gaps as well.
Their arrival blind distances stay far above their limits: for example
331.2→304.1 m on 500/f70-low002 and 528.7→496.1 m on 800/f60-high102. They are
not slight list errors that a lateral balancing swap can reliably fix.

Legal transverse swaps may improve list or move a narrow obstruction out of a
sight sector. Whole-column moves that lower physical forward roofs may improve
blind distance, but a generic transverse objective does not guarantee that.
A positional **physical roof-height** limit is the next useful constructor
constraint; a tier count alone ignores differing base heights, high cubes,
OOG and ship-eye geometry. Forward-bay cuts should use the existing visibility
model's geometric screen, with full native rechecking. Such construction cuts
can be deliberately conservative but must not be advertised as necessary
whole-map bound inequalities without a separate proof. The righting-area
constraint remains `containership_area_0_flood_mrad`; balancing TCG does not
replace its phase-specific check.
