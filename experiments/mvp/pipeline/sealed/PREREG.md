# Sealed pipeline protocol

Fixed before any sealed map opens. [AMENDMENT.md](AMENDMENT.md), dated
2026-11-01 EST and SHA-256 pinned in AMENDMENT.sha256, adopts logic2 findings
1–11 and supersedes conflicting original text. preregistration.json contains
the executable registration; every freeze binds all three and the amendment pin.
No sealed map or result has been used for development.

Synthetic ships and cargo, simulated port. This measures the **planning step
from a clean load list, templated sentences**, under frozen standard, not live.
Fresh generator draws are not new kinds of voyage. Productive cargo lifts are
excluded: comparisons concern **extra crane lifts**.

Keep tiny15, small30 and envelope24 separate. Test v3 reports 500–800 TEU
(10 tiny,20 small) apart from1700 TEU (5 tiny,10 small); envelope by ship only,
no finer rates. Every pass count has an exact two-sided Clopper–Pearson95%
interval. Gate rejections remain out_of_envelope in the all-map denominator.
Full-checker L0, completed cargo, obeyed intended instructions and fresh audit
are required for a pass; relaxed levels and all failed rows stay separate.

Arms: runner.run_packet standard, and a simple two-step planner, modelled from
one field expert's description (15s constructor+15s finisher). Envelope adds
one instructed pipeline condition and secondary two-step with300s finisher.
186 trials total:69+69+24+24. No Proposer, chance or fitted-rule arms.

Inside the claimed prepass, order canonical public_facts, tie by map ID. Cargo
sorts before ship and includes background ashore boxes; assignment is not
ship-stratified. rank%4 selects close_bay,max_tiers,dg_keep_away,one_port_per_hatch
(six each), bay=(rank//4)%odd_bays. Cap=max(0,physical−2), deck if positive else
hold. DG picks most numerous matching class on load list including mixed risks,
one vote per box/class, lexical tie; fallback3 and no_matching_cargo explicit.
Never redraw for ease, feasibility, unsupported geometry or absent targets.
Release requires dev-cohort evidence that the fixed cap binds on a strict
majority of all maps; absent plans count against that requirement.

standing=''. One reader instance reads each instruction once, serially before
planning. Record sentence, context, intended rule, allocation/map hashes,
reading/evidence and time. Workers replay recorded readings only. Confirmation
source sealed_expected_match means exact intended-rule equality, not a human.
Fresh audit rechecks the intended rule even if the reader parsed something else.
Reader and engine failures appear separately per type.

Instruction primary: binding obedient passes divided by maps whose
fresh-audited no-rule plan broke the intended rule. Instructed L0 pass and fresh
audit required. Baseline counterfactual failure does not change baseline pass.
Already obeyed and unavailable counterfactuals separate, overall/per type.
Report changed load-box physical-cell sets and signed instructed-minus-baseline
lift change; already-obeyed pairs are a search-noise floor. Negative changes
are search variation, not evidence that a rule saves lifts.

Primary lift metric: unweighted lifts_total. Primary cohort envelope24 with
at least8 mutual L0 passes; otherwise no medium lift claim, and fallback is
small30, explicitly named small, with500–800 TEU its registered primary ship
population and1700 TEU secondary. Inconclusive envelope interval never triggers
fallback. Quotable savings require complete audited non-surrogate evidence and
a paired95% mean-difference interval wholly below zero. Always show current-port
lifts and its own paired interval; inconclusive current change requires projected
D15 wording. Weighted and every other comparison are secondary. Means use
passes only, mutual comparisons use identical maps, no cross-cell mean claims.

Report wins,ties,losses and paired bootstrap95% intervals:10000 resamples,
random.Random(0), nearest-rank percentile. Missing metrics never become zero;
zero-baseline saving fractions remain undefined. Pass counts and2x2 discordance
tables are separate from mutual lift savings. Current and projected later-port
components accompany total. CSV preserves every map/arm, hash, status, time,
audits, reading/recheck, plan change and lift components.

Waive evaluation.md:85–87's weight-tolerance0/500/2000kg sensitivity: comparison
is with the labelled simple baseline, not field planners; budget instead tests
finisher saturation. State relocation-policy confound beside comparisons:
methods choose relocation destinations differently; port blocking and policy
can explain savings, and experienced planners may already block by port.

One global claim, no resume, no fresh-directory bypass, no trial retry. Phase
capabilities bind full request/run/map/arm/phase and are atomically consumed
before work. Sealed opens log measured byte hashes. Dirty2.1manifest/profile
or source-integrity failure refuses further opening. Child timeout, crash or
failed audit retains status error and partial evidence; remaining trials continue.
Reports publish in finally and are provisional on any error/interruption; no
sealed claim or replacement run. Only owned, recorded child PIDs may be stopped.

Workers<=enabled cores−2:18CPU,12 withGPU; selected workers and hours await the
adopted binding/profile study. No duration target. Admission waits, never raises:
fresh core-state.json age[0,120), popcount>=workers+2, load1<=workers+2,
explicit other_fleet_work:[] proving no other fleet work. Current mask/load-only
telemetry cannot authorize this release. Runtime receipt must include serial
reading, every arm including300s sensitivity, setup, checker/audits and admission.
Scale measured concurrent batch elapsed by map count, never divide by workers
again. Default2 workers is for development; maximum16 is only a physical cap.

Release remains blocked by adopted reader, frozen2.1manifest/standard, approved
runner sealed authorization seam, dev cap calibration, measured budget and fleet
exclusivity. Offline development uses maps/dev.txt, falling back toquick_tiny and
quick_small. Explicit reader/engine/two-step/admission surrogates prove report
shape only. No real-ship, industry, human-planner, model saving, intake, voice,
free-wording or reader accuracy claim follows from this harness.

The2026-11-02 revision in AMENDMENT.md supersedes the original tier-cap/budget
paragraphs above: registered cap physical−3; four native development workers;
real driver process-group deadlines; actual D264 adapter-v2 prepass at3.1.
The complete68trial study and strict17of32 release calibration remain explicit.

D270/D275 clarification in AMENDMENT.md fixes named standard to D265300/100,
seed0,1800s engine wall and the registered full engine-kwargs hash. The distinct
1200s outer child wall remains this evaluation's budget. Planning deadlines are
deadline_exceeded, retaining a checked incumbent only after fresh independent
audit; crashes, cleanup and audit failures remain errors. The authoritative
pipeline manifest is freeze/manifest-pipeline.json, verified with full_weights.


The2026-11-02 launch-preparation amendment supersedes the prior D264 reader and
complete68-trial runtime requirement: adopted D266 **swa-last-5 runs live on the
GPU**, with the v3 scoring mask/decoder, once serially per templated instruction.
Recorded expected answers remain development-only. D276's44 complete timing
rows across all8cohort/arm cells support a hash-pinned loaded-machine runtime
projection;17 supplemental baselines remain separate. Four workers/18enabled
cores, standard300/100/1800s engine wall,1200s outer child wall. Calibration
still requires17of32 fresh L0 baselines breaking the fixed physical-minus3 cap;
missing calibration cannot be waived by timing or launch config preparation.


The pre-freeze audit clarification makes vectorized mask E9 diagnostics visible
without treating a search heuristic as an additional hard rule. Completion uses
legal physical replay and all load boxes aboard; full checker L0, intended-rule
recheck and fresh independent audit remain required. Development calibration
reaudits original committed plans, no new search:20 binding L0 plans,5 no-plan
rows and7 unknown maps retained,20/32 meets the unchanged strict17/32 gate.
The sealed authorized-instance runner seam reuses only worker single-use-loaded,
byte-hash-verified instances; exact packet/model revalidation and ordinary
runner private-data fence remain, and the temporary assembly loader restores.
