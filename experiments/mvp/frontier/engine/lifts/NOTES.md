# Frozen post-pass lift polishing

Entry point: `engine.lifts.polish_checked(instance, checked_plan, checked_report,
*, seed=0, budget_s=30., problem=None) -> (plan, metadata)`.

The caller supplies a current-invocation ordinary full-checker pass. The adapter
binds its report to the exact instance/plan through the public
`report_matches_inputs`; an absent or mismatched report requires a fresh native
check. Only complete empty-arrival Place action sequences enter the copied
neighbourhood. Arrival/discharge families retain the incoming checked plan.

The frozen narrow polisher permutes comparable boxes over their existing
occupied positions, preserving length, height, E1-heavy class, E2 rank and stack
closer status. DG and OOG boxes stay fixed. Port/cover opening and transitive
top-access blocker costs guide the integer subproblem; native replay measures
actual lifts. Hard static, mass-share, fixed-motion securing and moment guards
restrict search but do not establish voyage feasibility or map optimality.

The initial plan and its concrete ballast recipe remain available throughout.
Every candidate carries that same recipe. The adapter rebuilds the local
securing reference using `departure_condition(instance, checked_plan)`, including
the actual declared adjustable tanks. It imports only engine-owned assignment
pages, assignment/row helpers, frozen Frontier `alns`, and the unchanged native
checker/move/action APIs. There are no runtime lab imports, disk-plan references,
map-specific branches or saved result inputs.

No improvement or any setup/search/check exception returns the exact input plan
object. A proposed replacement receives an independent final native check and
must have strictly fewer `report.count.weighted_nonproductive` lifts than the
held checked plan. The root integration rechecks that decision too. The model's
compact objective and replay estimate can never substitute for this gate.

The default 30-second allowance matches the frozen method's narrow polishing
stage. The unchanged implementation considers at most eight groups, allows at
most 32 changed assignments, and gives each CP call at most eight seconds with
one worker. It reserves fourteen seconds before starting a neighbourhood.
Construction and native checker calls are atomic and can overrun the outer wall
allowance. These are wall-clock limits, not a load-independent work guarantee.

## Provenance and excluded code

`polish.py` is byte-identical to
`lab/c6/c6-lifts/v1-frozen/polish.py` after removing its single provenance line.
Its frozen SHA256 is
`ced5cdf00857623038dd4534725031e4c95f5cd4de5fc8fdd89e7e8851da2ab4`.
It matches `v1-frozen/FROZEN.json`, and the current live source also matches the
frozen snapshot. The local copied-file SHA256 is
`7c3163060093f94cca1f02e7172a376b6ce82e44512341891ec0be6f94e485b3`.
`SOURCE-PROVENANCE.json` records both hashes and the exact upstream location.

`test_frozen_polish.py` copies all five original `test_polish.py` safety cases
with a package-safe local import. `checked.py` and `test_checked.py` are new
engine adapter/validation code. No `cover_lns.py`, wider polisher, rejected whole
C6 proposer, optional objective constructor or certificate was copied.

The C6 notes reject its whole proposer after a pre-polish pass loss and keep the
guarded post-pass component. No C6 measured win is claimed as an engine result.
The retained neighbourhood is inspired by Fischetti and Lodi (2003), *Local
branching*, DOI https://doi.org/10.1007/s10107-003-0395-5, and the top-access view
of Avriel, Penn and Shpirer (2000), DOI
https://doi.org/10.1016/S0166-218X(99)00245-0. No global optimality claim follows.

## Verification

The focused suite passed **15 tests**, exit0, using the root-granted single
process slot. They cover exact incumbent retention after search/setup/check
errors; rejection of a cheaper failing plan and of equal/more-expensive native
passes; current ballast retention; complete report binding; and departure-tank
reference construction. All copied tests are retained. An initial test fixture
masked the setup function it intended to spy on; the fixture was corrected and
the full focused suite passed. No runtime algorithm change was needed.

OMP, OPENBLAS, VECLIB and MKL thread limits were one. Bytecode and pytest cache
were disabled; pytest temporary files stayed under this package. `nice -n5` was
requested but the sandbox refused priority adjustment. No map solve, quick run,
benchmark, sealed data read or process interference was performed.
