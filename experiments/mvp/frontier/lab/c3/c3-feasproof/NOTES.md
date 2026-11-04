# C3 whole-map feasibility experiment

This experiment distinguishes a legal map, a feasible outer relaxation, and
an unknown map. Only the unchanged full checker can establish a legal stow.
No infeasible numerical LP, failed page master, or ballast timeout is promoted
to a whole-map proof. Final measured rows are in `RESULTS.md` and `summary.json`.

## Scope and process

All writes are confined to this folder. We read the full lab CONTRACT and its
Cycle 2/Cycle 3 updates, lab README, MEDIUM-WALL, C2 Benders/layout notes, C1
early_bound/REVIEW, the C2 restricted page master, and relevant checker source.
The fourteen inputs are exactly the twelve `lab/sets/medium12.txt` maps and
the first two approved `quick_medium.txt` V6 controls (88 and 162 boxes).
There are no sealed inputs, downloads of executable code, checker edits,
commits, cloud calls, or process kills.

Three delegated source/model/audit tasks shared one numerical compute token
with the executive. `c1/leader/BENCH-RUNNING` existed: only one CPU-busy process
ran across the team. Every numerical launch sets OMP_NUM_THREADS,
OPENBLAS_NUM_THREADS and VECLIB_MAXIMUM_THREADS to 1, plus
PYTHONDONTWRITEBYTECODE=1. HiGHS and CP-SAT explicitly use one thread/worker.
`nice -n 5` was attempted; macOS rejected setpriority, so the inherited
priority remained. Owned run PIDs are saved in run metadata. No single map
is intended to exceed twenty minutes. The final 12-map constructive sweep
may exceed twenty minutes in aggregate only if indivisible setup/replay/checker
calls overrun; keeping all preregistered maps is the reason, not extending
individual bounded search.

## Model and outward argument

The object is a fractional transportation network: exact-mass cargo classes
send flow to real bay/deck/tier footprint signatures, coupled by physical-cell,
height, mass, tank, and necessary securing/physics inequalities. Yard, hatch,
and future-restow lifts remain the full replay's objective for construction;
this feasibility LP does not optimize lifts.

For a concrete approved small graph, the historical
`random.small.40100003.0.2` census has 18 box vertices, 20 stacks and two covers.
The first D185 medium input has 121 boxes, 64 stacks and six covers. A long
box occupies two physical cells. Forgetting its exact row and allowing class
fractions enlarge the feasible set. `packing_notes.md` gives the explicit map
from every legal supported stow to its fractional image.

The packing model retains exact box masses and ordinary/OOG heights, length
acceptance, reefer plugs, explicit on/under-deck requests, cell occupancy,
prefix support counts and optimistic summed stack mass/height ratings.
Lower support heights give a lower cargo vertical moment. Tank variables span
zero to full capacity, including consumables, deliberately relaxing real fill
and voyage restrictions. Lower tank VCG comes from its entire solid enclosure,
not a half-fill tangent. Loading displacement endpoints include the source's
normalized acceptance allowance plus a numerical guard.

Weather applicability supplies the necessary upper bound
`KGphysical <= KGvirtual <= 1.5*d_stability`. Lower cargo/tank moments inserted
in this inequality preserve an outer set. Stability GM has its own minimum;
it is **not** identified with lashing GM. The latter uses a different liquid,
CG and free-surface provider. Its branches cover the closed relaxation
`[0,hull.max_gm_lashing_m]`, with zero representing the positive-GM limit.
Branch upper endpoints are not imposed on an artificial shared tank/GM
equation. Only necessary securing floors are strengthened by the lower
endpoint. Thus a branch survivor does not have an achieved GM witness.

`physics_notes.md` derives the guarded rational/Taylor motion envelope, own
box fitting exclusions, and bay aggregate force inequalities. It also derives
an upper envelope on the second-check form factor from rational polygon
waterplane and volume enclosures, yielding necessary area targets. A universal
all-pose righting-area upper bound is retained as a diagnostic. It is much too
loose to couple stability effectively to securing. Upright sight caps and local
hydrostatic tangents are omitted because their outward direction over both
loading providers has not been proved.

This is an outward **algebraic** model evaluated by ordinary numerical LPs.
Any future infeasibility claim also needs directed coefficient/error bounds
and an independently verified contradiction. `audit.verify_farkas` checks
serialized rational contradictions exactly, but does not certify that supplied
rows represent the physical checker. Current feasible witnesses require no
infeasibility certificate.

## Constructive witness follow-up

`page_followup.py` imports the C2 `c2-cgscale/rmp_method.py`, its frozen page
implementation, C1 E9 mask fix and the C2 local ballast helper. No evolving
`lab/engine/` code is imported or copied. The outer model copies no engine code.
The restricted integer master tries the outer per-odd-bay deck count/mass and
cargo moment targets as soft penalties. Its first complete dive is retained
as an incumbent/hint. The target pool may be incomplete; exhaustion or timeout
is unknown. Balance, OOG repair, load sequencing and real replay still gate
every candidate. A local search GM floor of 0.6 is heuristic only, and cannot
authorize a proof. Every resulting candidate receives an unpatched full check;
exact failed plan bytes and reports are saved as well as successful ones.

An LP witness's `physical_kg_lower_m` is the lower KG for that fractional
allocation/tank vector. It is not the achieved KG of a legal stow, nor a
global minimum of the KG ratio. The primary objective minimizes cargo moment,
not the KG ratio or a common stability/securing GM.

## Papers and source verification

- R. E. Moore (1966), *Interval Analysis*, Prentice-Hall. Outward enclosures
  motivate rational endpoint calculations. A primary implementation reference
  is [Sun Microsystems' interval arithmetic manual](https://docs.oracle.com/cd/E19957-01/806-3595/806-3595.pdf).
- G. P. McCormick (1976), [Computability of global solutions to factorable
  nonconvex programs: Part I—Convex underestimating problems](https://link.springer.com/article/10.1007/BF01580665).
  The publisher abstract confirms global exclusion by underestimating programs;
  this experiment uses coarser scalar enclosures and interval partitioning,
  not an implementation of the paywalled paper's complete algorithm.
- The C2 page method inherits Gilmore and Gomory (1961),
  [A Linear Programming Approach to the Cutting-Stock Problem](https://doi.org/10.1287/opre.9.6.849).
  Its restricted pattern family remains constructive, not an outer proof model.

The actual securing-chain argument is derived from the supplied
`c1/fx-finish/early_bound.py` and unchanged checker source. Its source hash is
pinned in physics.py. Run metadata binds input bytes, checker and imported
experimental source. Native source changes during a run must be reported,
not silently attributed to a pinned runtime.

## Reproduction

From repository root, with the four environment variables above and
`PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab/c3/c3-feasproof`:

```
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c3/c3-feasproof/run.py --physics --spatial --out results-final
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c3/c3-feasproof/run.py --followup --start 2 --budget-s 60 --ballast-s 8 --out results-final
```

The initial `results/` and `pilot/` rows are development/pilot evidence, not the
final measured portfolio. `validity.json` and `validity/` retain fresh unchanged
full checks of the preexisting 88/162-box passing plans (71/116 lifts).
The final independent raw witness audit does not import the relaxation builder.

The stronger final outer model adds `rod_bound.py`'s global spring-chain
inequalities, including capacities credited only to occupied bottom roots.
Run `rod_sweep.py` with the same environment for all fourteen maps and eight
lashing-GM intervals per map. `results-rod/` is the final outer evidence;
`results-final/` retains the prior outer targets and all twelve concrete page
follow-ups. The page follow-ups were given the earlier shear-strengthened
outer witnesses; the later stronger rod sweep also remains feasible. It does
not retroactively claim those stows meet the stronger model's particular
witness. `make_report.py` joins both stages and the fresh positive controls.

## Final measured result and verification

All twelve D185 maps are **unknown**. There are zero whole-map infeasibility
certificates, twelve feasible outer witnesses, twelve independently replayed
complete cargo stows, and zero unchanged full-checker passes. All 96 D185
lashing-GM intervals survive the stronger occupied-root rod model. Both
positive controls remain feasible in all eight intervals and their exact
preexisting legal plans project into every necessary row at GM floor zero.
All 112/112 tested intervals survive across the fourteen maps.

The stronger D185 outer sweep took 196.59 s; page follow-ups took 435.53 s,
including twelve full-checker calls. The earlier weaker D185 spatial sweep
took 164.24 s and is reported separately. Controls were freshly full-checked
in 9.297/9.280 s, passing at 71/116 lifts. These are reused preexisting
plans, not two new constructor successes. No paired medium 0.9.0 lift mean
is available. Per-map verdicts and times are in `RESULTS.md`/`summary.json`.

Thirty-seven distinct tests pass: six independent audit tests, two exact
positive-plan projections, one motion-envelope comparison, three spring-chain
tests, and all twenty-five shared lab tests. Rod tests compare 128 source
matrix solves with closed compliance, enclose sampled anchor-height responses
using the analytic endpoint proof, and compare per-tier lower envelopes with
the source rod. The two exact legal plans satisfy the stronger rod rows as
well as the original packing/physics rows. No extra full suite was repeated
after it had passed; the twelve experiment tests were rerun after adding rods.

`verify_final.py` separately recomputes raw packing, mass, tank, weather and
weakened fitting-force rows for both the zero-floor and target witnesses on
all fourteen maps. It independently replays every saved D185 candidate,
checks exact load-list equality/counts, and binds every saved full-checker
report to the exact instance/plan through the checker's canonical hashes.
All fourteen witnesses and all twelve replay/report bindings pass. It also
parses every local Python file. Rod coefficients receive independent source
algebra review plus the source-matrix/control tests; no floating LP dual is
represented as a certified physical contradiction. Native checker/imported
source hashes were unchanged during the final measured stages.

The first 428-box page row initially failed to serialize an infinite *failure
diagnostic*. Standard JSON now records non-finite diagnostics as explicit
strings; finite plan scalars are unchanged. That map was rerun and all four
428-box rows are present. Preliminary serialization/setup failures are not
reported as map infeasibility or passing stows.

The remaining gap is substantive. On D185/185100000, the geometric enclosure
requires total righting area at least 0.15618 m rad, while its universal
upper envelope is 239.94 m rad: far too loose to exclude anything. No
achieved common KG/GM witness exists because the two GM providers are not
identified. Sight likewise lacks a proved transfer enclosure over the full
pose domain. A feasible LP therefore does not decide physical feasibility.

**Verdict: keep part.** Keep the aggregate model, positive-chain bounds and
independent audit as a screening foundation; do not claim the requested
whole-map decision was obtained. Next derive a tight finite-angle hull/liquid
envelope and a certified relation between stability and lashing providers,
then repeat spatial branching. Further page-pool failures alone cannot answer
the map-level question.
