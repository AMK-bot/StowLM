# Scout changes for 0.13.0

The empty-arrival medium route retains its complete replay candidates and native
passes before optional later work. `candidates.py` publishes the concrete `Scored`
object (including its ballast recipe) through `context['publish_candidate']` and
keeps caller-owned backup lists. Both the legacy layout route and the public
delegate recover those backups after an optional exception. Caller early-stop
signals still propagate. Every candidate is complete and replay legal before
publication; `checked=True` follows the unchanged checker/verdict gate. A root
regression injects a later model exception after a checked first candidate.

`capacity.py` replaces the box-count feasibility restriction with a physical
cell-demand floor. The preferred cap is four tiers for every size. A separate
three-tier pure ladder is added whenever actual uncapped cell geometry can fit
the cargo at three tiers, preserving the useful low-deck family without a
120-box threshold. Visibility learning cannot lower below the required floor.
The closure screen now subtracts closed hold pages while keeping their usable
deck pages. These are constructor-family screens, never map infeasibility proofs.
Synthetic regressions cover the reviewed 193-cell demand / 168-cell three-tier
shortage and closure subtraction. `capacity_deck_cap` ablates the new selection;
the existing `capacity_cuts` switch controls the closure-aware screen.

Kept C4 code and provenance:

| Engine file | C4 source and retained idea |
| --- | --- |
| `moment_pages.py` | `lab/c4/c2-layout/pages.py`: actual mass-height, longitudinal and transverse moment rows, pricing duals, geometric pricing types, separate coverage and soft-target slacks |
| `moment_lns.py` | `lab/c4/c2-layout/lns.py`: moment rows in fixed-charge LP/IP and correct fixed-column residual RHS; retains engine work limits and deterministic pattern iteration |
| `dense_master.py` | `constructor_balanced.py:_integer`: soft moment targets retain complete pool choices; adds engine deterministic/conflict work caps and certified page cuts |
| `dense.py` | `method_dense.py`: four dense construction profiles and two checked-seed fixed-charge neighbourhoods, plus exact failed-plan/native-report diagnostics |
| `highs.py` | `pages_fast_dense.py:linprog`: explicit one-thread scheduler before the first fallback LP; used by legacy `pages.py` and copied `mixed_pages.py` |
| `test_c4_*.py` | Copied moment, LNS, LP/IP and thread regressions, with package-safe engine imports and additional integration checks |

`dense_layout`, `moment_pricing`, `dense_fixed_charge_lns`, and
`layout_diagnostics` independently ablate the new features. They apply only to
the medium layout route; the tiny/small portfolio remains the same apart from
the mandatory explicit HiGHS thread limit. Dense construction follows all prior
pure/mixed/closed-cover and local-search work. It uses fixed profiles and engine
LP-round, dive-node and deterministic/conflict limits, with the generous shared
outer safety deadline. Soft moment targets are search guidance, not necessary
physics inequalities or map lower bounds. Every emitted complete candidate
still passes real replay and the unchanged finishing/checker gate.

The copied C4 method's disk references, warm result loading, map-ID branches,
output directories and top-k candidate truncation are deliberately omitted.
Only a fully checked incumbent produced by this engine invocation can seed a
dense warm neighbourhood. Its actual departure tank recipe is preserved using
`departure_condition`, including on replayed candidate plans. No runtime import
comes from the lab or any stored result. Existing engine sight/OOG stages remain
additive; C4's separate experimental sight/balancing and adaptive-motion variants
are not compiled into this kept subset.

Diagnostics retain every dense failed candidate's concrete plan and full native
report in `method_metadata.failure_diagnostics`, indexed from its trial row.
Reports include exact rules, measured values and limits; failures are not called
infeasible. `page_report` can supply a caller cache. Otherwise one extra native
check per distinct dense candidate supplies the saved evidence. No relaxed tier
is counted as an ordinary pass; root tier completion owns the peel ladder.

Validation under the granted one-process slot: the complete scout suite passed
92 tests before the two extra integration regressions; the affected 23-test
subset then passed, including real-fixture moment initialization/count-limited
pricing and soft integer selection when a moment target cannot be attained.
OMP, OPENBLAS, VECLIB and MKL thread limits were one, bytecode/cache disabled,
and temp files stayed under engine. `nice -n 5` was requested but the sandbox
refused priority adjustment. No full-map probe, benchmark or sealed data read
was performed. Per-stage measured contributions remain pending the leader's
frozen quick run; no C4 warm result is presented as a cold engine gain.

Final integration also accepts `checked_incumbent` plus
`checked_incumbent_report` from another engine stage in this invocation. Only
the new C4 reconstruction/local-search stages see it, after all older page
families have finished. The native report must pass, have every part run, and
match both exact inputs through `report_matches_inputs`; instance/call, complete
real replay, ordered actions and native/replay lift counts must agree. The
cheapest own/provided checked plan supplies the seed, retaining its concrete
ballast recipe. It is never inserted into the old anchor's `passing` list.
C4 can still run from that seed when the page reference or anchor setup fails.
Two final synthetic regressions cover unchanged old-anchor work with a supplied
LP pass and rejection of a mismatched report before replay. The final focused
review regression file passed10 tests in the granted single-process slot.
