# Cycle 4 medium proof scaling

Read the lab contract through the Cycle 4 corrections, README, and review F7/F11.
All writes are confined to this Cycle 4 hatch folder. No validation outcome was
used to design or select the medium wrapper. No engine module is imported.

The proof scope is: **fewest lifts among plans the checker passes, counting later
ports by our D15 projection (fixed stow, no later loads)**. The relaxation admits
arbitrary current moves/restows rather than restricting them to load-once plans;
its lower bound drops current export/export order costs, and can therefore be
weaker than the joint first-aboard-order model. Empty decision arrival, closed
covers, no discharges/forecast/joint later loads, finite nonnegative objective
weights, and the recorded checker/rule mode are required. Immutable background
outside the decision-cargo catalog is unchanged. Proof equality requires a
separate fresh full-checker pass, never merely a relaxation witness.

## Model and sources

An integer allocation sends each box attribute group to its physical supporting
stack tuple, ignoring the tier and support interactions. A hold cover and each
of its first-discharge-port incidences pay a fixed two-lift charge. Cover-mediated
deck blockers pay projected restow lifts. The separate Hall relaxation enumerates
cover/port masks whose capacities cover all one-port and two-port demands.
For ordinary heavy boxes with validated empty-bottom witnesses, either they all
fit in the hold, or a heavy deck box forces each common witness cover open.
These are necessary final-state constraints, independent of construction policy.

Roberti, Bartolini and Mingozzi (2015), fixed-charge transportation,
https://doi.org/10.1287/mnsc.2014.1947, motivates the cover/port incidence costs.
Hooker and Ottosson (2003), logic-based Benders decomposition,
https://doi.org/10.1007/s10107-003-0375-9, motivates combining independently valid
relaxations and checker-derived final-state cuts. This wrapper uses the inherited
implementations and source explanations; no downloaded implementation is run.

## Copies and correction

`medium_layout.py` copies `lab/c3/c2-layout/bound.py`; `medium_ports.py` copies
its `bound_ports.py`, with local import names. Both replace `T.static` with the
existing hatch `safe_unary_mask` on a dataclass copy. This keeps F7's DG widening
and safe heat intervals; it does not trust empty-ship DG rejection of supported
upper tiers. The immutable original tables remain available to the inherited
plain packing solver. `medium-COPIED.json` records source/copy hashes; the three
copied full plans and their input/evidence hashes are in `medium-references/`.
The original aggregate source's reported lower bounds are comparator diagnostics,
not silently trusted inputs to the corrected model.

`medium.py:lower_bound` first runs the aggregate relaxation with at most ten
seconds and combines the Hall certificate. If equality is not reached, it runs
the unchanged local plain hatch-packing model for the nominal remaining budget.
The public `certified.lower_bound` interface and all existing bound modules are
unchanged. Joint first-aboard order is deliberately deferred because the medium
test asks whether construction/model size already exhausts a minute.

The fixed three-map list is the feeder500 subset of Cycle 3 `train4.txt`, selected
by the order: 88-box high seed192200000, 87-box high seed192200002, and 88-box low
seed192200000. The prompt's "88-box maps" is interpreted as this feeder500 family;
the feeder800 162-box map is excluded. These maps were preselected as passable by
our V6 feasibility constructor; V6 is not used as a lift benchmark. Medium lift
counts include projected later-port lifts, not a future executable-voyage claim.

## Compute and measurement

While BENCH-RUNNING exists, the whole team uses one CPU-busy process. Root granted
this agent the sole token for the initial three-map probe and tests. Environment:
OMP/OPENBLAS/VECLIB threads=1, PYTHONDONTWRITEBYTECODE=1, vessel cache in this folder.
`nice -n 5` is requested, but macOS sandbox rejects setpriority and still runs the
process. The wrapper records actual elapsed time, map identities, all relevant
source hashes, and marker state. Model construction is not interruptible in the
inherited solver, so a requested time budget is not a hard wall-time promise.

Initial command (from repo root, plus numerical environment above):

```sh
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c4/c2-hatch/medium.py --budget-s 45 --out experiments/mvp/frontier/lab/c4/c2-hatch/medium-results/probe
```

## Initial result and limitations

| TRAIN feeder500 f60 | Boxes | Checked reference upper | C3 bound | Corrected C4 bound | Wrapper seconds |
|---|---:|---:|---:|---:|---:|
| high seed192200000 | 88 | 71 | 65 | 65 | 45.007 |
| high seed192200002 | 87 | 70 | 70 | 70 | 35.512 |
| low seed192200000 | 88 | 55 | 51 | 51 | 12.329 |

Total wrapper work is **92.848 seconds**, with one reference-equality certificate
in three maps; fresh full-checker admission is separately performed by root.
The two gaps remain **6 and 4**, giving mean gap 3.333 lifts. These are inherited
upper bounds, not newly generated plans, and no fresh 0.9.0 paired arm is measured
by this wrapper. This is a bound-scale diagnostic, not a solver lift/time gain.

On high seed192200000, aggregate model building/E6 took 15.70s, Hall's separate
E6 witness validation took 14.97s, and the remaining plain packing budget took
14.21s. Packing has 14,947 variables and 73,549 constraints; CP-SAT returned
UNKNOWN with no incumbent or strengthened bound. This is not infeasibility.
The identical E6 witness probes in aggregate and Hall could be shared safely,
but the measured wrapper deliberately retains source implementations unchanged
apart from the DG correction. It is not a tuned one-minute production solver.

On low seed192200000 the plain packing relaxation solved to its exact minimum
of 51 in **11.49s**, with 14,948 variables and 73,498 constraints. Its relaxed
witness costs only four shipping lifts above 47 unavoidable relocations.
Thus more packing search within this same model cannot prove 55: current-order
or genuine whole-ship/physics constraints must close that gap, or a better legal
plan must be found. On high seed192200002 the necessary E6/capacity proof already
forces 24 shipping lifts above 46 relocations, equalling the checked 70.

All **four medium tests passed**: independent exhaustive shipments versus Hall
masks, forced port-split cost, cardinality/outward rounding, and fresh replay of
all three copied plans with every placement retained by the DG-widened unary
mask. Test command uses the same numerical environment and cache above, plus:

```sh
.venv/bin/python -m pytest experiments/mvp/frontier/lab/c4/c2-hatch/medium_tests.py -q -p no:cacheprovider
```

The first collection attempt lacked the private module path and exited before
running tests; the test now explicitly adds its own directory and the rerun
passes. No model source changed after this measurement, and no shared file was
written. Numerical sessions 27042 and 23060 both exited; the CPU token was released
to root and the constructor agent.

## Declared exact-order follow-up

Before running it, `medium_joint.py` declares only the two still-gapped TRAIN
maps (high000 and low000), **120 seconds each**, seed zero, one numerical thread.
It calls the unchanged `certified.lower_bound` including its first-aboard-order,
current-restow/export-relocation price and lazy final-state cuts; it changes no
public interface or existing bound implementation. Its combined certificate is
the maximum of that result and the corrected initial medium certificate (65/51),
with input/source/reference hashes verified. It retains the copied 71/55 upper
plans; this follow-up is neither a plan construction run nor a validation-tuning
run. It records actual elapsed time because model construction cannot be
interrupted by the inherited time budget. A numerical CPU token is required;
preparation alone does not authorize overlapping the leader's benchmark.

The follow-up received root's sole CPU token and started in session 8191.
High000 completed at **120.018s**, CP-SAT UNKNOWN, no relaxation incumbent or
static-legal witness. The unchanged joint model had **15,457 variables and
142,467 constraints**, 439 safe E6 bottom rows, 1,616 necessary departure-order
rows, 96 safe heat exclusions, and seven DG rows rebuilt optimistically.
Its own surviving bound was only 47; combining the independently checked
aggregate/Hall certificate retains 65. No proof or new plan follows from this
timeout. Model construction completed, so the observed barrier is solving
the full optimistic joint/order model, not a hypothetical eager-OOG blow-up.

Low000 completed at **120.014s**, with 15,458 variables and 142,030 constraints.
Three lazy iterations found two complete static stows, each rejected by the
source-pinned all-ballast securing diagnostic (unavoidable lashing ratios at
least 1.43153 and 1.47841). Two valid full-stow nogoods were added; the final
solve returned UNKNOWN without a retained witness. The model's bound remains
51. Thus the joint run produced **zero new fully legal plans and zero additional
medium equalities**, taking **240.033s** total. The complete medium bound table
remains **65/70/51 against 71/70/55**, one of three equalities; root separately
performs the fresh full-checker admission of the three inherited upper plans.

## Optional global physics row: measured negative

`medium_physics_review.md` derives a genuine global weather-KG necessary row,
with exact rational elimination of independent full-range ballast tanks. Local
LCG/TCG/trim linearizations were excluded because they are not globally outward.
The independent reviewer conditionally approved the source-pinned canonical
feeder500 numeric experiment, retaining finite-domain/roundoff qualification.
The public bound API and its eight existing dependencies were not changed.

All **eight tests** passed in session 84584: the previous four medium tests,
two independent ballast-envelope arithmetic tests, missing-source fail-open,
and projection of all three copied passing TRAIN plans into the optional row.
`medium-results/physics-projection.json` records the complete parameter/provider,
source hash, numeric domain and row values. The row is **redundant even without
cell exclusion, support or mass capacities** on all three TRAIN maps:

| TRAIN map | Independent maximum over every box's slots, million kg m | Necessary row upper, million kg m |
|---|---:|---:|
| high000 | 33.159 | 58.090 |
| high002 | 31.835 | 58.829 |
| low000 | 21.888 | 57.417 |

These maxima are sums of each box's largest lower-moment coefficient over all
slots, so the comparison certifies the row cannot cut any assignment in these
models. It would not be useful to spend another CP solve on this row. A tighter
finite-angle stability/securing relation remains open; substituting a heuristic
GM or trim target would not be a valid proof improvement.

Both sessions 8191 and 84584 exited, and the CPU token was explicitly released
to root. No validation benchmark or validation outcome was read by this agent.
