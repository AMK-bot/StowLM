# Separate adaptive-motion constructor arm

`method_adaptive.py` copies the corrected `method_dense.py`; frozen dense and A
sources remain unchanged. It has separate `diagnostics-adaptive/`,
`trace-adaptive/` and `ballast-adaptive/` output paths. All inherited work limits,
plain fallback, moment pricing, sight restriction, full checker and native
finisher gates remain in force.

The old reference oracle uses whole-ship motions from the initial plain
coverage hint. A later lower cargo CG can raise native securing GM and change
motion amplitudes enough that an old oracle underprices securing loads. The
corrected dense low001 example has a complete 149-lift S-only stow, but its
actual securing ratios remain distant from capacity. That is evidence to
refresh guidance, not evidence that a GM surrogate can replace the actual
containership area requirement.

After every newly assessed concrete reconstruction, the new arm constructs
`departure_condition(instance, assessed.plan)`, so declared adjustable ballast
is applied before native loading and motions are calculated. It replaces
`build_context['oracle']` with `Fast(instance, refreshed_ship)`. The constructor
reads this at the beginning of the next attempt's reference-oracle mode. It
also replaces the seed list with the latest actual candidate plus the original
accepted warm anchor, if present. Known passing plans remain retained by the
normal assessment path. Oracle-free attempts remain oracle-free.

The active model's oracle is deliberately **not** changed while its generator
is paused: its fitted knapsack coefficients, pattern eligibility, factor and
pricing caches refer to its original motion condition. A new model rechecks
warm columns against the refreshed oracle. Several candidates in one attempt
can refresh the context; the last assessed candidate supplies the next
attempt's reference. Metadata records source attempt/trial/plan, full-check
status, refresh success/error and elapsed time. On an ordinary native loading
error the previous oracle is retained.

This is a fixed-reference heuristic, not a necessary bound or an acceptance
rule. It still approximates per-column securing and samples departure motions;
exact phase-specific loading, intact area, securing, visibility and all other
rules remain with the unchanged full checker and native finisher. A failed
search cannot prove whole-map infeasibility. No GM threshold is added.

Code review verifies that `constructor_balanced.iter_generate` retrieves
`context.get('oracle')` inside the attempt loop, and `_warm` subsequently
revalidates columns through `model.add`. `assess` returns a concrete plan with
the retained ballast declaration, not the caller's raw no-ballast scoring plan.
A pass retained before this optional arm's search stays available.

Implementation was code-only while both CPU slots were occupied. A subsequent
separately admitted root run measured all three declared historical near-miss
targets with180s proposal/45s finalization,2tries/1worker. It returned0/3full
passes,3/3complete replay-legal stows, mean104.58wall seconds. It executed
14successful/attempted oracle refreshes with0refresh errors. Source hashes
are inADAPTIVE-SOURCES.json and rows inresults/adaptive-targets. This follow-up
does not improve a measured pass or accepted lift total; keep it as a negative
experiment rather than the recommended whole-cohort entry point. Wall caps
and different attempts make no speed or solver-comparison claim.
