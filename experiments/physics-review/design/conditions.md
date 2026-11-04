# Ship-design sanity pass: loading-manual conditions on the KCS feeders (2026-10-27)

Harness: `harness.py` (C1–C4 → production finisher → full `check`), `sweep.py` (stability-only sensitivity),
`report.py` → `conditions_tables.md` (every criterion, value vs limit, per condition). How to run and override:
`README.md`. Raw data: `results_before.json`, `sweep_results.json`. Rules and criteria were not changed. All
assumptions are labelled in `harness.py`.

**Verdict: 0 of 12 standard conditions pass.** The finisher returned `not_found` every time (4–5 exact evaluations
in 45 s; no `infeasible_proven`). A ship designed to today's rules (D2) would pass all of them. The failures come
from the **design values**, not from the planner.

## Before: failing criteria (value / limit)

| Ship | Condition (boxes hold/deck, Δ t) | Failing criteria |
|---|---|---|
| 500 | C1 14 t (80/216, 144 ashore by deck stack limit; 7362) | GM −0.80/0.15; all six B2.3 (GZmax 0.00/0.67); weather 0/1; trim 1.10/1.078; lashing not run (GM<0) |
| 500 | C2 22.5 t (80/144; 8258; **scantling unreachable**, max homogeneous cargo 5040 t vs 6509 t needed) | GM 0.05/0.15; B2.3 all six; weather 0/1 (roll T 69 s>20); cell guide 1.50/1 |
| 500 | C3 mix 15.7 t (80/252, 97 ashore; 8437) | GM 0.08/0.15; B2.3 all six; weather; visibility 300/233 m; cell guide 1.82/1 |
| 500 | C4 ballast arrival (3190) | B2.3 areas (e.g. GZmax 0.82/1.58); weather table (B/d 6.9>3.5); trim 1.87/1.078; fwd draught 1.84/2.47 |
| 1000 | C1 (168/448, 164 ashore; 13910) | GM −2.36; B2.3 all; weather; lashing not run |
| 1000 | C2 20 t (168/336; 15366; max homogeneous 10.08 kt vs 10.93 kt needed) | GM −1.40; B2.3 all; weather; trim 1.29/1.244; port draught |
| 1000 | C3 (168/500; 16085) | GM −1.10; B2.3 all; weather; trim 1.44/1.244; **SF 112 %** @ x=58.5 m (0.47 L); visibility 578/269 m |
| 1000 | C4 (5230) | B2.3 0–40/30–40/GZ/flood areas; weather table (B/d 6.6) |
| 1700 | C1 (400/688, 324 ashore; 23435) | GM −0.61; B2.3 all; weather; **SF 138 %** @ 76.4 m; visibility 351/344 m |
| 1700 | C2 24.9 t (400/344; 26729) | B2.3 area 0–flood 0.32/0.39; aft draught 9.63/9.52; **SF 165 %** @ 75.2 m; cell guide 1.77/1 |
| 1700 | C3 (400/833; 27866) | B2.3 five areas; weather table (roll T 26.5 s); aft draught 9.78/9.52; **SF 170 %**; visibility 1975/344 m; cell guide 1.79/1 |
| 1700 | C4 (8088) | weather table (B/d 8.2); trim 2.48/1.60; fwd draught 2.13/3.33 |

Probe with every ballast tank pressed full (464 / 731 / 1145 t): GM still −0.29 (500 C1), −1.75/−0.84/−0.56 (1000
C1/C2/C3) and −0.05 (1700 C1). **The present ballast cannot recover GM in any loaded condition on 500/1000.**

## Diagnosis (what drives each failure)

1. **Lightship VCG above the deck (500) / high (1000).** The TEU-only regression gives VCG 8.70 m = **1.055 D** on
   the 500 (D 8.25 m), 0.874 D on the 1000 and 0.777 D on the 1700. The regression sample was 13 geared ships
   with their own depths; for ungeared feeders 0.55–0.70 D is the plausible band (assumption, to be sourced by the
   lightship researcher). Lightship share of Δ ≈ 0.43 (500 C1), so each 1 m of VCG is worth 0.43 m of GM.
   Measured: 500 C1 GM goes from −0.80 to +0.47/+0.75/+1.00 at 0.70/0.62/0.55 D. Going from 8.70 to 0.62 D (5.1 m)
   is **+1.55 m of GM**. That is necessary but not sufficient.
2. **Too little of the capacity is under deck (cargo KG).** Hold share of cells: 18 % (500), 20 % (1000), 28 %
   (1700); feeders typically have ~40–50 % under deck (assumption). The KCS fit removed 64/168/288 hold cells, and
   500/1000 have only 2/3 hold tiers. Mean deck-box VCG is 15.3/19.7/22.4 m against KM 8.8/10.2/12.7 m at design
   draught. On the 1000, even VCG 0.55 D gives C1 GM −0.99 (−0.44 with WB full). Its KG excess is ~2.5 m and comes
   from the deck cargo, which no lightship or ballast change can fix. **Fix: more hold depth/tiers (D) or a
   stability-limited homogeneous intake (fewer deck tiers), as a real loading manual states.**
3. **Ballast capacity ~4–5 % of displacement and only amidships.** WB = 464/731/1145 t in 0.2–0.8 L double bottom
   strips (DB 1.05–1.25 m high, narrow). Typical feeders carry ~25–35 % of DWT (≈1600–2300 t for the 500; assumption).
   The hull-fitted full-width DB in `sweep.realistic_ballast` gives 821/1532/3027 t. It lifts pressed-full GM by
   +0.3 (500 C1), +0.6 (1000 C1) and +0.7 m (1700 C1) relative to today's tanks. There are no peak tanks (the aft
   peak would not fit the KCS stern in 0.03–0.08 L), so trim cannot be corrected: C4 trim 1.87/2.48 m vs
   1.078/1.60 limit and forward draught 1.84/2.13 vs 2.47/3.33 m. C4 needs ≈ +0.6 m fwd immersion
   (≈ +700–900 t forward). **Fix: fore-peak + full-width DB + side ballast; capacity ≥ ~30 % DWT.**
4. **Freeboard / reserve buoyancy (geometry) binds B2.3 even at high GM.** At the best sweep point (realistic DB,
   VCG 0.60 D, WB full), GM reaches 1.48/2.06 (500) and 1.43/2.30 m (1700) but B2.3 still fails:
   - 500: area 0–flood −0.18, GZ≥30 −0.11. Deck edge goes under at 9–12°: D−T = 1.19 m and coamings are only
     0.6 m.
   - 1700: only area 0–flood −0.08 / area 30–40 −0.02 fail, with φf 34–40° from the vents at D+4.5 m.

   **Fix: higher hatch coamings with weathertight covers counted (real feeders ~1.5–2 m), vents higher or with
   closures. For the 500, check the sourced depth: 8.25 m with T 7.06 m (B/D 2.33) looks like a brochure
   depth/draught pairing; D needs a check.**
5. **Weather-table applicability (A 2.3.5) acts as hidden GM and draught floors.** The checker fails closed on all
   of these (it doesn't extrapolate):
   - roll period T = 2CB/√GM < 20 s needs GM ≳ 0.6/0.75/1.05 m (500/1000/1700);
   - B/d < 3.5 needs mean draught ≥ 5.5/6.4/7.9 m;
   - KG/d−1 ≤ 0.5.

   The B/d floor alone makes every ballast/arrival condition fail: on the 500 that needs ≈ 4,000 t of ballast. No
   realistic ballast design reaches it. **Decision needed (not a design fix):** whether the checker should take φ1
   from MSC.1/Circ.1200-style alternatives outside the table range. Don't relax it silently.
6. **Shear amidships (1000 C3 112 %; 1700 C1/C2/C3 138/165/170 %, all at x ≈ 0.47 L).** The D22 shear-area profile
   p(x) = 0.35 + 0.65 sin²(2πx/L) falls to **0.35–0.37 of peak at midship**, where real side-shell shear area is about
   constant. Capacity there needs ×1.7 (1700) or ×1.12 (1000). Uniform scaling (`strength.shear_area_scale` 1.75)
   would pass, but the right fix is a profile with a midbody plateau (e.g. ≥ 0.8 between 0.3–0.7 L; assumption).
   BM peaks at 87 %, which is fine. The lightship LCG (0.459 L on 1700) vs LCB (0.485 L) adds midship shear.
7. **Trim at design draught.** The aft draught exceeds the max (load-line) draught on 1700 C2/C3 (9.63/9.78 vs
   9.52), and 1000 trims by the head (−1.44 m) because lightship LCG is 0.499 L vs LCB 0.485 L. Without peak tanks
   the finisher can't trim it out. **Fix: peak tanks, and/or put the regression LCG consistent with the actual hull
   LCB.**
8. **Securing (cell guides) and deck stacks.**
   - Hold cell-guide ratio is 1.5–1.8 for 22–25 t boxes. The section is hard-coded in `checker/lashing.py`
     (A = 0.003 m², W = 1e-4 m³, lever 0.5 m) and is too weak for the hold stack ratings (68–100 t). It needs
     about 1.8× section modulus.
   - The 500 deck 20' stack limit (45 t for 5 tiers) caps 14 t boxes at 3 tiers, which is why C1/C3 leave 97–144
     boxes ashore and why C2 can't reach scantling draught. Together with the +70 % DWT mismatch this says the hull
     is too big for its box capacity.
   - Visibility fails on C3 because the nearest-LCB fill raises forward deck stacks. That is a loading-plan issue
     (forward tier limits belong in the loading manual), not hull design.

## Sensitivity (stability-only; `sweep_results.json`, ~0.1–7 s per check)

C1 / C3 GM in m, ballast empty → pressed full, today's tanks:

| VCG | 500 C1 | 500 C3 | 1000 C1 | 1000 C3 | 1700 C1 | 1700 C3 |
|---|---|---|---|---|---|---|
| baseline (1.055/0.874/0.777 D) | −0.80→−0.29 | 0.08→0.50 | −2.36→−1.75 | −1.10→−0.56 | −0.61→−0.05 | 0.59→1.05 |
| 0.70 D | 0.47→0.90 | 1.19→1.55 | −1.62→−1.04 | −0.46→0.05 | −0.24→0.31 | 0.91→1.35 |
| 0.62 D | 0.75→1.17 | 1.43→1.79 | −1.28→−0.72 | −0.17→0.33 | 0.15→0.67 | 1.23→1.66 |
| 0.55 D | 1.00→1.40 | 1.65→1.99 | −0.99→−0.44 | 0.09→0.58 | 0.49→1.00 | 1.51→1.93 |

Realistic DB ballast (821/1532/3027 t) + 0.60 D, pressed full: 500 C1/C3 1.48/2.06, 1000 −0.12/0.87, 1700 1.43/2.30.
**No sweep point passes the full intact set.** The remaining failures are B2.3 areas tied to freeboard and φf (all
ships), weather on 500 C3, and trim/draught on 1000/1700. **The intact-stability pass/fail result doesn't change
anywhere in 0.55–0.70 D, so lightship VCG alone can't fix any ship.**

## Timings (single core)

- Full `check`: 3.6–21 s. Loaded conditions take 4–16 s; ballast/arrival conditions take 18–21 s (light-draught
  direct stability).
- `check_stability` alone: 0.1–7 s (the cache makes repeats fast; a cold exact check is ~3–5 s).
- Finisher: 45–59 s for a 45 s budget, 4–5 evaluations, always `not_found`.
- Vessel load from cache: 1.5–2.5 s.
- Whole C1–C4 fleet run: ~16 min. Sweep: ~6 min.

## For the "after" run

Write `after.json` with the sourced lightship values, a ballast design (e.g. `sweep.realistic_ballast` + fore
peak), `strength.shear_area_scale` or a new profile, and `max_deck_tiers`. Then run
`harness.py --overrides after.json --label after` and `report.py before after`. Changes that need code/params
work outside this harness:
- principal dimensions or hold tiers (fleet regeneration);
- coaming height and vent positions (params `envelope`; the stability geometry would pick these up through the
  override path, but `HullModel` hydrostatics would not, so do a full rebuild);
- the cell-guide section (lashing code constant);
- the A 2.3.5 applicability policy (a leader decision).
