# Saved stows: as-is tier classification

Historical saved reports only; no solving or new pass claim; not a cold baseline.

The fixed historical portfolio has 35 failed maps: L4: 20, L5: 8, T0/unknown: 7.

| Rung | Failed maps | L4 | L5 | T0 / unknown |
| --- | ---: | ---: | ---: | ---: |
| f60 | 4 | 4 | 0 | 0 |
| f70 | 8 | 7 | 0 | 1 |
| f80 | 12 | 7 | 3 | 2 |
| medium12 | 11 | 2 | 5 | 4 |

| Map | Level reached | Hard rules broken in selected saved report |
| --- | --- | --- |
| D192.medium-feeder_800-f60-high-s192200100 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f60-high-s192200101 | L4 | iacs.member_irs.container_securing |
| D192.medium-feeder_800-f60-high-s192200102 | L4 | iacs.member_irs.container_securing |
| D192.medium-feeder_800-f60-low-s192200100 | L4 | iacs.member_irs.container_securing |
| D192.medium-feeder_500-f70-low-s192200001 | L4 | iacs.member_irs.container_securing |
| D192.medium-feeder_500-f70-low-s192200002 | L4 | iacs.member_irs.container_securing |
| D192.medium-feeder_800-f70-high-s192200100 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f70-high-s192200101 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f70-high-s192200102 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f70-low-s192200100 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f70-low-s192200101 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f70-low-s192200102 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_500-f80-high-s192200000 | L5 | iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| D192.medium-feeder_500-f80-high-s192200001 | L5 | iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| D192.medium-feeder_500-f80-high-s192200002 | L5 | iacs.member_irs.container_securing, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| D192.medium-feeder_500-f80-low-s192200000 | L4 | iacs.member_irs.container_securing, iscode.loading.trim, solas.v22.visibility |
| D192.medium-feeder_500-f80-low-s192200001 | L4 | iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, solas.v22.visibility |
| D192.medium-feeder_500-f80-low-s192200002 | L4 | iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, iscode.loading.trim, solas.v22.visibility |
| D192.medium-feeder_800-f80-high-s192200100 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing |
| D192.medium-feeder_800-f80-high-s192200101 | L4 | iacs.member_irs.container_securing, iscode.loading.list |
| D192.medium-feeder_800-f80-high-s192200102 | L4 | iacs.member_irs.container_securing |
| D192.medium-feeder_800-f80-low-s192200100 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f80-low-s192200101 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f80-low-s192200102 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| random.medium.185100200.0.2 | L5 | iacs.member_irs.container_securing, iscode.loading.draft, solas.v22.visibility |
| random.medium.185100201.0.5 | L5 | iacs.member_irs.container_securing, iscode.loading.draft, solas.v22.visibility |
| sim.medium.185100210 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing, iscode.loading.draft, solas.v22.visibility |
| sim.medium.185100211 | L5 | iacs.member_irs.container_securing, iscode.loading.draft, solas.v22.visibility |
| random.medium.185100001.0.5 | L5 | iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| sim.medium.185100010 | L5 | iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| sim.medium.185100011 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| random.medium.185100100.0.2 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| random.medium.185100101.0.5 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing, iscode.loading.draft, solas.v22.visibility |
| sim.medium.185100110 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| sim.medium.185100111 | L4 | iacs.member_irs.container_securing |

Every saved full-checker report is included in AS_IS.json, including failures on maps that have another historical L0 witness. The per-map choice first minimises level, then the fixed within-level ranking. This is a retrospective witness table and reads warm historical material; runtime solving reads none of it.

Breach is the sum of absolute measured-minus-limit gaps over hard occurrences, in original units. No missing measured/limit pair becomes zero: its breach remains null and ranks after measured gaps. Each report has its exact content-addressed path and SHA-256, preserving all measured/limit values and locations without duplicating the 389 MB archive. Candidate plan paths are stem associations and can be ambiguous when historical paths were overwritten.

Only hard violations determine level. Soft and tolerated violations remain diagnostics, consistent with the unchanged checker definition of L0. T0, missing replay count, incomplete checker parts or inconsistent checker verdict make a report ineligible at every level.
