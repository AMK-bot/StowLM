# Cold scorecard entry and evidence

`method.py:propose` calls only `cold.cold_candidates(instance, seed, budget_s,
context)` and gives it a whitelisted, live context. It ignores stored plans,
assignments, ballast recipes and baseline rows. Every constructor record must
assert its real-replay completeness gate; all returned placement sequences go
through the shared runner's independent replay scoring.

`method.py:finish` screens these same-run candidates with the pinned C1
ballast-independent securing bound. A contradiction excludes only its fixed
cargo stow, never the map. It then prioritizes physics/low-KG variants and
tries at most two numerical finishers, allocating 600–900 seconds to the first
when enough budget remains and the remainder to the next. The constructor and
finisher share a 1200-second cooperative deadline, with 60 seconds reserved for
the runner's independent full check. Native opaque calls can overrun cooperative
deadlines; the root-owned process watchdog supplies the hard limit.

Failed candidates, exact failed margins and full-checker reports returned by
the finisher are retained under the output's `details/` directory. All optional
native numeric caches are redirected to this experiment's `results/vessel-cache`.
No cache contains a stored cargo assignment or winning ballast recipe. Source
manifests bind the adapter, helpers and actually imported native/C1/C2 providers.
Do not edit implementation files during a cohort run. The shared runner's own
resume key does not cover every private imported helper, so the wrapper requires
a fresh output directory. `audit.py` detects changed helper dependencies.

First-map command (the file contains the approved first `medium12` input):

```
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 nice -n 5 .venv/bin/python \
  experiments/mvp/frontier/lab/c4/c2-benders/method.py --set first \
  --budget-s 180 --finalize-s 1020 \
  --out experiments/mvp/frontier/lab/c4/c2-benders/results/first-cold-1
```

Use `--set medium12` and another fresh output directory for the whole cohort.
The wrapper uses one worker and one outer finishing try; the inner portfolio
handles the current-run alternatives. CPU admission belongs to the executive,
including the stricter `BENCH-RUNNING` limit.

```
.venv/bin/python experiments/mvp/frontier/lab/c4/c2-benders/audit.py \
  --rows experiments/mvp/frontier/lab/c4/c2-benders/results/first-cold-1 \
  --set first \
  --out experiments/mvp/frontier/lab/c4/c2-benders/results/first-cold-1/audited.json
```

Aggregation reports complete replay-legal stows separately from full-checker
passes, preserves binding failed rules even without a passing plan, and leaves
unattempted maps unresolved. Failed plans receive no passing lift value. Lifts
include D15 projected later-port work for the fixed stow with no later loads.
Neither a local optimization failure nor restricted-pool exhaustion is a
whole-map impossibility proof.

The scorecard still prints its historical comparison, but it is not the fair
Cycle 4 solver comparator. `audit.py` excludes those stored fields. Its optional
`--fresh-e9-rows` accepts a freshly run `c1/leader/method_e9_val.py` arm, labels
machine-load matching unverified, and uses only mutual passes for lift means.
That reference chooses search `min(120,max(20,6*n_boxes))` and up to 300 seconds
of V4 finishing. Its timing may take minutes per map. E9-caused extra passes
are bug-fix effects; V6 is a pass-oriented constructor, not a lift benchmark.
All of these map sets are development data, not unseen evaluation.

The adapter tests use fake providers and standard-library code only:

```
.venv/bin/python -m unittest discover \
  -s experiments/mvp/frontier/lab/c4/c2-benders -p test_adapter.py -v
```

Five tests passed without numerical imports, map solves or checker calls. They
cover ignored historical context, shared/focused work budgets, securing-proof
rejection without a pass claim, the independent runner acceptance gate and
failed/input-mismatched artifact handling. No map result is asserted here.

## First cold attempt: fixed-stow rejection

`results/first-cold-01/random.medium.185100000.0.2.json` records the initial
frozen implementation's first-map attempt: 180.7645 seconds, one complete
replay-legal stow, and zero full-checker passes. The plain constructor loaded
all 121 requested boxes exactly once and replayed at 118 lifts. This is a
complete-stow count and diagnostic lift value, not a passing objective. The
other five constructor variants did not produce a complete replay-legal stow;
their restricted construction failures leave physical feasibility unknown.

The unchanged plain cargo chain has a guarded securing rod lower bound of
243,957.590 N against a 212,500 N limit (ratio 1.148035718). Its five-slot
witness is `0020682, 0020684, 0020686, 0020688, 0020690`; the source equilibrium
relative residual is 9.05e-16. The bound took 0.0695 seconds. The adapter
therefore attempted zero ballast finishes and invoked no native full checker.
This excludes ballast-only repair of that fixed chain. It does not exclude
cargo reassignment, another page pattern, or the map. The useful next step is
same-run cargo/page repair or a cut fixing the complete governing pattern,
followed by a new independently replayed candidate.

A separate standard-library audit confirmed input and saved-plan byte hashes,
121 unique loads with exact requested-box coverage, unchanged implementation
and imported dependency hashes throughout the attempt, and unchanged native
source hashes against the saved manifest. Input SHA-256 is
`e5c94abe50f8bcf87f221d56c092e28c6b00e093a31de318db5765092015be8b`;
plan SHA-256 is
`e99a8019cffab3246d653cc7e9c331ee7bc59cdd8222e3c888870db924e690a3`.
This result belongs to that frozen initial implementation; later constructor
or finisher revisions require a fresh output directory and rerun. No fresh
E9 comparison or whole-map proof was obtained.

## Production cohort plan and read-only source audit

After a fresh first-map pass and independent recheck, use the committed
`medium12` cohort with a fresh output directory and frozen helper sources:

```
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 HIGHS_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 nice -n 5 .venv/bin/python \
  experiments/mvp/frontier/lab/c4/c2-benders/method.py --set medium12 \
  --budget-s 240 --finalize-s 900 --seed 0 \
  --out experiments/mvp/frontier/lab/c4/c2-benders/results/medium12-cold-01
```

This gives construction 240 seconds and finishing at most 900 seconds, plus
the adapter's 60-second reserve for the shared runner's independent check.
If construction needs 300 seconds, use `--finalize-s 840`. Requesting 900 with
300 construction does not add time: `finish` caps its deadline at 1140 seconds
after `propose` began. The finisher also reserves 45 seconds inside its share
for its own ordinary full check. Both checks are required; only the shared
runner's check establishes a scorecard pass.

These are cooperative allocations, not a proven 1200-second process wall cap.
The runner creates its initial Problem before `propose`; independent action
replay, imports, and opaque native calls can also consume time. The executive
must record and enforce any hard limit using only its own recorded process
PID. One worker and one numerical thread respect the active benchmark rule.
Twelve maps may take up to four hours at the per-map limit; preserve all twelve
in the report, including unresolved or interrupted maps. The fixed cohort is
needed to distinguish a single adaptive success from general construction and
finishing coverage. No stored-row comparator is a fair solver-gain comparison.

Failure evidence has three separate sources. If construction returns no
acceptable stow, its per-variant diagnostics remain in the row's
`method_metadata.cold_pipeline.constructor` and `construction.json`; there is
no measured full-checker binding rule. If the securing screen rejects a fixed
stow, its named source-bound certificate remains on that candidate. If exact
finishing runs, each attempt retains failed margins and the finisher's full
report when available; a returned failed plan also goes through the runner's
independent check, whose rejected rule IDs appear in `tries[].violations`.
`audit.py` exposes securing certificates and exact failed margins/rules, but
currently leaves `binding_rules=[]` for a construction-only failure. Consult
the retained constructor diagnostics rather than interpreting that empty
list as physical feasibility or a complete rule check. Interrupted opaque
calls can leave only the already written construction/trace artifacts.

The staged finisher's source audit found that each repaired cargo plan gets
a fresh PhysicsEvaluator, geometry census, local Jacobian and cache identity.
Inherited masses come from this run's candidate ballast, and the incumbent
updates `(evaluator, fractions, result)` together. The final candidate uses
that same evaluator and its capacities, preventing old-cargo tank vectors from
being attached to a different stow. Ordinary full checks remain the acceptance
gate. The evaluation and native-part counters do not count direct equilibrium
queries inside forward-draft root probes, so those counters are not a bound on
all hydrostatic work. No numerical test or solve was run for this audit.

## Revised constructor boundary and probe06

Read-only inspection of `cold.py`, `greedy.py`, `cover_mip.py` and their
reference builders found no path from cold construction to a saved stow,
recipe or witness. Greedy choices use the live model tables, scalar options,
seeded RNG and this invocation's partial columns. Every tentative append uses
the model's static/legal and Fast securing gates. The integer recovery acts
on this invocation's finite pattern pool and revalidates its chosen columns;
its soft longitudinal target and restricted infeasibility are search results,
not physical certificates. The private RMP recovery hook does not replace a
checker provider.

The word `reference` here means a newly constructed motion/sight model. The
native reference resolves this map's initial tanks on a current-run complete
cargo stow. The affine reference solves a fresh C2 ballast seed, applies its
candidate through `PhysicsEvaluator._ballast`, then derives Fast motions and
sight roofs from that ship. Its affine solve is non-outward and requests
`exact_recheck=False`; these gates certify neither stability area nor a full
checker pass. Earlier complete or refined stows within this invocation may
seed later references. That reuse is cold search, not an archived warm start.

`results/cold-constructor-06/metadata.json` reports a development constructor
probe with seed 0, requested budget 235 seconds, four variants/eight-candidate
cap, and 221.4743 seconds actual wall time. It emitted four complete stows:
plain (118 diagnostic lifts), plain descent (116), native affine physics
(114), and its descent (114). The first two fail the fixed-stow securing bound.
The latter two have cargo KG 9.848319 and 9.784813 m, and rod lower ratios
0.819359 and 0.917038. Their saved `proof` is null. These are unknown ballast
feasibility verdicts, not securing passes or full-checker passes; the lower
ratios only show that this particular lower bound did not reject them.
Native-instance physics produced no complete stow, and the fourth variant's
closed-cover search failed in its restricted pool.

Standard-library inspection matched probe06's input hash and `cold.py` source
hash to current bytes. Both surviving plan artifacts have 121 distinct Place
actions, exactly the requested box set, 121 distinct slot identifiers, the
correct instance ID, and no supplied ballast recipe. Their replay legality is
the constructor's recorded native replay result; this source/artifact audit
did not rerun replay or physics. Probe06 is not the independent shared-score
production run, used different variant/candidate caps, and has no saved
full-checker report. Only the frozen `first-cold-02` run and its independent
recheck can establish a cold pass. No fresh E9 comparison is implied.

`warm_restows.py` deliberately opens the archived C3 accepted witness and
labels its outputs `cold=False`, `warm_archived_witness=True`, and
`included_in_cold_pass_count=False`. It is a separate entry point, with no
import from method/cold/ballast. Hashing its source in the cold manifest does
not execute it or read its witness. Any measured warm lift improvement must
remain separate from all cold construction/pass counts.

## Frozen first-cold-02 result and separate warm recheck

The shared score runner completed `results/first-cold-02` in 1105.5540 seconds:
four distinct complete replay-legal stows on the first development map, zero
full-checker passes on that one map. Construction took 240.2702 seconds. Two
fixed stows were rejected by securing certificates; two other stows received
exact finishes of 694.3295 and 168.5250 seconds, with 51 and 25 native physics
evaluations respectively. Their ordinary full reports each ran all 23 parts
without part errors and rejected securing, arrival righting area and draft.
The first report contains 54 securing violations, one area violation and two
draft violations; the second contains 48, one and two. The runner then
independently rejected the final fallback for these same three rule IDs.
The failed candidates' 116/112 diagnostic lift counts are not passing scores.

`results/first-cold-02/independent-json-audit.json` verifies the approved input
hash; all four saved plan hashes and exact 121-box coverage; every implementation
hash against the frozen `source/` snapshot; and unchanged imported/native
source endpoints. Both failed native reports carry canonical bindings matching
their precise saved candidate plans and the approved instance. Their saved
tank maps match the best-physics records. This was a JSON/hash audit, not an
extra numerical rerun. Search failure leaves those two stows unknown, and says
nothing conclusive about all stows on the map. No fresh E9 comparator was run.

Separately, a fresh independent native check of the explicitly warm
`results/warm-restows-01/plan.json` passed in 25.9362 seconds. Legal replay
loaded all 121 requested boxes exactly once; all 23 parts ran with zero
violations/errors and no frozen-provider stubs. The accepted count is 100
projected lifts: 24 relocations, 12 current hatch lifts, 44 later restow lifts
and 20 later hatch lifts. Arrival area spare is 0.000210482 m·rad, and the
smallest rod spare ratio is 0.001379234. All 319 native Python source files
were unchanged across that check. The independent report is semantically
identical to the warm driver's report; their JSON formatting differs.
Raw artifacts, hashes and canonical report/input bindings are saved under
`results/warm-restows-01-independent/`. This is a warm archived-witness
improvement and contributes zero cold passes.

## Cold03 legacy/compact source review

The frozen cold03 adapter adds a cold legacy stage when construction has at
least 240 seconds, the arrival ship is empty and the map has at most 150 load
boxes. That stage receives `min(210, 0.7 * construction_budget)` seconds.
The C2 constructor then receives the measured unused construction allocation,
not a second full budget. When legacy emits a strengthened candidate, the
continuation uses at most 90 seconds of that remainder: fresh affine/native
reference, mixed sight patterns, unused-cover patterns and a soft pool. At a
240-second allocation, exhausting legacy's 168 seconds leaves 72; at 300,
exhausting 210 leaves 90. An early legacy return releases more time, but
compact still caps itself at 90 seconds and at the measured remainder.
Finishing still shares the original
1200-second map deadline and 60-second independent-check reserve. Uninterrupted
native balancing, model construction or reference creation can overrun these
cooperative sub-deadlines; the executive watchdog remains necessary.

The new live-object exception to the context whitelist is explicit:
`cold_live_scored` is assigned by `method.py` from this invocation's legacy
records, after external stored/context fields have been excluded. Compact mode
requires that object's plan to match the instance, replay legally and load the
whole requested set. It builds a fresh Problem and uses only that live plan's
columns and freshly derived reference. Direct callers of the helper must
respect the documented current-run origin; instance identity and replay alone
cannot establish when an arbitrary supplied object was created. The production
adapter establishes origin through its data flow. It reads no saved candidate.

`legacy_cold.py` imports separate C1 pages, method and Fast modules. It binds
ordinary import names only while loading the legacy method, then restores the
embedding process's names. Its relative LCG target comes from its own cold
plain stow, and its Fast motions use that stow's native initial tanks. Only
the authorized E9 mask fix and private LP thread limits alter search behavior;
the production full checker remains the acceptance gate. Aggregate metadata
merges both stages' variants, while the adapter deduplicates complete
assignments and preserves public candidate IDs, total wall time, worker PID
and starting load average. No cold03 outcome is asserted by this source review.

The revised ballast seed portfolio uses geometry to select complete occupied
wide pairs, including pressed partners, for replacement in narrower tanks.
Approximate mass/x/y moment residual and capacity guards validate seed
construction only. Near-full descendants alter a pair only when both partners
remain pressed, so an empty/asymmetric pair is not refilled implicitly.
Arrival balancing tries occupied alternatives with a physical transfer bracket
and verifies the resulting exact loading TCG. For repaired cargo, that balanced
vector is attached to the repaired plan before creating its fresh evaluator
and cache identity. All area/securing acceptance still needs exact native
evaluation and public checking. `seed_evaluations` is a deduplicated submitted
point count, which can include a point rejected at the next deadline guard;
use `evaluations`, completed trace records and native-part counters for actual
completed oracle work. This review ran no tests or numerical jobs; the parent
reported 44 tests passed before freezing the run.

## Frozen first-cold-03 result

`results/first-cold-03` completed in 948.4343 seconds at configured construction
300/finish 840 seconds: two complete replay-legal legacy stows, zero
full-checker passes on the first map. Legacy used 70.7371 seconds and compact
continuation 74.2328, giving 145.2457 seconds total propose time; compact
emitted no additional candidate. The plain legacy stow has a ballast-independent
rod contradiction. The strengthened stow and its repaired descendants remain
unknown after 88 exact physics evaluations and 524 stability/lashing part calls.

The final repaired candidate has 116 diagnostic lifts and five raw hard
violations: four securing entries from two distinct departure chain witnesses
(each is duplicated in the native report), and one arrival area violation.
Worst rod ratio is 1.002460248. Arrival area is 0.286338967 m·rad against
0.292060671 required, a margin of -0.005721704. All 23 checker parts ran with
no part errors; draft passes in this report. The runner independently rejected
the returned fallback for the securing and second-check rule IDs. No failed
lift value contributes to passing means, and no whole-map proof follows.

`results/first-cold-03/independent-json-audit.json` verifies the approved input,
both complete plans' hashes/121-box coverage, the finished plan/report's exact
canonical bindings, its tank map against the best-physics record, implementation
hashes against frozen `source/`, and unchanged imported/native endpoints.
This audit ran no replay or numerical checker. The last search probe is
explicitly incomplete and was not selected as the final best physics result.

The separately fresh `fresh-e9-first-01` row returns no complete candidate and
zero passes in 68.3994 seconds, with no shared-runner finishing or acceptance
check. This does not exclude internal attempts discarded by the plug-in. It uses the
requested 0.9.0-plus-E9 reference entry point at construction 120/finish 300,
three tries. Thus neither first-map arm has a passing lift value to compare.
Machine-load matching is not established, budgets differ, and the fresh
all-12 reference remains in progress; no stored comparator or solver-gain
claim is made. The independently verified warm 100-lift result above remains
separate from these cold failures.

## Frozen first-cold-04 result

`results/first-cold-04` completed in 1101.8678 seconds: two distinct complete
replay-legal 121-box stows, zero full-checker passes on the first map. It
requested construction 300 seconds and finalize 1140; the shared map deadline
still limits search/finishing to 1140 seconds after propose starts, leaving
60 for the runner. A larger finalize request therefore releases unused
construction time to finishing without raising that cooperative map limit.
Actual propose time was 139.7846 seconds and the selected finish share
1000.0870 seconds; finishing plus the independent runner check used 961.9391.

The returned repaired candidate has 116 diagnostic projected lifts:
24 relocations, 12 current hatch, 58 later restow and 22 later hatch lifts.
Its ordinary full report ran all 23 parts without errors and contains exactly
one hard violation, arrival `containership_area_0_flood_mrad`: measured
0.286578476 m·rad versus required 0.291657672, margin -0.005079196444.
Securing and loading pass this report; worst rod ratio is 0.998962840.
The runner independently rejected the fallback for the same second-check rule.
The search used 141 physics evaluations, 846 native stability/lashing part
calls and all 16 SLP iterations. This is an unknown ballast feasibility
verdict for the strengthened stow and its family, not an infeasibility proof.

`results/first-cold-04/independent-json-audit.json` verifies both constructor
plan hashes and exact requested coverage, final candidate mass-map linkage,
native canonical input/plan report bindings, all implementation hashes against
the frozen `source/` snapshot, and unchanged imported/native source endpoints.
It records the sole failed metric and complete report gates. This was only
JSON/hash inspection; no replay, checker or numerical job was added. The warm
100-lift pass remains a separate archived-witness result, and the fresh
all-12 E9 comparator is not yet a completed paired cohort.

## Frozen cohort hard-protection review

The final cohort profile keeps a separate incumbent whose completed native
lashing and loading margins are nonnegative. Its record stores the evaluator,
clipped tank fractions and exact result together. Selection after cargo repair
uses that record's own immutable cargo replay, capacity vector and tank order;
it does not reuse another cargo's fractions or cache. A fully physics-feasible
elastic incumbent takes precedence. Otherwise the final plan is rebuilt from
the protected record and still receives the ordinary public full check, then
the shared runner's independent check. Neither a protected record nor its
area objective is a scorecard acceptance gate.

The area target is the native `containership_area_0_flood_mrad` margin: the
positive righting-area integral to its native endpoint, minus the actual
policy/form-factor minimum (`0.029/C` before any policy tightening).
`physics.margin_scale` infers that current positive minimum as measured minus
raw margin, and divides by it. This keeps the margin's sign; the search ranks
normalized fractional area deficits across phases rather than changing the
physical criterion or substituting GM. Saved raw margins and full reports
retain the physical units. A protected record can still fail area, weather,
sight, discrete stability or other full-checker rules.

Within a stage, near-valid securing and valid loading rows get zero LP slack.
If that local protected LP is infeasible, a logged elastic fallback can guide
another proposal, but exact native threshold checks still reject regressions
and shrink the trust region. Invalid/unavailable/branch-changing finite
differences are fixed to zero movement. The exact line ranking switches to
area deficit once loading/securing are valid; the LP itself continues to
minimize all unprotected elastic slacks. This is local search guidance, with
small numerical guard tolerances, not an outward relaxation, Farkas proof or
whole-family infeasibility certificate. Source-only review found no binding
or acceptance defect requiring interruption of the frozen cohort. No numerical
job, test or production edit was made for this review.

## Completed fresh E9 reference cohort

`results/fresh-e9-medium12-01` contains exactly the twelve approved development
inputs, with no missing or extra map rows. Every input path/hash matches the
committed cohort. All twelve rows resolved as `no_candidates`, with no plan
returned to the shared score runner, no outer finish attempt and zero
full-checker passes. This establishes zero returned complete candidates; the
reference's proposal routine may have built, checked or discarded internal
attempts, which these outer rows do not enumerate. No map infeasibility follows.

Mean wall time is 251.8736 seconds, total 3022.4835 seconds. The direct
`method_e9_val.py` propose/finish hashes match current bytes. Every row has the
same native/shared-runner source digest, which independently recomputes to
`54c4a017ce3c51804d91695f026450334072b53a0f28c7abef4dd577115eb765`.
That scorecard digest covers native stowage, frontier top-level helpers and
score/catalog/summary; it does not bind every transitively imported C1/ref
helper. The independent row/input/source audit is saved as
`results/fresh-e9-medium12-01/independent-json-audit.json`.

The fresh reference wrapper uses capped 120-second search, 300-second V4
finishing and three tries. Our frozen cohort profile uses 300-second cold
construction with a shared 1200-second map allocation. Machine-load matching
is unverified, so timings are descriptive and the different allocations must
be stated. Zero reference passes leave no mutual passing lift pairs or paired
mean lift change to report. Stored comparison fields are excluded. The cold
candidate cohort is still in progress; the accepted warm 100-lift witness
does not supply a cold pass or a paired comparison.

## Final fixed cohort: first row

The final twelve-map cohort consists of the first-map row in
`results/cohort-first-05` plus the other eleven approved rows in
`results/cohort-rest11`. Earlier first-map attempts are development history,
not additional cohort members. Source/profile stays frozen across both
directories: seed 0, constructor 300 seconds, finalize request 1140 inside the
shared 1200-second map allocation. Worker/PID and load metadata must accompany
timings; the remaining eleven maps run at two workers when CPU admission
permits. A per-map table will report complete replay-legal counts, actual
full-checker pass, binding failure/certificate and wall time, with no passing
lift assigned to a failed row.

The first final-cohort map completed in 1061.2977 seconds, with two complete
121-box stows and zero passes. Actual propose time was 252.0636 seconds.
The retained protected candidate's full report ran all 23 parts without
errors and has exactly one hard violation: arrival righting area 0.2853143784
against 0.2922221349 m·rad required, margin -0.006907756507. Securing/loading
pass that report; worst rod ratio is 0.998442183. The native count is 116
diagnostic projected lifts, with `passing_lifts=null`. Finishing used 142
physics evaluations, 852 stability/lashing part calls and 16 SLP iterations;
the independent runner rejected the same area rule.

`results/cohort-first-05/independent-json-audit.json` validates input and plan
hashes, exact 121-box coverage, native canonical input/plan bindings, unchanged
frozen implementation/dependency/native endpoints, and protected-cargo/tank
identity. The protected incumbent context equals the final best-physics
context, and the returned plan's tank map matches it. This JSON audit adds no
numerical verification run. At that audit the other eleven rows were pending.
The completed initial cohort is now frozen in `results/frozen-medium12-01/`:
12/12 maps have complete stows, 27 constructor stows, 26 fixed-stow securing
certificates and 0/12 passes, mean map wall 412.2221 seconds. `RESULTS.md`
contains the final table and scope. There is no whole-family infeasibility
conclusion and no passing lift mean.

## Separate post-cohort coordinate/repair review

The next profile changes the legacy target using only its freshly generated
plain stow. `c1_master_cargo_lcg_m` uses each placement's first support-cell
coordinate, matching the unmodified C1 master row; physical support-mean LCG
is still reported separately. The target is that live baseline at zero offset.
Both the coordinate correction and the changed offset policy are explicit
metadata, rather than an archived numerical target. Source inspection found
no saved-plan, witness or recipe read in this path.

Current-run cargo repair disables the optional zero-total-TCG band under old
ballast, which could exclude a descended incumbent. It retains ordinary
eligibility/E1/E2/stack/heel-change/Fast and real replay gates. The finisher
then resolves the repaired ship, calls native arrival balancing, attaches that
vector to the repaired plan and creates a fresh evaluator/cache identity before
exact physics. A failed balance bracket is not a feasibility claim; exact
physics and the public checker still decide. Warm restow polishing keeps its
separate policy. No acceptance or report-binding defect was found in this
source-only review. The parent reported 24 targeted tests passed; no test or
numerical job was run by the reviewer. These edits/results do not alter the
preserved initial-cohort profile or its counts.

The post-cohort ballast patch chooses finite-difference stencils using cheap
native raw loading at departure, mid-voyage and arrival. It reverses or caps
a stencil before a TCG sign cusp, pins unavailable/too-small derivatives, and
retains mirror/common directions that leave a centered phase's TCG unchanged.
The subsequent full native trial rechecks all three sign branches and
free-surface correction membership before using the derivative. Positive and
negative TCG receive both scaled linear inequalities: the predicted change
satisfies `-|r| <= sign(r) * R*d <= 0`, keeping predicted TCG between its
current value and zero. Centered rows use a small symmetric band. These are
local numerical restrictions, not physical feasibility constraints or cuts.

Source inspection found no new archive input or acceptance change. Complete
native margins, atomic protected cargo/tank selection and both ordinary full
checks remain required. Native root nonconvergence is now caught as an
unavailable stencil and pins that direction; timeout still propagates to the
bounded stage. This review ran no numerical job or test. The root reruns the
full suite on current bytes before `corrected-first-01`; that new profile and
any subsequent results are separate from the frozen initial cohort.

That first corrected profile is now complete and separately audited at
`results/corrected-first-01/independent-final-audit.json`: 2 complete constructor
stows, 0/1 passes, wall 1006.3782 seconds. Its repaired candidate passes loading
and securing but fails arrival righting area by 0.016167056 m·rad; all 23 native
parts ran without errors. Exact plan/report/cargo/tank bindings and the frozen
23-file corrected source snapshot match. The final 118 lifts are diagnostic,
not a passing score. This remains an unknown physical feasibility result; a
later consistent-projection profile requires its own source/output identity.
The initial twelve-map cohort and warm witness counts stay unchanged.

## Subsequent projected-stage source review

The next profile evaluates the same native arrival-TCG projection at stage
initialization, cheap stencil probes, full finite-difference endpoints,
forward-line points and combined trial steps. Each sample returns both the
exact report and the actual projected tank vector. Initial/accepted/local and
global/protected incumbents retain that actual vector together with its cargo
evaluator; the final plan is rebuilt from the same record.

For direction `j`, both its margin slope and effective tank direction use the
same stencil scalar: `E_j = (P(x+h_j*G_j)-x)/h_j`. The LP's positive/negative
tank-capacity rows use `E`, and its proposed move is `E*z`; projected-null
directions receive fixed-zero LP variables. Combined steps are projected again
and judged by exact margins, not by the linear prediction. After projection,
derivatives still require all three native TCG sign branches and unchanged
free-surface correction membership; pressed/slack crossings are excluded.

Projection explicitly checks arrival `|TCG| <= 1e-8 m`, matching the centered
branch tolerance. A looser residual accepted by standalone balancing can only
make a projection/stage unavailable. Root nonconvergence is converted to a local
failure, preserving earlier incumbent evidence. No archive input or public
acceptance gate changes were found. This is still a numerical search
restriction, not an outward feasibility certificate. Source-only review found
no blocker; root runs the full suite before the separately frozen
`projected-first-01` attempt. No projected-profile outcome is measured here.

The projected first-map run is now separately complete and audited at
`results/projected-first-01/independent-final-audit.json`: 2 constructor stows,
0/1 passes, wall 1111.4097 seconds. Its repaired candidate passes loading and
securing but fails arrival area by 0.006299729 m·rad; all 23 parts ran cleanly.
The 112 diagnostic lifts remain excluded from passing statistics. Projection
restored direction 0 in 11 completed valid finite differences; the pure split
direction was correctly projected-null on ten probes. All report/input/plan/tank/cargo and
23-file snapshot bindings match. `RESULTS.md` records the detailed telemetry,
final scope table and open issues. No cold goal or broader infeasibility proof
was achieved, and the independently accepted warm 100-lift result stays separate.
