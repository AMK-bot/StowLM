# sim.small.40100034: missing geometric specials

Read-only diagnosis of the fixed quick input `experiments/mvp/corpus/val/small/feeder_500/simulated_yard/seed-040100034.json`, SHA256 `0bafad1b85e3c191d8ae08e218f30434df667805c34867406f92635a6d87b88a`. No solver, mask replay or checker was run for this diagnosis. The quick30-f300 row contains 36 completed CP assignments, all rejected in native sequencing; a failed sequencer order is not map infeasibility.

There is no DG cargo in this load list. The repeated final boxes have two geometric restrictions missing from the assignment CP:

* `QZXU0000077`, 40 feet, 2.896 m tall, 12,039.907 kg, class M, requests `away_from_heat`. All decision slots span the forward heated tanks in x and y. Forward heated tanks end at z=8.4399 m. The deck base is 10.4 m, giving only 1.9601 m vertical separation for a bottom-tier deck box. The catalogue requires 2.4 m; no port/instance override exists. Thus no bottom deck slot is legal for this box. Every hold slot is also within the heated tank envelope vertically or below the required gap. Require deck floor >=10.8399 m, equivalently at least one actual supporting tier here. A legal top-tier box must still obey all-lower weight order. Source: `checker/rules_special.py:special_violations`, `feasibility/_engine.py:_heat_many`, `rules/catalogue.toml:expert.e8.away_from_heat`.
* `QZXU0000009`, 40 feet, 2.896 m plus 0.5 m over-height, class M, has 0.25 m over-width on both sides. Adjacent transverse lanes have 2.5 m centre separation, while regular container width is 2.438 m. The OOG envelope therefore overlaps an ordinary adjacent-lane envelope by 0.188 m. When their actual vertical interiors overlap, `expert.e9.over_width_length` rejects the pair. No additional margin is needed: the default OOG clearance is zero and intersections remain prohibited. A cheap sufficient construction puts this box in outer row 06 and reserves adjacent row 04 empty (on both physical bay columns); alternatively enforce a vertical non-overlap disjunction against neighboring cargo. Its over-height also closes both supporting columns to cargo above. Source: `checker/rules_special.py:special_violations` and `feasibility/_engine.py:_e9_batch`.

These are not a proof that the two special boxes are incompatible with each other. They can occupy well-separated transverse rows. A native-checkable hand seed, **not yet validated**, is:

| Box | Slot |
|---|---|
| QZXU0000101 | 0020102 |
| QZXU0000082 | 0020104 |
| QZXU0000040 | 0020202 |
| QZXU0000020 | 0020204 |
| QZXU0000061 | 0020182 |
| QZXU0000077 | 0020184 |
| QZXU0000035 | 0020382 |
| QZXU0000098 | 0020282 |
| QZXU0000009 | 0020682 |
| QZXU0000117 | 0010582 |
| QZXU0000014 | 0030582 |
| QZXU0000056 | 0010584 |

The four hold long boxes occupy every hold cell, avoiding legacy E6 empty-hold opportunities for the heavy twenty on deck. A class-M long box supports the heat-booked class-M box at deck tier 84. The OOG box is isolated on positive outer row 06, with row 04 empty; the three twenties occupy negative outer row 05 and do not mix hold sizes. This seed is only a proposal: yard sequencing, mask completion screens, D55, full geometry, ballast and the full checker must still run. Any success must be attributed to this manually informed repair and checked independently.

Actionable CP changes are a heat floor inequality and conditional OOG envelope non-overlap rows, rather than further full-assignment no-goods that fail to identify either constraint. A final-state OOG pair cut can be sound for a fixed support geometry; a cut using only two box-slot choices must account for their variable support heights before it can exclude those choices globally.
