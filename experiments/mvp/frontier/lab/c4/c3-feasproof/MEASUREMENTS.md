# Measured Cycle 4 result

Graph: fractional box-class to real bay/tier signatures, shared cargo moments and departure tanks across D57 phases, necessary securing/rod rows, and whole-domain finite-angle interval covers.

Joint sweep: 68.665 s for 23 maps; area sweep: 480.406 s for 69 map-phases. Fresh unchanged full checks: 335.780 s, 19/19 complete replay-legal passes on 12 input maps. No new constructed plans or passes. No paired 0.9+E9 comparison or mean-lift improvement claim.

In medium12, seed 185100000 is feasible with the retained 114-lift legal witness. Every other map is unknown, with a fractional outer witness. None is proved infeasible. Candidate area caps are extremely loose and uncertified for the default direct floating fallback; they impose no strict KG cut.

Exact per-phase and per-bay endpoints are in windows.json. The following are **surviving scalar outer windows**, not achieved safety conditions. Draft is mean upright draught in metres, deck mass is tonnes. The times include the joint and area stages for that map, excluding the separately timed control recheck.

| Map seed | Verdict | Departure KG m | Midpoint KG m | Arrival KG m | Draft dep / arrival m | Deck count | Deck tonnes | Time s |
|---|---|---:|---:|---:|---|---:|---:|---:|
| 185100200 | unknown | 7.768–14.280 | 7.937–14.280 | 8.114–14.280 | 7.886–9.520 / 7.886–9.520 | 215.000–404.000 | 2577.994–6344.136 | 28.73 |
| 185100201 | unknown | 7.637–14.280 | 7.805–14.280 | 7.979–14.280 | 7.886–9.520 / 7.886–9.520 | 212.000–410.000 | 2318.525–6319.718 | 26.42 |
| 185100210 | unknown | 7.822–14.280 | 7.992–14.280 | 8.171–14.280 | 7.886–9.520 / 7.886–9.520 | 213.500–403.000 | 2727.287–6621.416 | 27.49 |
| 185100211 | unknown | 7.666–14.280 | 7.834–14.280 | 8.009–14.280 | 7.886–9.520 / 7.886–9.520 | 203.000–398.000 | 2410.522–6021.292 | 27.32 |
| 185100000 | feasible, 114 lifts | 5.673–10.575 | 5.806–10.575 | 5.947–10.575 | 5.486–7.050 / 5.486–7.050 | 66.500–113.000 | 891.662–1719.348 | 21.68 |
| 185100001 | unknown | 5.675–10.575 | 5.806–10.575 | 5.944–10.575 | 5.486–7.050 / 5.486–7.050 | 64.500–116.000 | 959.917–1947.049 | 20.79 |
| 185100010 | unknown | 5.689–10.575 | 5.822–10.575 | 5.963–10.575 | 5.486–7.050 / 5.486–7.050 | 69.500–115.000 | 927.918–1695.495 | 20.48 |
| 185100011 | unknown | 5.755–10.575 | 5.890–10.575 | 6.034–10.575 | 5.486–7.050 / 5.486–7.050 | 72.500–113.000 | 996.003–1766.238 | 20.68 |
| 185100100 | unknown | 6.634–12.668 | 6.766–12.774 | 6.905–12.888 | 6.429–8.700 / 6.429–8.700 | 123.500–216.000 | 1546.581–3326.471 | 24.17 |
| 185100101 | unknown | 6.540–12.583 | 6.669–12.685 | 6.804–12.794 | 6.429–8.700 / 6.429–8.700 | 118.500–215.000 | 1529.045–3376.449 | 23.74 |
| 185100110 | unknown | 6.796–12.695 | 6.931–12.800 | 7.073–12.913 | 6.429–8.700 / 6.429–8.700 | 128.000–213.000 | 1757.325–3393.994 | 25.07 |
| 185100111 | unknown | 6.607–12.519 | 6.738–12.620 | 6.876–12.728 | 6.429–8.700 / 6.429–8.700 | 125.000–216.000 | 1499.301–3243.499 | 24.71 |

All 11 preselected passing f60/f70 input maps remain feasible with their 18 saved legal plans. This includes passing plans with 88, 162 boxes. These are validity controls, not a lift benchmark.

Verification: 92 exact matrix support certificates verified independently; 19/19 full-plan projections and scalar-window containment checks; 57/57 observed phase areas inside candidate bounds; all 69 area windows retain full free-trim and heel coverage. 25 unit tests passed with GMP, plus all 9 area tests passed on the standard Fraction backend. Source and artifact hashes pass final_audit.py. No strict area cap is installed.

Keep part: retain the joint windows, witnesses and regression checks; the area lane has not resolved a map or narrowed the KG windows. Next certify native direct arithmetic and contract the extreme free-trim tails before refining heel knots.

Assumptions: default proportional D57 consumables; one departure ballast state retained at sea; empty decision arrival/no future load scope; ordinary active rule catalogue. Unsupported variants must remain unknown.

Entry points: run_windows.py, then run_area.py; final_audit.py checks the retained artifacts. Papers and the outwardness argument are in NOTES.md.
