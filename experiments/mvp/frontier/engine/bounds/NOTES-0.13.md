0.13 bound and constructor integration
====================================

The exact model, joint first-aboard model, strengthening, geometry and securing
cuts, lazy loop and public certified wrapper originate in lab/c4/c2-hatch.
Those eight upstream algorithms are unchanged from C3; the engine retains its
reviewed domain, DG, geometry, oracle-unknown and objective guards. The new
constructor.py, constructor_fast.py and constructor_target.py and their three
test files are copied C4 sources, with local package imports and provenance.
No runtime source, witness, incumbent, assignment or ballast is read from lab.

Public API:

* map_bound(..., certified=True, upper_bound=None, work_limit=10., budget_s=900.)
  shares work 25% legacy / 75% certified. Each internal solve uses one worker.
* certified.lower_bound(..., upper_bound=None, work_limit=10., budget_s=900.)
  tests the integer improvement cutoff U-1 only for integral objective weights.
  The caller must supply U from a fresh real full-check pass; absent U it proves
  the ordinary relaxation bound. Cutoff infeasibility means no cheaper plan,
  never whole-map infeasibility. D15 remains fixed stow with no later loads.
* constructor_target.construct(..., work_limit=2., budget_s=900.,
  apply_e9_fix=True) uses 15% work on the independent plain packing target.
  Restricted load-once infeasibility advances candidate targets only. Its
  independent_lower_bound is never raised by that restricted infeasibility.
  actions/first_model_actions are raw replay candidates, not checker passes.

SolverWork accounts actual CP-SAT deterministic seconds cumulatively across
fresh lazy solves; a deterministic solve-count guard also bounds zero-work
restarts. Wall time is solely a generous outer guard in this mode. Necessary
E6 row construction is complete rather than wall-truncated in work mode.
Outer-wall interruption is explicitly work_complete=False, so D167 work-mode
comparisons can exclude unfinished allowances. Work_limit=None keeps legacy
wall policy for compatibility. Native static oracle errors never create cuts.

Producer metadata distinguishes independent_plain_packing from the joint/lazy
model; strictly tied values retain the earlier certificate source. Witness
availability is independent of producer selection. A lower-bound equality
becomes proved_optimal only in the engine after an unchanged full-check pass.

The adjacent engine/unary.py is engine-original, not a copied relaxation. It
enumerates a superset of all physically supported final departures for empty
arrivals with <=6 loads, <=16 physical cells and no forecasts/plan_calls.
It uses only native physical size footprints and bottom-prefix support, never
T.static, empty-ship DG eligibility or approximate heat. A universal hard E8
away_from_heat violation for the same booked box certifies only that unpeeled
T3 booking; it cannot prohibit the tier-peeling route or claim T0 infeasibility.
Any incomplete enumeration or native oracle problem is unknown. The approved
random.tiny.20000040.0.2 regression requires all 192 supported stows to reject
QZXU0000035 under that same native E8 rule.
