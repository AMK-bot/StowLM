# Saved-report diagnosis: pilot pass versus cold f70 near-misses

This is a JSON-only extraction, with **no new physics evaluations**. Sources are
the four saved full reports under `diagnostics/`, trial details under `trace/`,
and the machine-readable extraction in `physical_diagnosis.json`. The pass is
`D192.medium-feeder_500-f70-low-s192200000` (135 lifts); the three failures are
`...s192200001` (111, 107, 123). These are different TRAIN maps; their numerical
differences are descriptive evidence, not controlled causal estimates or bound
coefficients. All failures have securing, containership second-check area, and
visibility violations. No case below fails the scalar initial-GM threshold.

## Actual intact-area margins

The required area is recovered from **measured area minus the checker's signed
`containership_area_0_flood_mrad` margin**. It is not held constant between maps
or phases. Units throughout this table are m rad.

| Plan | Phase | Measured area | Required area | Margin |
| --- | --- | ---: | ---: | ---: |
| 135 pass | departure | 0.473520 | 0.296755 | +0.176765 |
| 135 pass | mid-voyage | 0.490470 | 0.304295 | +0.186175 |
| 135 pass | arrival | 0.460248 | 0.316911 | +0.143337 |
| 111 fail | departure | 0.296346 | 0.309796 | −0.013450 |
| 111 fail | mid-voyage | 0.302974 | 0.317868 | −0.014894 |
| 111 fail | arrival | 0.262864 | 0.331303 | −0.068440 |
| 107 fail | departure | 0.261918 | 0.311496 | −0.049578 |
| 107 fail | mid-voyage | 0.266261 | 0.319684 | −0.053423 |
| 107 fail | arrival | 0.226512 | 0.333215 | −0.106703 |
| 123 fail | departure | 0.263286 | 0.307894 | −0.044608 |
| 123 fail | mid-voyage | 0.267351 | 0.316227 | −0.048876 |
| 123 fail | arrival | 0.227463 | 0.329598 | −0.102135 |

Arrival is the tightest area phase in all four stows. Looking only at departure
or requiring GM above 0.15 m would miss the barrier.

## Loading and native securing

KG, FSC and GM are metres. `GM stability` is solid GM minus FSC from the intact
loading calculation. `GM native` and the ratios are from the separate native
securing report. They are not interchangeable. Full ratio vectors and roll
periods are retained in the JSON extraction; fitting and rod maxima are shown
here because they identify the immediate problem.

| Plan | Phase | KG | FSC | GM stability | GM native | Max fitting | Max rod |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 135 pass | departure | 6.799729 | 0.119975 | 1.723082 | 1.788963 | 0.827170 | 0.910877 |
| 135 pass | mid-voyage | 6.831194 | 0.124757 | 1.641789 | 1.724958 | 0.814465 | 0.901757 |
| 135 pass | arrival | 6.997444 | 0.129936 | 1.440232 | 1.511968 | 0.770970 | 0.870516 |
| 111 fail | departure | 7.320012 | 0.120029 | 1.221226 | 1.286285 | 2.568941 | 1.659980 |
| 111 fail | mid-voyage | 7.372237 | 0.124815 | 1.118768 | 1.200847 | 2.506506 | 1.629285 |
| 111 fail | arrival | 7.561039 | 0.129999 | 0.895054 | 0.964158 | 2.322340 | 1.538300 |
| 107 fail | departure | 7.410319 | 0.120029 | 1.129278 | 1.194651 | 2.501909 | 1.627022 |
| 107 fail | mid-voyage | 7.466146 | 0.124815 | 1.023214 | 1.105639 | 2.434697 | 1.593892 |
| 107 fail | arrival | 7.658848 | 0.129999 | 0.795464 | 0.865982 | 2.239052 | 1.496903 |
| 123 fail | departure | 7.405276 | 0.120029 | 1.101183 | 1.165978 | 2.480505 | 1.616481 |
| 123 fail | mid-voyage | 7.460901 | 0.124815 | 0.995536 | 1.077720 | 2.413115 | 1.583235 |
| 123 fail | arrival | 7.653385 | 0.129999 | 0.768347 | 0.839786 | 2.215885 | 1.485358 |

The failures have **lower**, not higher, native GM than the pass, while fitting
loads are about 2.2–2.6 times capacity. Hence the saved evidence does not support
"overfilled hold/high GM" as the current cold-smoke failure explanation.
Tall/heavy deck columns can fail securing even with softer roll. The same-map
107→111 descent lowers cargo moment and improves arrival area by 0.03635 m rad,
but slightly increases departure fitting (2.502→2.569). This is the local
stability/securing tradeoff, with both solutions still far outside a pass.

## Cargo moments and visibility

Trial details use the explicitly labelled **visibility expanded-envelope**
half-height convention; these are not intact-stability cargo KG. Cargo stays
aboard throughout the sampled departure/mid-voyage/arrival leg, so this cargo
moment is unchanged across phases while consumables and liquid corrections
change. The fallback's cargo moment was not recorded; it is left unknown.

| Plan | Cargo vertical moment (kg m) | Cargo envelope KG (m) | Cargo LCG (m) | Transverse moment (t m) | Maximum roof (m) | Settings/lane |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 135 pass | 10,497,969.04 | 9.111874 | 52.690732 | +47.995353 | 20.764 | caps (3,5,5), weight .08, hold reward 1; dive |
| 111 fail | 15,170,112.58 | 13.566922 | 51.357317 | −7.534264 | 24.575 | uncapped, weight .02, hold reward 20; descent |
| 107 fail | 15,925,599.76 | 14.242568 | 51.474464 | −52.884232 | 24.575 | uncapped, weight .02, hold reward 20; first-fit |
| 123 fail | unknown | unknown | unknown | unknown | unknown | plain coverage fallback |

The pass's blind distances are 114.84/112.07/110.90 m, versus the 232.8 m limit.
For 111 they are 563.58/521.38/497.59 m; for 107,
560.92/518.90/495.16 m; for 123, 510.16/472.48/450.50 m. Thus a ballast-only
local optimizer is the wrong first repair for these cold stows. Forward roof
geometry must change. All three also fail clear angular sectors: their minimum
clear gaps are below 5 degrees, whereas the pass has no cargo blind sectors.

## Count-bounded next schedule (search guidance only)

1. Keep one cheap uncapped plain construction solely as a complete replay
   fallback. Do not spend repeated native checks on nearly identical tall
   first-fit stows before a moment-priced restricted master has run. The
   inherited `first_fit` ranking uses heavy/hold preference and remaining mass
   room; it does **not** directly minimize the new `vertical_weight` objective.
   Its name `low_first_fit` in constructor metadata is therefore misleading.
2. Spend the three genuinely priced construction attempts on caps (3,4,5),
   (3,5,5), and (4,4,4), with positive vertical weights .04/.08/.04 and moderate
   hold rewards 2/4/2. Keep the first forward third capped; do not use uncapped
   decks as the only oracle-free branch. These are a fixed schedule for all
   cohort maps, not a claim that those caps are necessary.
3. Preserve a reference-motion oracle for at least two attempts, constructed
   with declared departure ballast, and an oracle-free **capped** attempt for
   diversity. A fitted oracle may overrestrict; it must not decide acceptance.
   Whole-column low/flat exchanges can cut overturning arms and roof height
   without relying on a universal GM floor.
4. Give the restricted master an explicit heuristic vertical-moment ceiling,
   for example a fixed fraction of the map's plain seed moment, and let its soft
   violation variable retain a complete fallback. Try two count-bounded ceiling
   levels instead of assuming a larger hold reward supplies the same guidance.
   A target inferred from another map's passing cargo KG is not a valid bound.
5. For each complete candidate, record all three actual area margins and native
   securing ratios. Prefer static-legal stows with positive minimum area slack
   and ratio near one. Use the native finisher only after visibility/static
   geometry passes and securing is near capacity. On area-only or area-plus-
   securing near-misses, optimize exact phase margins jointly; never fit a
   scalar-GM acceptance rule.

These changes would be a new method version and need a new measured arm. Keep
already frozen cohort results and source hashes intact. No performance claim
is made until a complete real replay and full unchanged checker succeeds.
