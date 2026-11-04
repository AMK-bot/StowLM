# Functional app: live verification, 3 Nov 2026

Every row below is a fresh synthetic call made in the Functional app (`/planner?live=1`, `python -m app --functional`) on
this MacBook Pro (M5 Max), with live Muse Spark and Muse Voice Transcribe calls on the user's own key (loaded from the
environment, never written to any file here), the StowLM Reader on this laptop (adopted adapter `eff712e18a64…`, v3
schema mask, MLX) and Engine 0.13.0 through the frozen runner. Times are wall-clock seconds measured on this machine in
that run. Lifts are extra crane lifts (rehandles now and projected at later ports, hatch-cover lifts; D15). "Passes"
means a full-checker pass (L0) that also obeys every confirmed instruction. Synthetic ships and cargo, simulated port.
Spoken instructions are macOS text-to-speech clips (generated voice), uploaded as WAV.

| Call | Section | Cargo papers | Instruction | Engine (standard profile) | Result |
|---|---|---|---|---|---|
| `c20261103-212953-d39448` | feeder_500 tiny, 3 boxes, seed 1506098477 | Muse Spark, 1 booking, 0 questions, 45 s, $0.046 | spoken "Leave bay one empty on this call." → transcript "Leave Bay one empty on this call.." → reader "Nothing loads into bay 01." (1.80 s) → confirmed | first checked plan 41.8 s; whole run 44.3 s | **Passes the full checker** (L0), 5 lifts, 54 active rules, instruction rechecked: obeyed; signed package verifies offline (`verify_package`: passed). Evidence: `2026-11-03-tiny-d39448/` |
| `c20261103-213344-b842f8` | feeder_500 medium (half-ship section), 89 boxes, seed 22025512 | generated load list, not read by a model | none | first checked plan 76.1 s (76 lifts), improved to 66 at 137 s; whole run 157.0 s | **Passes the full checker** (L0), 66 lifts |
| `c20261103-211849-ff09d6` | feeder_500 small, 24 boxes, seed 585183298 | Muse Spark, 4 bookings at once, 0 questions, 73–115 s each, $0.338 | spoken "Keep the deck stacks to four tiers at most on this call." → "At most 4 tiers per stack on deck in every bay." | 6.5 s | No checked plan: the engine's relaxation found no room for the cargo under that cap (hold is 2 tiers). Not a pass |
| `c20261103-212438-199686` | feeder_500 small, 25 boxes, seed 1841589390 | Muse Spark, 5 bookings, $0.370, 1 by-design question (a 45P3 platform box the intake doesn't support) → generated load list chosen | typed "Keep the class 3 cargo at least one bay away from bay one." → "IMDG class 3 stays at least 1 bay from bay 01." | 0.7 s | Instruction unavailable: with every box aboard the limit can't be met (the 40-ft class 3 box always touches bay 01 in this two-bay section). Not a pass |
| `c20261103-202714-dc57c9` | feeder_500 medium, 87 boxes, seed 2097357861 | generated load list | spoken "Keep bay eleven empty on this call." → transcript "Keep Bay 11 11 empty on this call." (an ASR slip, left as heard) → "Nothing loads into bay 11." (2.36 s + 2.80 s cold load) | 1,538.8 s | Relaxed plan, L4: breaks `iacs.member_irs.container_securing` (our securing approximation, T2), 0 boxes ashore, 105 lifts; "Breaks a class or statutory limit: needs a person's sign-off". Not a pass |
| `c20261103-204517-ee9193` | feeder_500 medium, 86 boxes, seed 1435387473 | Muse Spark, 15 bookings, $0.77, 96 questions (84 from CSV papers whose columns intake couldn't bind; fixed: papers are now plain text) → generated load list | spoken "Leave bay nine empty on this call." → "Nothing loads into bay 09." (2.52 s + 2.72 s cold load) | 1,800.5 s (deadline) | No checked plan within the deadline. Not a pass |
| `c20261103-201307-d36912` | feeder_500 medium, 83 boxes | Muse Spark, 10 of 11 bookings ($0.67), then the old $2 launch cap stopped booking 11 (D359) | — | — | Stopped at intake |

Engine-only worker checks by fa-engine (no model; a hand-validated reading): two fresh tiny calls passed the full
checker, no instruction 5 lifts in 44.5 s, "close bay 1" 8 lifts in 43.0 s.

What this shows, and doesn't: the whole route runs live on fresh seeds: generator, Spark intake, Voice, the fine-tuned
reader with the planner's confirmation, Engine 0.13.0, the full checker, the ladder and a signed package. Two fresh calls
passed the full checker, one of them with a spoken instruction read by the reader. Medium calls with an instruction did
not pass within the 1,800 s deadline in these two tries (one relaxed L4 plan, one no plan); the held-out results
(13 of 18 binding voyages, D291) are the measured rate, not these anecdotes. Small sections are too short for most bay
and DG-distance instructions, and the app says so through the runner's own refusals.

Every seed above is labelled "Fresh seed: in no published map set" by the app's seed screen.

Spend for the build and these runs: $2.30 in total: Muse Spark $2.30 and Muse Voice Transcribe $0.00025 (39 settled calls
in the app's own ledgers, $2.186, plus one $0.115 call on the project ledger before the app had its own), inside the $5
allocation in `docs/compute.md`.
