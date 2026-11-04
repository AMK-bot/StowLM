# Adopted reader, end to end (task 1.2)

Adopted: `swa-last-5` (`eff712e18a64…`), decoder `db55700d4de5…`, the sealed-scoring mask. Synthetic ships and cargo, simulated port. Development maps, not a sealed result.

## Reading

D211 sentences read correctly: **8 of 12**. These rows repeat a few sentences across ships, and they came from an earlier sealed reader set, so this is not a fresh accuracy sample.

Demo sentences with an expected answer: **82 of 83**; 6 have none to compare with.

Timing: {"model_load_s": 2.54, "first_sentence_s": 4.25, "cold_start_s": 6.8, "warm_median_s": 3.44, "warm_p90_s": 4.64, "warm_mean_s": 3.62}. Shared machine.

## Wrong readings

- d211 `max_tiers-05`: "No containers on deck in bay 2." expected `{"schema_version": "stowlm.reader/2", "constraint": {"type": "max_tiers", "bay": 2, "zone": "deck", "max_tiers": 0}}`, read `{"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 2}}`
- d211 `dg_keep_away-01`: "Class 3 — sorry, class 8 — one bay from bay 1 at minimum." expected `{"schema_version": "stowlm.reader/2", "constraint": {"type": "dg_keep_away", "imdg_class": "8", "min_bays": 1, "away_from": {"kind": "bay", "bay": 1}}}`, read `{"schema_version": "stowlm.reader/2", "refusal": {"refused": true, "code": "ambiguous", "reason": "The instruction is uncertain or ambiguous."}}`
- d211 `dg_keep_away-02`: "Class 3 — sorry, class 8 — one bay from bay 1 at minimum." expected `{"schema_version": "stowlm.reader/2", "constraint": {"type": "dg_keep_away", "imdg_class": "8", "min_bays": 1, "away_from": {"kind": "bay", "bay": 1}}}`, read `{"schema_version": "stowlm.reader/2", "refusal": {"refused": true, "code": "ambiguous", "reason": "The instruction is uncertain or ambiguous."}}`
- d211 `dg_keep_away-04`: "Class 3 — sorry, class 8 — one bay from bay 1 at minimum." expected `{"schema_version": "stowlm.reader/2", "constraint": {"type": "dg_keep_away", "imdg_class": "8", "min_bays": 1, "away_from": {"kind": "bay", "bay": 1}}}`, read `{"schema_version": "stowlm.reader/2", "refusal": {"refused": true, "code": "ambiguous", "reason": "The instruction is uncertain or ambiguous."}}`
- voice-clean `th-reader-031e722e98863ad8-clean`: "บอลดาดฟ้าของเบสิบให้ซ้อนตู้สินค้าได้ไม่เกินสามสามชั้น" expected `{"constraint": {"bay": 10, "max_tiers": 3, "type": "max_tiers", "zone": "deck"}, "schema_version": "stowlm.reader/2"}`, read `{"schema_version": "stowlm.reader/2", "refusal": {"refused": true, "code": "unsupported", "reason": "The instruction is outside the supported vessel or vocabulary."}}`

## Said, read, confirmed, replanned

12 of 12 runs finished. Full-checker passes through the runner: 8. Binding replans: 8. Twelve of D211's 20 cases, chosen to span all four rule types; one measured path, not a benchmark. "Before" is D211's stored Frontier 0.9 plan, so a change also reflects the newer engine.

| Case | Read right | Status | Lifts | 0.9 lifts | Rule broken before | Changed | Fresh checker | Rule breaks | Binding | Wall s |
|---|---|---|---:|---:|---|---|---|---:|---|---:|
| close_bay-01 | True | checked_plan | 8.0 | 8.0 | True | True | True | 0 | True | 56 |
| close_bay-03 | True | checked_plan | 8.0 | 8.0 | True | True | True | 0 | True | 57 |
| close_bay-05 | True | checked_plan | 6.0 | 6.0 | True | True | True | 0 | True | 57 |
| max_tiers-01 | True | checked_plan | 8.0 | 8.0 | True | True | True | 0 | True | 58 |
| max_tiers-03 | True | checked_plan | 8.0 | 8.0 | True | True | True | 0 | True | 81 |
| max_tiers-05 | False | needs_confirmation | — | 6.0 | — | — | — | — | — | 4 |
| dg_keep_away-01 | False | needs_confirmation | — | 4.0 | — | — | — | — | — | 4 |
| dg_keep_away-02 | False | needs_confirmation | — | 7.0 | — | — | — | — | — | 3 |
| dg_keep_away-04 | False | needs_confirmation | — | 12.0 | — | — | — | — | — | 3 |
| one_port_per_hatch-01 | True | checked_plan | 10.0 | 9.0 | True | True | True | 0 | True | 539 |
| one_port_per_hatch-04 | True | checked_plan | 15.0 | 15.0 | True | True | True | 0 | True | 315 |
| one_port_per_hatch-05 | True | checked_plan | 9.0 | 10.0 | True | True | True | 0 | True | 548 |

A planner comparing the reading with the registered meaning is simulated: `confirm` accepts only an exact match. Lift differences on these maps are search noise as much as rule cost (logic review 3, B1), so no cost claim follows.

p22-demo: show only readings in `readings/hero-readings.json` whose `correct` isn't false.
