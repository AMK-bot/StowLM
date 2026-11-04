# D320 A — fresh held-out extension ext-1

Registered 2026-11-03T03:02:27.594695+00:00, before generation and before any extension map is run.
Decision: docs/decisions.md D320 A. This is a separate, prospectively registered
extension after 3.1 has been observed. Its exact label everywhere is:

**fresh held-out extension, run after the freeze; not the pre-registered sealed claim**

## Maps and seeds

Exactly 24 synthetic stage-A maps, three replicates per cell. Cell order is the
Cartesian product: ship (feeder_500, feeder_800), physical-region fill target
(60%, 70%), generator (random_yard, simulated_yard). Thus 12 maps per ship,
12 per fill, 12 per generator, eight cells with three maps each. Same public
cell recipe as pipeline/maps/PREREG.md and its frozen generator, with up to
195 load-list boxes, nominal 500–800 TEU, at most 70% regional physical fill.
The 195-box cap overrides proximity to target fill. Not real ship voyages.

Root seed = 306320000 + 100*zero-based cell index + replicate (0,1,2).
The exact seeds, in that order, are: 306320000, 306320001, 306320002, 306320100, 306320101, 306320102, 306320200, 306320201, 306320202, 306320300, 306320301, 306320302, 306320400, 306320401, 306320402, 306320500, 306320501, 306320502, 306320600, 306320601, 306320602, 306320700, 306320701, 306320702.
No parent root is shared across maps. The repo's frozen corpus producer and
maps/generate.py ordering/trim algorithm are reused: medium decision region,
laem_chabang_like, noise .5, max_attempts100, booking_slack0,
booking_mass_fraction None, n_boxes None, dense_20ft false, tiny_dg_enabled true,
mass multiplier1, C6 proportional-deficit interleaving with SHA256 namespace C6,
longest occupancy-bounded prefix and 195-box cap. Arrival ship empty, no
current discharges, forecasts or future planned load calls. No repair or redraw.
An unavailable generation, schema failure, envelope rejection or planner
failure is retained for its registered map; no replacement seed is permitted.

Before generation, audit these roots against every seed recorded in safe
metadata, seed indexes, request/cohort/set inventories and path names, including
external metadata for all forbidden sealed payload trees. After generation,
check every new instance's saved-byte and canonical-instance SHA256 against
recorded prior instance digests and the other extension maps. Publish exact
coverage, input hashes, any inaccessible metadata, and the closed-world limit:
we cannot inspect unrecorded seeds/digests or forbidden sealed payloads. A
collision or missing required external inventory blocks launch; it cannot be
explained away by this seed range. Never open an old sealed map. Generate only
these new maps, never solve or rank them during preparation. No held-out map is
a development timing pilot. The plumbing dry run uses a separate existing dev
map, explicitly excluded from the 24 and all result summaries.

## Arms and confirmation

Exactly 72 trials: pipeline, two-step and instruction on each of the same24maps.
No sensitivity/Proposer/chance/fitted-rule arm. Pipeline uses runner.run_packet,
registered standard profile seed0: budget300s, finalize100s, engine wall1800s;
outer planning wall1200s, independent audit wall300s. Same 3.1 standard recipe.
Two-step is labelled “a simple two-step planner, modelled from one field expert's
description”; constructor15s plus finalizer15s, hard outer180s, audit300s, as3.1.
No per-map tuning. Full-weight manifest v4 SHA256 acf2f275efa73f45c43709f4a52807dd480390e1497f085cab11d3604265bbec;
v3→v4 planning-code byte identity is published separately. The only allowed
in-process changes are orchestration, extension authorization and output/cache
path routing; no planner/checker/rule/profile algorithm changes. Packet standing
is the empty string; load list/yard overrides absent. All outputs, keys, logs,
cache copies and scratch remain under measurements/ext-1 or runs/ext-1.

Instruction allocation is exactly frozen sealed.instructions.public_facts,
select_rule and sentence_for: sort24maps by canonical public facts, map_id tie
break; rank modulo4 chooses close_bay, max_tiers, dg_keep_away,
one_port_per_hatch, exactly six each. max_tiers physical-minus3, same fixed
bay/rank selection and zone rule as3.1, no new calibration or tuning. DG chooses
the most numerous matching class on the load list (lexical tie break, fallback3
recording no_matching_cargo), min_bays1. Impossible/vacuous rules retained.
Live adopted swa-last-5 masked reader, exactly once per sentence, serially before
any planning, with adapter/decoder/domain hashes copied from3.1. Workers replay
hash-bound readings; no worker constructs a reader. GPU prepass waits for an
explicit leader GPU-ready marker; no GPU work during preparation. Confirmation
source sealed_expected_match; binding.confirm_brief must precede execution.
This measures exact expected-rule confirmation, not a human planner's approval,
free wording or reader out-of-distribution accuracy. Synthetic templated
sentences and planning step from a clean load list.

## Metrics and reporting

Primary descriptive outcomes: per-arm full-checker complete L0 passes out of
all24 registered maps, plus by ship (12each), with exact two-sided Clopper–Pearson
95% intervals. Relaxed L1–L5 plans never count as passes; report by level apart.
Out_of_envelope is an explicit column retained in the all-map denominator.
Reader failures, engine failures, audit errors, crashes, deadlines and not_run
are separate retained statuses. No outcome-based exclusion or replacement.
Full checker, physical replay/load completion, every instruction obeyed and a
fresh independent process audit are required for any pass. A clean wall expiry
is deadline_exceeded, no plan within the time budget; a checked incumbent can
count only after a fresh audit proves it was published before that deadline.
No raw result or unverified incumbent may be promoted to a pass.

Instruction primary number: binding obedient passes / maps whose fresh-audited
no-rule pipeline plan broke the intended rule, overall and per rule type, with
exact95% intervals. Retain already_obeyed and unavailable counterfactuals apart.
Report instructed L0 obedient pass count out of24 alongside it; intended-rule
recheck pass/fail/unavailable, reader/engine failures by type, and boxes in
different final physical cells. Reuse the same-map no-rule pipeline baseline.
Lifts, if retained for audit, are secondary diagnostics only: current-port and
projected later-port separated; signed instruction-minus-baseline change on
mutual L0 passes only, already-obeyed pairs separately. No new lift-saving claim.
Report all per-map rows/CSV, status/level, audit and freeze provenance.

Report ext-1 separately beside 3.1, never replace the original186rows or pool
partial interrupted attempts. A clearly labelled SECONDARY pooled descriptive
figure may combine the completed 3.1 envelope24 and ext-1 envelope24 (48 maps)
for per-arm L0 passes and the matching binding instruction numerator/denominator;
exact intervals are recomputed from summed numerators/denominators, with both
component counts and manifest epochs beside it. Same generator family, not new
voyage types, independent replication or a recovery of the original sealed
claim. If either constituent report is incomplete/unverified, pooled figure is
unavailable. No tiny/small maps, sensitivities or partial runs enter that pool.
No superiority test, fine-tuning benefit or real-planner comparison is inferred.

Wall times are operational logs only and are NOT quoted as benchmark results:
other work may run beside the extension. Capacity: at most4maps concurrently,
three arms serial per map, one numerical thread per child; wait for fresh
core-state.json age[0,120)s, enabled cores>=8, workers<=enabledcores-2, finite
load1<=45. Admission waits and prints reason; no false idle-fleet attestation.
Operational reservation3–4h plus potentially unbounded admission/contention;
this is a projection from3.1, not a measured ext-1 runtime or claim.

## Authorization, resume and integrity

This extension has its own controller lock, immutable config/map manifest and
run identity; it never changes or reuses the spent 3.1 global claim. Exactly one
attempt per map/arm and one live reading per instruction; exclusive, payload-
bound phase capabilities are consumed before opening a map. Resume keeps all
published/audited outcomes, permanently retains spent interrupted attempts as
explicit errors or verified pre-deadline incumbents, and schedules only phases
that were never spent. It cannot retry a failed timed trial. Partial prepass
readings are retained, never reparsed after an outcome; no planning starts until
the allocation and all readings are terminal and hash-bound. Concurrent launcher
is refused by a nonblocking controller lock. Validate exact v4 manifest and full
weights before first opening and on every phase; drift stops further openings,
spends no unstarted trial, and publishes a provisional population-complete report.
All maps opened are logged with saved-byte hashes. A report is written in finally.
The source remains frozen; registration fixes behavior before any ext-1 run.
