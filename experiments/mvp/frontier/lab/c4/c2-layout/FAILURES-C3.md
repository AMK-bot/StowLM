# Cycle 3: all 13 failed ladder maps

The complete fixed cohorts contain 12 maps per rung. C3 f60 passed 8/12; f70 passed 3/12. All 24 had a complete replay-legal trial. Each failed score row has `status=no_candidates`: the method discarded all failed trial stows before returning to the runner. No failed map is proved infeasible.

Codes: **S** securing (`iacs.member_irs.container_securing`); **D** draft; **T** trim; **V** visibility; **E9** over-width/length OOG clearance; **W** weather; **A** containership second check. A saved rule ID does not identify which second-check metric failed: do not silently equate A with area-to-flood.

“Closest” means fewest distinct failing rule IDs, then lowest lifts, then trial order. It is only a diagnostic ranking. Full rule IDs, all checked trials, restricted-pool failures and result SHA-256 hashes are in [failures_c3.json](failures_c3.json).

| Map (D192 prefix omitted) | Cold trial rules / lifts | Closest checked rules / lifts | Search outcome |
|---|---|---|---|
| feeder_800-f60-high-s192200100 | E9,S,D,T,V / 173 | E9,S,D,T,V / 173 | no candidates; 19 restricted LP uncovered |
| feeder_800-f60-high-s192200101 | E9,S,D,T,V / 168 | E9,S,D,T,V / 168 | no candidates; 16 restricted LP uncovered; 2 timed rounds |
| feeder_800-f60-high-s192200102 | S,D,T,V / 164 | S,D,T,V / 164 | no candidates; 20 restricted LP uncovered |
| feeder_800-f60-low-s192200100 | E9,S,T,V / 139 | E9 / 103 | no candidates; 0 restricted LP uncovered; 3 timed rounds |
| feeder_500-f70-low-s192200000 | S,W,A,V / 111 | S / 133 | no candidates; 7 restricted LP uncovered; 10 cell screens |
| feeder_500-f70-low-s192200001 | S,A,V / 83 | S / 109 | no candidates; 6 restricted LP uncovered; 10 cell screens; 1 timed rounds |
| feeder_500-f70-low-s192200002 | S,A,V / 91 | S / 131 | no candidates; 5 restricted LP uncovered; 8 cell screens; 3 timed rounds |
| feeder_800-f70-high-s192200100 | E9,S,D,T,V / 202 | E9,S,D,T,V / 202 | no candidates; 18 restricted LP uncovered; 32 cell screens |
| feeder_800-f70-high-s192200101 | E9,S,T,V / 207 | E9,S,T,V / 207 | no candidates; 19 restricted LP uncovered; 35 cell screens |
| feeder_800-f70-high-s192200102 | S,D,T,V / 195 | S,D,T,V / 195 | no candidates; 19 restricted LP uncovered; 32 cell screens |
| feeder_800-f70-low-s192200100 | E9,S,T,V / 178 | E9,S,T,V / 178 | no candidates; 6 restricted LP uncovered; 49 cell screens; 6 timed rounds |
| feeder_800-f70-low-s192200101 | E9,S,T,V / 183 | E9,S,T,V / 183 | no candidates; 19 restricted LP uncovered; 35 cell screens |
| feeder_800-f70-low-s192200102 | S,V / 185 | S,V / 185 | no candidates; 2 restricted LP uncovered; 55 cell screens; 6 timed rounds |

## What this means for Cycle 4

The six f60/f70 feeder_800 high-mass cold trials all fail securing, trim and visibility; five also fail draft and four E9. Their later restricted masters fail to cover all boxes, so the recorded results do not establish whether moment-aware pricing can produce a legal complete alternative.

All three feeder_500 f70 low-mass cases reach checked candidates failing securing alone. Their cold stows additionally fail second-check/visibility (and one weather); scoring only scalar GM cannot diagnose the second check. The feeder_800 f60 low seed192200100 reaches a 103-lift candidate failing E9 alone: geometry repair remains a distinct construction issue.

The three feeder_800 f70 low-mass cases retain only cold checked stows, failing securing and visibility, with E9/trim on seeds100 and101. Later closure searches are incomplete or screened; none proves infeasibility.

C3 Benders establishes the actual binding second-check metric on its accepted D185 witness as `containership_area_0_flood_mrad`, arrival area margin 0.000158135 m·rad, while all securing rules pass. This transfers the correct diagnostic/search target, not a universal binding claim for every D192 map. See [BINDINGS.md](../../c3/c2-benders/BINDINGS.md).

V6 is a constructor built to pass, not a lift benchmark. C3’s four warm primary maps were chosen from V6-passing maps and are preselected as passable; the whole 12-map ladder cohorts above avoid that filter. Medium lift totals use the fixed-stow, no-later-loads D15 projection.

Reproduce with `PYTHONDONTWRITEBYTECODE=1 python3 experiments/mvp/frontier/lab/c4/c2-layout/build_manifest.py`. No solver or checker is run; source rows supply historical diagnoses.
