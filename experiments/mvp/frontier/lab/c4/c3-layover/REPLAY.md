# Saved method stows: replay diagnostics

**Diagnostics only. These results are not full-checker passes and are not certified upper bounds.**

For each of the 72 frozen TRAIN maps, select the saved raw method candidate with the lowest `search_lifts`, breaking ties by candidate index. Replay it through the ordinary move engine; require complete legal loading/discharge, retained arrival cargo aboard, and exact agreement between replay D11 and saved `search_lifts`. This includes maps whose separately finished method plan passed the primary benchmark; the raw candidate selected here can differ from that passed plan. The primary full-checker comparison remains in [TABLE.md](TABLE.md).

| Size | Target arrival occupancy | Complete replay / maps | Diagnostic mean lifts | Restow now/later | Arrival restow now/later | Future attribution unavailable |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| tiny | 30% | 12/12 | 5 | 1/0 | 1/0 | 0 |
| tiny | 50% | 12/12 | 8 | 4/0 | 4/0 | 0 |
| small | 30% | 9/12 | 81.778 | 38/29.778 | 38/24.667 | 0 |
| small | 50% | 9/12 | 92.333 | 63.778/20.222 | 63.778/19.556 | 0 |
| medium | 30% | 10/12 | 338.2 | 142/151.6 | 142/130.6 | 0 |
| medium | 50% | 9/12 | 450.444 | 238/181.778 | 238/180.667 | 0 |

Means include only maps with a saved, independently complete legal replay. A missing raw witness contributes no objective or restow estimate. Future lifts follow the fixed-stow D15 projection with no later loads. Arrival attribution uses the frozen runner's support/cover closure guard; if that guard disagrees with projected restow totals, attribution remains unavailable and the corresponding cell mean is left blank.

No planner, ballast finisher, numerical search, or full checker was invoked for this report. A legal cargo replay can still fail stability, securing, visibility, strength, or another full-checker rule. These diagnostic lifts establish no feasible-plan upper bound.

Comparator internal failed stows are unavailable from their native entry points. No comparator replay objective or restow count is imputed. Passing comparator plans remain part of the separate primary table.

All 216 primary rows, input hashes, cell assignments, and frozen runtime source maps were validated before replay. Per-map evidence, count parts, weighted parts, future-call counts and guarded arrival attribution are in [replay-diagnostics.json](results/replay-diagnostics.json).
