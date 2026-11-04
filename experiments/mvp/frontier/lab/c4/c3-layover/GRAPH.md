# Concrete arrival graphs and read-only tiny-row audit

Counts below come directly from the frozen TRAIN JSON and manifest. No solver,
checker, numerical search, or Python source change was made for this note.

| Object | Small 30%, seed 331401024 | Medium 30%, seed 331401048 |
| --- | ---: | ---: |
| Arrival box vertices | 20 | 72 |
| Retained arrival vertices | 19 | 71 |
| Due-now arrival vertices | 1 | 1 |
| New-load vertices | 28 | 96 |
| Current decision-cargo vertices, arrival + new | 48 | 168 |
| Other yard-box/access-blocker vertices | 24 | 84 |
| All container records | 72 | 252 |
| Physical stack pages, hold + deck | 16 = 4 + 12 | 64 = 28 + 36 |
| Nonempty arrival stack prefixes | 16 | 64 |
| Decision cells / occupied arrival cells | 68 / 20 | 240 / 72 |
| Residual cells before discharge | 48 | 168 |
| Residual cells after due-box removal, retained cargo restored | 49 | 169 |
| Hatch-cover vertices | 2 | 6 |
| Covers blocked by arrival deck cargo | 2 of 2 | 6 of 6 |
| Arrival deck boxes blocking covers | 14 | 40 |
| Arrival cargo mass, kg | 99,628.342 | 615,806.362 |
| Retained arrival mass, kg | 88,975.147 | 598,728.958 |
| Adjustable tank variables / local search directions | 18 / 10 | 18 / 10 |

The small graph has the fixed prefix
`QZXU0000457@0030102 → QZXU0000082@0030104` in hold page `00301H`:
the 10,653.195 kg bottom box is due now at THLCH; the 10,538.399 kg
upper box is retained for VNSGN. The arrow is a clearance dependency: the
upper box must move before the lower box can leave. Its cover `HC-002-S`
already has seven arrival deck boxes above it; `HC-002-P` has seven more.
Thus clearing the due box touches its retained upper box and the seven
retained deck blockers, even before choosing any new placement. The 28 new
boxes split into 20 for HKHKG and eight for VNSGN; all 19 retained arrivals
are for VNSGN. Yard access adds 24 non-load blockers across 16 yard stacks.

The medium graph similarly has
`QZXU0001535@0230102 → QZXU0000380@0230104` in hold page `02301H`:
17,077.404 kg due at THLCH below 16,717.641 kg retained for SGSIN.
The due cover `HC-022-S` has eight arrival deck blockers. Cover blocker
counts are `HC-002-P/S: 6/6`, `HC-010-P/S: 6/6`, `HC-022-P/S: 8/8`.
New boxes split into 59 for VNSGN and 37 for SGSIN; all 71 retained arrivals
are for SGSIN. There are 84 additional yard blockers across 51 yard stacks.

A page pattern assigns boxes above its retained prefix, consuming residual
tiers and mass. Cover-to-deck and cover-to-hold incidence links couple pages:
retaining later-port deck boxes can force projected restows when earlier-port
hold cargo leaves. Moving a prefix now may remove that crossing at a current
cost. In these inputs a yard relocation costs one lift, a direct restow shift
one, an off/on restow two, opening/replacing a cover two, and each projected
restowed box two. Later-port costs use the checker's fixed-stow D15 projection
with no later loads. Fixed synthetic vessel background remains constant
physics data, outside the decision-box graph.

## Ballast coupling

Both examples have identical tank geometry: nine mirrored P/S pairs, each
containing two adjustable mass variables. The pairs are `APT`, `DB-A`, `DB-F`,
`DB-M`, `FPT`, `FWD-DEEP`, `HEEL`, `WING-A`, and `WING-F`. Initial adjustable
ballast is 949,544.881 kg: the six `DB-A/F/M-P/S` tanks are full and the other
twelve tanks empty. Water density is 1,025 kg/m³.

The local LP uses ten directions in this eighteen-variable space: nine
common-fill directions, one for each mirrored pair, plus `HEEL-P` increase /
`HEEL-S` decrease. Common-fill directions move both fractions together and
preserve transverse symmetry from a symmetric recipe; the split direction
changes heel. Aft/forward pairs change longitudinal moments, and
low double-bottom versus higher wing/peak tanks change vertical moments and
free-surface effects. For scale, full `DB-M` has CG approximately
`(lcg, |tcg|, vcg) = (53.52, 4.22, 0.73) m`, full `HEEL` approximately
`(53.89, 8.53, 5.28) m`. Cargo pattern moment features couple the page graph
to these water variables. Ten local directions are a heuristic subspace;
failure there proves no whole-space ballast infeasibility.

## Completed tiny rows: attribution audit

Read-only audit of all 72 completed tiny arm rows under `results/cycle4/`:
fresh 0.9.0 passes 0/24; engine and method each pass 24/24. All 144 future-call
attribution flags on the 48 passing plans are true. Every passing row's
arrival-only current and future restow counts equal its corresponding total
restow parts in this tiny cohort; no missing attribution or mismatch was found.

| Target occupancy | Engine / method mean lifts | Engine arrival restows now/later | Method arrival restows now/later |
| --- | ---: | ---: | ---: |
| 30% | 7.833 / 5.000 | 2 / 0.5 | 1 / 0 |
| 50% | 12.500 / 8.000 | 4 / 3 | 4 / 0 |

All 24 passing engine rows nevertheless record native
`complete_replay_legal=false`, `replay_legal_stows=0`, and an empty archive.
For example, `train.layover.tiny.o30.s331401000` independently records
`passed=true`, `replay_complete=true`, 10 lifts and arrival restows 2 now / 2
later, while those native archive fields deny complete replay. This agrees
with the known `retain` action-ID mismatch for discharge/through actions;
the runner's independent gates determine the reported pass. This paragraph
audits stored JSON consistency; it is not a new full-checker execution.

Sources: [manifest](cohort/MANIFEST.json),
[small input](cohort/train.layover.small.o30.s331401024.json),
[medium input](cohort/train.layover.medium.o30.s331401048.json),
[example engine row](results/cycle4/engine/train.layover.tiny.o30.s331401000.json).
Frozen manifest SHA-256:
`3ad179492d2b7ab255c5e8974aa12eba01b1676abbd36057e29031af8f52f578`.
