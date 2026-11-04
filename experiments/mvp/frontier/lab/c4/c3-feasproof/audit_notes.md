# Cycle 4 independent validation

The positive-control registry includes every saved passing f60/f70 result row
under Cycle 3's layout experiment whose input has explicit authority in that
experiment's `MANIFEST.json`. The saved success audit and ladder comparison
also supply evidence aliases. Canonically identical final plans share one
fresh check; different tank recipes remain distinct controls. This gives 18
f60/f70 plans over 11 maps, plus the independently verified 114-lift D185
witness in `c3/c2-benders/results/verified-independent/plan.json`: 19 plans
over 12 maps. These are preselected passing development controls, not sealed
maps or a new constructor comparison.

`audit.py` imports neither the search oracle nor a checker wrapper. It uses
the unchanged full checker with ordinary rule activation, exact stored final
plan bytes, and native D57 voyage liquid-state derivation. Detailed reports
retain qualified departure, midpoint and arrival parts. The legacy aggregate
stability metrics describe departure and must not substitute for all three
phases. The executive owns all JSON artifact writes. Native vessel caches are
redirected into this Cycle 4 folder for the fresh run.

For every phase retain physical KG, corrected virtual KG, mass, both drafts,
mean draught, trim, positive total GZ area, its required minimum, rod ratio,
and the distinct securing GM. Corrected virtual KG includes stability's FSC;
securing uses another native provider. A GM or draught interval established
for one provider cannot silently constrain the other.

An area envelope must cover the actual checker's finite-angle free-trim,
worst-side, liquid-corrected curve and its interpolation/integration policy.
The total integral ends at flooding or the first vanishing angle after a
positive lever. An ideal analytic curve bound is insufficient if the qualified
numerical table can evaluate above that ideal bound. Qualification residuals
are empirical evidence, not certified global interpolation-error envelopes.
The default audit comparison has no hidden numerical allowance. Any explicit
allowance is recorded as an observation tolerance and never authorizes an
infeasibility proof.

The raw interval helpers use exact rational decimal endpoints. Their tests
reject inverted or missing endpoints, unknown inputs, nonfinite area values,
and an upper area even infinitesimally below an observed control. They check
arithmetic and identity, not the theorem establishing an envelope's physical
outwardness. No checker rule, parameter or activation is changed.

Fresh measurement: all **19/19 plans** passed unchanged full checking, legal
replay, exact requested load counts and all requested boxes aboard, with zero
violations. There are 57 qualified phase observations. Aggregate checker and
replay time was **335.780 seconds**; first cold checks on feeder500/800 cost
90.147/86.079 seconds. The accepted D185 control remains 114 lifts in
27.666 seconds. These are warm saved-plan validations, not cold solver gains.
`controls.json` records every identity, source hash and measured phase;
`control_reports/` retains complete fresh native reports. The executive ran
all nine audit tests, four native-semantics tests and three table-bound tests:
16 passed.

The native-provider source review found four coverage requirements now made
explicit in the union helper and toy regressions: hull interpolation accepts
and clamps queries within 1e-10 of a grid endpoint; liquid interpolation
accepts and extrapolates within 1e-13; prismatic liquid tables still evaluate
the zero-trim subdomain of a broad trim interval; and pressed-full liquid CG
uses the special constant `moments[0,0,-1]/volume`, independently of pose.
Shaped variable-consumable correction maxima retain zero as a lower bound
because both native scalar and batch providers explicitly apply `max(0,...)`.

These repairs do not establish uniform numerical enclosures for the direct
floating polygon or liquid fallback providers. The direct polygon kernel
becomes poorly conditioned near vertical free trim. Accordingly the native
union helper's `checker_outward` flags must remain false. No map-level
infeasibility conclusion is authorized from these prototype envelopes.

Final independent JSON audit: **23 maps, 69 phase envelopes, 57 observed
control-phase enclosures**, all without exclusions or identity/flag errors.
All 69 serialized rectangular area sums were re-evaluated with exact Python
Fractions and fit below their published upper endpoints. All artifact digests
match `windows.json`; current source hashes match the frozen area summary.
The map windows record 23 successful raw-mass audits and 92 independently
verified serialized support certificates. Strict area fields remain null;
candidate numerical areas were never installed as necessary constraints.
The coarse area sweep took **480.406 seconds**. These checks verify arithmetic,
coverage bookkeeping and preservation of the controls, not uniform native
floating-kernel outwardness. All eleven remaining D185 maps stay unknown.
