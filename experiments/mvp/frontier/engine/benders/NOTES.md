# Cycle 2 Benders integration

`ballast.py` and `test_ballast.py` are copied from
`lab/c2/c2-benders/`. `cuts.py` copies its alternative-page generation,
restricted integer master and securing cut, with local guards and layout
callbacks. No lab file or native checker is changed.

The master chooses complete pure stack patterns on disjoint physical stacks.
A securing-chain certificate excludes the exact whole patterns supporting
that chain; another securing certificate excludes only the complete selected
stow. The certificate is for fixed instance/background and every ballast
setting, not for unrestricted map infeasibility. `certify_selection` binds the
certificate to the real replayed ship, checks complete bottom-contiguous pure
pages and refuses mixed, holed, overlapping or nonempty-arrival families.
Mixed patterns and per-bay sight can use the callback seam later, but require
an explicit new support proof before chain-local cuts are available.

`page_cut_callback` and `page_cut_repair` are model-local hooks. Column IDs must
remain append-only and cuts must survive all restricted-master resolves.
The repair master also enforces the model's existing banned columns, an
integration fix relative to Cycle 2's standalone restricted master. Exhausting
that generated pool is never a whole-map infeasibility proof.

The local ballast LP is an extra finishing lane after inherited tanks and
before native V4. Hull tangents, half-fill tank arms and free surface are
search approximations. LP infeasibility and approximate Farkas rays remain
unknown physically. Nonlinear three-state loading success is also only
guidance. Every produced recipe receives the unchanged full checker; all
failures retain the complete native V4 budget. A rejected input recipe is removed before native V4: native finalize otherwise
considers an existing recipe finished and silently skips its ballast search.
This fixes a pre-existing engine routing bug for external callers. Even the LP's separate exact
mass-interval certificate is recorded rather than used to bypass V4 here.

The `securing_cuts` and `ballast_lp` engine Config switches control these new
stages. Stage records retain cut support/proof, alternative/master outcomes,
LP status, setup/LP/exact-probe time, proposed recipe presence and full-check
verdict. Quick measured contribution is recorded in the engine's main NOTES.

Theory and its source review are inherited from Cycle 2:
Hooker and Ottosson (2003), logic-based Benders decomposition,
https://link.springer.com/article/10.1007/s10107-003-0375-9;
Codato and Fischetti (2006), combinatorial Benders cuts,
https://pubsonline.informs.org/doi/10.1287/opre.1060.0286.
No new literature search or downloaded code is used.

# Cycle 3 integration for 0.12

`physics.py`, `optimize.py`, `permute.py`, and their tests are copied from
`lab/c3/c2-benders/`; `seed.py` copies the pure-page seed from its
`construct.py`. All imports use engine-owned copies or frozen native APIs.
Experimental CLIs, file-based plan/reference loaders, trace-file outputs,
experiment module imports and process-global vessel-cache settings are removed.
The only source reads in the new physics evaluator hash native Python files;
there are no retained plan, recipe, result-row or witness reads.

The normal route starts with an engine-generated stow and default tanks or
that stow's fresh local LP recipe. Native area and securing margins drive a
trust-region LP with finite differences and Broyden updates. Thresholds are
recomputed for each actual tank state. The local model respects the pressed
98 percent tank regime and the raw transverse-CG cusp. Cargo permutation
exchanges ordinary same-size/height boxes, allowing category/heavy/rank
search classes to vary under the original E1/E2/static/column/replay gates.
The local reference motions and tank recipe come from this call's physics,
not the warm Cycle 3 witness. All fitted rows, no-goods and local failure
statuses remain search guidance, never physical impossibility proofs.

The new lane runs only when a complete public checker report has actual hard
area failures (area units, not arbitrary second-check lever/angle failures)
and/or securing failures, with no other hard failures. Thus the quick D185
stow that also fails trim/visibility does not enter this targeted lane. A
fixed-stow securing proof is not used to suppress cargo permutation: when the
new permutation lane is enabled the engine obtains the full report and may
change cargo before rejecting the original fixed stow.

Finishing order is now securing screen, direct public check, small local LP,
qualified real-area/securing rescue, inherited accelerated V4, then unchanged
native V4. Previously the inherited lane could run accelerated V4 before the
local LP. Both real-area search and cargo permutation have Config switches;
the new search has an additive allowance (120 seconds by default), bounded
iteration/evaluation counts, and no worker processes. Native V4 retains its
full baseline allowance. Exact provider/full-check calls are cooperative and
can outlive the additive deadline; no local timeout is an infeasibility proof.

`area_securing.refine` accepts only an in-memory MovePlan. It independently
full-checks accepted search outputs; the finishing chain also retains its
public checker gate. New regressions reject file-path inputs, forbid external
record reads during the cold route, verify area-only routing, verify no pass
from metadata alone, preserve the 300/600-second native fallback, and permit
cargo permutation after a fixed-stow securing certificate. Copied C3 grouping,
height-objective, margin-scaling and regime-difference tests are retained.
The copied permutation's unguarded `score(Sequencer.order(...))` was fixed to
handle a failed sequencer explicitly.

The first Cycle 3 D185 witness was warm and targeted. This compilation makes
no cold-pass claim without new measurement. Its 114 lifts include projected
later-port work under the fixed-stow, no-later-loads model. The binding area
spare was only 0.000158 m rad in that checked witness; neither its success nor
local search failure proves a cohort pass rate or whole-map infeasibility.
The baseline comparisons in the main NOTES must distinguish a fresh matched
0.9.0+E9 arm from stored rows and attribute E9 extra passes to the bug fix.

Additional inherited references: Griffith and Stewart (1961), successive
linearization, https://doi.org/10.1287/mnsc.7.4.379; Broyden (1965), secant
updates, https://www.ams.org/mcom/1965-19-092/S0025-5718-1965-0198670-6/.
These motivate heuristic local search; no convergence certificate is claimed.

The 0.12 work-count page route may pass its `Work` object to
`page_cut_repair`. In that mode alternative generation uses a fixed 1,200-pair
cap and the restricted CP master uses fixed conflict/deterministic-work caps;
the supplied deadline is only an outer safety wall. The old fractional wall
allocation and 15-second ordinary stopping rule remain solely for the old
layout route. Work limits are retained in the repair contribution record.
