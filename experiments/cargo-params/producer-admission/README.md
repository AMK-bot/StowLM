# Producer admission of DG boxes (plan 2.5, w2-cargo-c)

How many DG boxes the unmodified random-yard producer (rule freeze **on**, C2 off, tiny rung, `feeder_500`,
`laem_chabang_like`, blocking 0.3, D62 crossing share 0) requests and draws, and how many end up in an emitted
`Instance.load_list`. "Admitted" here means the box survived every producer gate (draw, adapter/IS Code screens, C1,
background acceptance). It is not proof of a full loading plan.

Files
- `producer_admission_v1.py`: the w2-l2-cargo audit (aggregate counters only). `results-v1-seed710.{json,log}` hold
  its finished run (seed 710, max 20 attempts, 22:52–22:57 EDT, 275.9 s, peak RSS 0.60 GB). The emitted instances stay
  in /tmp; their SHA256 values are in `results-v1-seed710.instances.sha256`.
- `producer_admission.py` (v2): the same producer, plus per-attempt drop attribution. A pass-through recorder wraps
  `screen_load_list` and the report's rejection dicts log their writes. No gate is changed.
  `run-v2-{stress,main}.{json,log}` are its runs, which were **blocked** (see below).

## Results, seed 710, max_attempts 20 (v1)

Each tiny attempt draws a whole candidate (main: 4 boxes; stress: 3 boxes). Only the accepted attempt's boxes are
emitted, so every box in a rejected attempt is dropped. Main used 12 attempts (1 emitted). dg_stress used 5 attempts
(1 emitted, plus 1 single-shipment C1a redraw).

| profile | class | UNs drawn | requested | drawn | admitted | where the dropped boxes went |
|---|---|---|---|---|---|---|
| main | 3 | 1263 | 1 | 1 | 0 | attempt 2: IS Code displacement screen or background gate* |
| main | 4.1 | 1333 | 1 | 1 | 0 | attempt 10: displacement or background* |
| main | 9 | 3480 | 1 | 1 | 0 | attempt 3: displacement or background* |
| stress | 1.4S | 0012 | 1 | 1 | 0 | attempt 1: **C1** (c1a+c1g+c1h); maybe also attempt 2 |
| stress | 2.1 | 1978 | 1 | 1 | 0 | attempt 3: displacement or background* |
| stress | 2.2 | 1006 | 1 | 1 | 0 | attempt 1: **C1**; maybe also attempt 2 |
| stress | 2.3 | 1017 ×2 | 2 | 2 | 0 | attempt 1: **C1**; attempt 3: displacement or background* |
| stress | 4.3 | 1402 ×2 | 2 | 2 | 1 | attempt 3: displacement or background* |
| stress | 6.1 | 2810 ×3 | 3 | 3 | 1 | attempt 4: displacement or background* |
| stress | 8 | 1789 ×3 | 2 | 3 (1 redraw) | 1 | attempts 2, 4: displacement or background* |

Totals: main had 3 DG boxes in 48 requested and admitted 4 boxes, none of them DG. Stress had 12 DG requested and
13 drawn, and admitted 3 (UN1402, UN2810, UN1789).

Attempt outcomes:
- Main: 10 × `iscode.loading.displacement`, 1 × background gate (`background.finisher.not_found`), 1 emitted.
- Stress: 1 × C1, 1 × displacement, 2 × background gate (`background.physics_input.changed`), 1 emitted.

\* v1 records rejection counters per run, not per attempt, so a rejected DG-carrying attempt can only be narrowed to
"displacement or background". v2 was written to remove that ambiguity.

By the leader's reason buckets: C1 screen = stress attempt 1 (1.4S, 2.2, 2.3). Background gate = 3 attempts in all.
Slot admission = 0. Other = `iscode.loading.displacement`, the main source of loss (11 of 15 rejected attempts).

The UN1333/UN1263 pair never appeared together in one candidate, so the pair was not exercised.
2.1/2.3/6.1 and 1.4S: drawn, but only 6.1 was admitted.

Contamination: during v1, other executives edited source files (`ballast_inport.py`, `fleet_manual_witness.py`,
`feasibility/_engine.py`, ...; see `source_changes`). Both stress background rejections are
`background.physics_input.changed`, an artefact of those edits, not a property of the cargo.

## v2 status: blocked by an external dependency

The 23:01 EDT runs were stress seeds 710–715, then main seeds 710–712. Every case failed during setup, before any
attempt, with `D64 feeder_500.C1 manual ballast witness requires requalification for changed tanks/parameters`.
Between v1 and v2, other work changed `params/fleet/feeder_500.toml`, the other fleet/port params, `solver/ballast*.py`,
`vessel/fleet*.py` and the checker in-port modules (w2-physwarm is requalifying). Once the D64 witness is requalified,
rerun:

    uv run python experiments/cargo-params/producer-admission/producer_admission.py \
      --profiles dg_stress feeder_export --seeds 710 711 712 713 714 715 --output <json>

## D70 (>= 10 instances per tested class): reachable?

- **Main set, not from tiny.** About 6 % of main boxes are DG (3/48), so a 4-box tiny instance carries about 0.25 DG
  boxes, and seed 710 admitted none. Across 75 tiny instances that is roughly 19 DG boxes spread over about 9 classes.
- **DG-stress panel, plausibly from small, unmeasured.** The stress profile is 100 % DG. Tiny gives 3 DG per instance,
  so 15 tiny instances give about 4.5 per class over 10 classes: not enough alone. The small rung (36 TEU against 6)
  should give about 6× more DG per instance. Then 15 small instances could put most classes in at least 10 instances,
  but only if admission is not class-biased.
- **Open risk:** in seed 710, 1.4S, 2.1, 2.2 and 2.3 went 0 for 5 into emitted instances, and the C1 rejection fell on
  the 1.4S/2.2/2.3 candidate. One seed cannot tell bias from chance. The v2 rerun (several seeds, per-attempt
  attribution, plus a small-rung stress pilot) is what decides it.
- Yield: 1/12 (main) and 1/5 (stress) attempts were emitted. Most losses are the IS Code displacement screen in
  random_yard/random_export, which is outside this fence.
