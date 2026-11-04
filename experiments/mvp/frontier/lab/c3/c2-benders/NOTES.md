# Cycle 3: real righting-area and securing search

Goal: the first unchanged full-checker pass on any fixed D185 TRAIN map.
Only this folder is written. No sealed inputs are read; all map paths come
from the committed lab medium12 set. Cycle 2 and Cycle 3 contract updates
were read in full. The benchmark marker existed at admission, so the entire
team uses at most **one CPU-busy process**, one numerical thread, until it
disappears. Read-only/code subagents do not run numerical jobs. Every owned
long numerical job records its PID/session, attempts nice -n 5 (sandbox
setpriority is denied), and uses a vessel cache inside this folder. No other
session's process is stopped. User permission to perform the work is already
provided by the Cycle 3 order; no additional approval is needed.

## Model and assumptions

The master chooses whole ordered stack patterns covering concrete boxes once;
physical stack footprints cannot overlap. Tank masses couple patterns through
the three ordinary D57 voyage conditions. Lifts are the real move replay's
yard relocations, hatch lifts and future restows. A complete replay-legal stow
is distinct from a full-checker pass. The small/medium examples remain the
18-box/20-stack/two-cover and 121-box/64-stack/six-cover graphs documented by
the original scout; here the first medium map is the actual 98-lift stow.

The inherited stow has cargo KG 9.536906935 m and cargo vertical moment
18,179,168.955 kg·m. Fixed/background cargo is retained. No synthetic mutation
of hull, cargo mass, gravity, tank parameters, rules, activations, tolerances
or voyage policy is allowed. Search-only approximate constraints never
establish infeasibility, and partial physics never establishes a pass.

## What actually binds

The .6 GM-floor recipe's three failing tests are **total positive GZ area to
flooding or first vanishing angle**, `containership_area_0_flood_mrad`, not
scalar GM and not area to 30 degrees. Arrival is worst: .243343 required
.307787 m·rad; the departure rod ratio is already .996425. At .9 the arrival
area improves to .256324 required .290736, but securing fails. Departure
area actually decreases when ballast is increased. Therefore scalar GM is
an inadequate substitute for the real area criterion.

`PhysicsEvaluator` calls the ordinary check_stability and check_lashing for
each requested real voyage state, with their own ordinary qualified tables
and direct fallbacks. It scales each area margin by its **current** Code
threshold; form factor and hence threshold vary with tank setting. It saves
all raw margins, metrics, reports, exact masses and timings. It never calls
an alternate checker or changes its providers. A final candidate always
receives `stowage.checker.check`.

## Algorithms and reused code

- `physics.py` is new exact separation/memoization around native providers.
- `probe.py` maps tank filling regimes on the real retained C2 stow.
- `optimize.py` uses a trust-region linear program over nine symmetric tank
  pair increments. Real area, securing, loading and weather margins provide
  finite-difference Jacobians; exact evaluated steps update them using Broyden.
  Jacobians refresh after pressed/slack regime changes and poor progress.
  Every trial uses exact native physics. Local LP infeasibility/stagnation is
  unknown physically. Pair increments preserve initial port/starboard mass
  differences and are a search restriction, not an infeasibility argument.
- `construct.py` imports the stable C2 cgscale page/pricing implementation and
  C2-layout OOG repair. It adapts the layout's asymmetric port permission,
  includes base heights in pricing types, and clears the first-fit incumbent
  shortcut before genuine low-KG polishing. Stable Frontier Problem/Sequencer
  supplies real replay. The authorized C1 E9 wrapper is installed. Mutable
  `engine/` is not imported or edited.
- C2 retained raw and ballast plans are read, never rewritten. Their full
  checker reports are historical evidence; new passes require fresh checks.

Primary mathematical references: Griffith and Stewart (1961),
[A Nonlinear Programming Technique for the Optimization of Continuous
Processing Systems](https://doi.org/10.1287/mnsc.7.4.379), for successive local
linearization; Broyden (1965), [A Class of Methods for Solving Nonlinear
Simultaneous Equations](https://www.ams.org/mcom/1965-19-092/S0025-5718-1965-0198670-6/),
for secant Jacobian updates. These are search heuristics for the nonconvex
checker, not global convergence/proof claims. Pattern construction reuses
Gilmore–Gomory (1961) and the restricted-master heuristic motivated by
Sadykov et al. (2019), DOI 10.1287/ijoc.2018.0822. Source review, primary
links and the exact criterion are detailed in REVIEW.md. No downloaded code
is run.

## Early measured failures retained

Near-full DB-M experiments separate the native policies: stability's ballast
correction list stops at 98% fill, whereas securing uses actual tilted-liquid
FSM at every nonzero fill. On the .9 recipe, DB-M=.99 worsens arrival area
slightly to a -.03549 m·rad margin and still fails securing. At .975,
crossing the correction regime raises arrival FSC from about .1395 to .5895 m
and worsens the area margin to -.05103. DB-M=.9 finally passes departure
securing, but area worsens to -.07224. DB-A changes are weak. Thus the regime
hypothesis does not rescue this stow by itself. The first cold two-part probe
took 69.1 s (67.4 s stability setup); later two-part probes took 2.0–4.2 s.
All measurements, including the negative ones, are under results/regimes/.

The first low-roof constructor batch produced three complete replay-legal
stows in 30.56 s. Its new four-tier candidate lowers the roof 23.355→21.984 m
but **increases** cargo KG 9.53691→9.69455 m, at 116 replay lifts. Other
seed-derived candidates retain KG. No full-checker pass follows from these
construction-only measurements. Tight four-second CP masters returned
unknown; they do not prove packing infeasibility.

Three oracle scaling/sanitization tests passed. Final experiment outcomes,
full-checker passes and broader regression verification will follow below.
Individual local ballast searches are budgeted at ten minutes. A whole cohort
or chained construction/physics run may exceed 20 minutes only to preserve
all predeclared cases and unchanged full-checker verification, not because
one stow gets unbounded search. Cold qualified cache setup is timed separately.

## Cycle 3 search progression

The initial 98-lift stow received 60 exact physics evaluations in 543.4 s;
arrival area deficit fell from .06445 to .01292 m·rad, with departure rod
ratio 1.00317. Legal whole-column/box descent then lowered cargo KG by
.024565 m (46,825.5 kg·m), relieved that chain to .99405 at fixed reference
motions, and produced a complete 106-lift replay. These are distinct stows.
Its next 75-attempt search took 358.4 s and passed every full-check rule
except one arrival second-check area test (deficit .005582 m·rad).

Raw upright TCG appears as an absolute-value term in the actual conservative
worst-side GZ curve. An opposite-sign HEEL split releases the inherited
transverse offset; the local LP now respects that cusp instead of predicting
benefit beyond zero. Finite differences stay on their current 98% pressed
regime, shrink with the trust region, and refresh at regime/sign changes.
The next search used 45 unique physics evaluations in 271.2 s. Its unchanged
full check (6.79 s) again failed only arrival area: .287741 against .291350
m·rad. All securing tests passed; departure rod ratio was about .999721.
Departure forward draught was 7.04743 m against a 7.05 m limit. Search
failures remain unknown, never physical impossibility certificates.

`redistribute.py` replaces slack DB-F water with narrow FPT/FWD-DEEP and
WING-A water, preserving total mass and native departure longitudinal
moment and root-solving arrival raw TCG to zero. It explores regimes that
the earlier pair-offset restriction excluded. Both original P-only and
corrected two-tank variants and their exact reports are retained. The
two-tank FWD-DEEP replacement reduced the area deficit to .0005143 m·rad,
but securing ratio rose to 1.004603. Removing stability FSM and removing
securing FSM are coupled differently; neither scalar GM nor a lower roof
alone predicts acceptance.

The fixed-topology assignment model in `permute.py` exchanges ordinary
boxes of identical size, physical height, rank, heavy/reefer status; protected
boxes stay fixed. It minimizes the native stability cargo vertical moment,
with stack/LCG/TCG rows and reference-motion securing search rows followed
by exact Fast rechecks. These fitted rows and pattern no-goods are search
restrictions, not universal ballast certificates.

The available c3-feasproof finite-angle relaxation reports unknown with a
feasible outward-model witness on the first four D185 maps; its constructive
follow-up does not pass. It provides no whole-map area impossibility proof.

### Resource incident

While the benchmark marker still existed, a stop request via os.kill for an
owned probe was denied. I incorrectly started its corrected replacement
without first confirming exit, briefly overlapping two owned busy processes.
I stopped the newer job through its recorded managed session (Ctrl-C), waited
for the original to finish, and restarted serially. No other session's process
was touched. This was a resource-limit violation, not a measured speedup.
The marker was subsequently absent when the fixed-topology CP job was
admitted alongside the sole parent probe, restoring the usual two-process cap.

The narrow-tank local run took 497.25 s (31 unique attempted oracle
evaluations; its deadline ended an incomplete last evaluation), including
a 17.08 s unchanged full check. Best result: still only the arrival area
test, deficit .00070190 m·rad; departure securing ratio .998458. A restricted
fixed-topology CP solved optimally in .036 s after 3.46 s setup but lowered
no cargo KG, then failed the full checker (area and a slightly changed rod
chain). Its sorting relaxation leaves only .0150 m cargo-KG room inside the
same rank/height groups. Relaxing the search rank groups while explicitly
retaining every original E2 upper/lower inequality is being tested; this is
a larger search family, not a relaxed acceptance rule.

An inspection error in a follow-up direction proposal initially described
the saved HEEL finite difference as negative. Its retained fraction vector
shows it was +.0075. This was corrected before any such drawdown was run.
The corrected probe tests WING-F→HEEL mass-neutral transfers and ordinary
HEEL additions, each through all three exact native physics states.

The later final feasibility-proof notes now cover all twelve D185 maps:
all remain unknown, with zero whole-map certificates and all 96 lashing-GM
intervals surviving their stronger occupied-root rod relaxation. Their
finite-angle envelope is explicitly too loose. Thus these results do not
justify abandoning the constructive near-miss search on this map.

Shared lab plus then-current C3 regression suite passed 39 tests, including
the real-checker rejection of an illegal finished plan (65.5 s for that test).
After adding heavy-family relaxation, all five permutation unit tests passed
again; together there are 40 distinct passing tests. The later CP experiment
also retains exact original column/replay and native full-check gates.

## Final result: first D185 full-checker pass

**`random.medium.185100000.0.2`, 121/121 requested boxes loaded, legal replay,
114 weighted nonproductive lifts, zero violations.** Two separate fresh
processes imported only the ordinary native checker, with no search/oracle
imports or rule wrappers. They passed in **23.2303 s** and **22.5947 s**,
producing byte-identical full reports. The checker itself binds canonical
instance/plan hashes in its provenance; the separate verification manifest
also binds input/plan/report bytes and all 88 imported native source files.

Final witness:
- `results/verified-independent/plan.json`
- `results/verified-independent/full-report.json`
- `results/verified-independent/verification.json`

Input SHA-256: `e5c94abe50f8bcf87f221d56c092e28c6b00e093a31de318db5765092015be8b`.
Plan bytes SHA-256: `b83cad9747d4edc2cf569f1a47fa8fb36021730cbae8fcc3359976b0f2a94891`.
Report bytes SHA-256: `0cb77cf9b322fc7006ef9428b733779e7eb9cefe2e62f4341cc0e3e3ea7851a9`.

The successful cargo model removed an unintended extra restriction: synthetic
`category` strings themselves encode discharge port and weight class, so
preserving category silently kept both fixed even after rank/heavy relaxation.
`--relax-category --relax-heavy` permits those exchanges while retaining
concrete unary eligibility, exact original E1/E2 rows, original column gates,
real sequencing/replay and final unchanged full check. Seven permutation
regressions now pass, giving **42 distinct passing tests** with the shared
suite and other C3 tests. No catalogue or checker rule changed.

The final CP search used 3,797 assignment variables/15 groups, returned a
retained incumbent in 30.6454 s (26.7291 s CP), and changed 84 boxes from the
106-lift refined stow. Cargo KG decreased .0011294 m to 9.5112126 m; cargo
LCG moved aft .2598287 m. Its new arrangement costs 114 replay lifts and
gave reference securing ratio .991051. Combining this cargo assignment with
the exact `heel-plus-0.08-dbm-plus-0.0008` recipe produced the accepted witness.
The eight-variant tank batch took about 150 s; earlier exploratory searches
took minutes, as recorded above. These are **warm artifact-stage times**,
not a measured cold-start D185 runtime.

What binds now: arrival area .291164308 required .291006173 m·rad (spare
.000158135); departure maximum rod .990776504; forward draft 7.022355875
against 7.05 m (27.644 mm spare). `BINDINGS.md` records all three phases,
including the distinct stability/securing GM and roll period. The most
effective tank changes removed slack wide DB-F tanks, controlled raw TCG,
and used HEEL/FWD-DEEP changes while explicitly controlling forward draft.
Increasing a scalar GM floor alone did not solve that trade-off.

**Gate counts:** new C3 complete replay-legal stows exist on two targeted
D185 maps (121-box first map and 229-box `sim.medium.185100110`); **one map
passes the full checker**. The latter 229-box descent retained complete
legal replay but failed its post-repair LCG search guard and was not fully
checked; it remains unknown. The other eleven D185 maps are undecided by
this work. No whole-map infeasibility proof is claimed.

### Quick regression, measured separately

`results/quick-regression/summary.json` is a stored-reference replay lane:
fresh full checks preserve inherited 0.9.0 recipes and load order. Tiny:
8/8 versus 8/8, paired mean 6.375 versus 6.375, 15.465 s/map. Small:
9/10 versus 9/10, paired mean 13.8889 versus 13.8889, 7.826 s/map. Three
medium maps have no stored candidate and no paired lift estimate; the D185
pass above is separately verified, not counted as a stored-regression result.
The historical 0.9.0 big-planner D185 attempt failed, as documented by the
Cycle 3 contract; consequently there is no paired D185 lift comparison.

### Entry points and reproduction

Verify the frozen witness, one process/thread, from the repository root:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 nice -n 5 .venv/bin/python \
experiments/mvp/frontier/lab/c3/c2-benders/verify.py \
--plan experiments/mvp/frontier/lab/c3/c2-benders/results/verified-independent/plan.json \
--out experiments/mvp/frontier/lab/c3/c2-benders/results/reverify
```

Search entry points are `construct.py:generate/refine`,
`permute.py:optimize_assignment` and `optimize.py:optimize`; they retain
search restrictions and never turn local failure into an impossibility
certificate. `physics.py:PhysicsEvaluator` separates exact native margins.
`summarize_bindings.py` extracts the final physical table from saved JSON only.
The CP driver is frozen in `results/permute-category/driver.py`; local search
driver snapshots remain next to their traces. Both inherited C2 artifacts
and stable C1/C2 code were imported/read only. Mutable engine was not used.

**Verdict: keep.** A new D185 full-checker pass meets the contract's keep
criterion. Next use the same real-area/securing rows and wider category-safe
exchange groups on the remaining maps, then measure a cold pipeline and
reduce the passing witness's 58 projected restows. The compute-overlap
incident above remains disclosed; no speed claim relies on its partial run.
