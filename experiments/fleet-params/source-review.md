# Fleet particulars source review

Read 2026-10-27. Read `data/raw/reference-ships/NOTES.md` and
`data/raw/larsen-pacino/NOTES.md` before the source texts. Existing local
downloads and MANIFEST provenance were inspected; no new download was required.
Public numerical facts below are anchors, not approved limits for synthetic ships.

## Numerical anchors

All dimensions below are metres; all masses quoted as tonnes must be multiplied
by 1000 for parameter files. Tank volumes are cubic metres unless stated.

* Arctic Trader owner brochure, PDF p1, February 2026:
  <https://mmred.no/images/filer/Arctic-Trader-Brochure-Rev-0-170226.pdf>.
  LOA 116.40, Lpp 107.80, B 19.2, D 8.25, maximum draught 7.06,
  DWT 3850 t. Two hatches each 25.2 × 16. Capacity 532 TEU, composed of
  134 hold + 398 deck 20-foot positions (alternative forty-foot intake 66 hold
  +192 deck; do not add alternative intakes). Hold stack 68 t for either 20/40;
  hatch stack 45/60 t respectively. Printed deck 40-foot weight is "1000t":
  apparent typo, do not transfer it. Reefer plugs 92 =36 hold +56 deck.
  Fuel oil 553, gas oil 103, ballast 2976, potable water 110.
  Forty-five-foot accommodation is listed but position mapping is unpublished.
* Dina Trader owner brochure, PDF p1, June 2022:
  <https://mmred.no/images/Dina_Trader_Brochure-2022.pdf>.
  LOA 134.44, Lpp 124.41, B 22.5, D 11.3, max draught 8.7,
  DWT 11150 t. Capacity 868 TEU =77 closed-hold +524 open-hold +267 deck;
  234 reefer connections. Four holds, two open and two closed; only holds 1/2
  have hatch covers. Hold heights: holds1/2 four 9.5-foot +one 8.5-foot;
  hold3 nine 8.5-foot or eight 9.5-foot; hold4 ten 8.5-foot or nine 9.5-foot.
  Hold dimensions as printed 12.4×13.30, 12.4×18.62, 26.95×18.62,
  26.95×18.62 (source labels both second and third entries #3, a typo).
  Tanks are MASS, not volume: IFO 770 t, gas oil100 t, ballast4000 t,
  potable120 t. Conversion requires explicit density assumptions.
  No stack weight limits or tank centroids are given.
* Vega Tau sheet (closed-top approximately 1000 TEU alternative anchor):
  <https://www.vega-reederei.de/pdf/tc_VEGA_TAU.pdf>.
  PDF p1: LOA139.1, Lpp129, B22.6, D11.8, max draught8.8,
  design8.0, DWT11797 t at8.8 and9600 t at8.0. Four holds.
  PDF p2: nominal960 TEU, four HC tiers below deck without losing positions;
  45-foot on hatches (not an exact position map). Alternative listed
  twenty/forty-foot arrangements give966 and964 TEU; source nominal is960.
  PDF p3: tanktop100/140 t per20/40-foot stack; hatches60/90 t except
  hatch1 45/60 t; deck above engine room80/120 t. These are loading-manual
  constraints with weight-order qualifications, not a universal lashing model.
  254 plugs =124 hold +130 deck; listed hold distribution18/54/52 FEU for
  holds2/3/4. PDF p6 tanks: HFO1018 t at980 kg/m³; gasoil217 t at860;
  ballast4671.8 t at1025; freshwater169 t at1000. PDF p5 explicitly states
  IFO preheating; fuel tanks should therefore be modelled as heat sources.
* Bangkokmax listing:
  <https://www.eshiptrading.com/SS94778.html>, “Vessel’s Specification”.
  LOA171.99, B27.6, D14, draught9.52, DWT21413 t,1700 TEU,
  1243 TEU homogeneous14 t,192 reefers,5 holds. Lpp, stack limits and tanks
  are absent. Identity discrepancy: URL SS94778 displays SS92210; preserve
  this caveat. The KEH “2000 TEU” design has2116 TEU and is a DIFFERENT
  vessel: its8.2 Bangkok/10.5 maximum draught is not a measured draught
  specification of this1700 TEU anchor.
* Bardu validation target:
  <https://na.swireshipping.com/documents/pdfs/MV%20Bardu%20Specifications.pdf?rvn=27>.
  Version16.03.2016 PDF p2: LOA208.9,Lpp196.9,B29.8,D16.4,
  scantling11.6/design10.1; TPC52.13 t/cm at11.6 (=52130 kg/cm).
  TPC is not displacement, despite source heading “Displacement (TPC)”.
  At1025 kg/m³ it implies waterplane area5085.85366 m² and
  Cwp≈0.86676 using Lpp196.9×B29.8. This is a derived check, not a
  hydrostatic table or transferable fleet coefficient.

## Larsen–Pacino benchmark

Source: Larsen & Pacino, “A heuristic and a benchmark for the stowage planning
problem” (2020); CC0 GitHub copy pinned to
`4b54b2100662f7d928af47b1139a914cde8f71e3`, read2026-10-27.
Base URL:
<https://raw.githubusercontent.com/maritimelab/Stowage-Planning-Benckmark/4b54b2100662f7d928af47b1139a914cde8f71e3/vessel_data/>.
Append `vessel_S.txt`, `vessel_M.txt`, or `vessel_L.txt`.

Headers explicitly identify fields; these are much larger vessels, not the
three feeder anchors. S/M/L header bay×stack×tier bounds are21×16×18,
24×17×19,24×22×21. Counted cell records3516/5132/7686; true reefer flags
770/951/1144. These counts are BENCHMARK RECORDS, not assumed feeder TEU
or physical plug counts. Do not blindly call their bay indices BAPLIE bays.

S lines187–219 give a representative stack: tcg−8.505 m,
above-deck maxHeight13.05 m, maxWeight20=67.5 t,maxWeight40=100.8 t,
vcg26.1 m; tier indices14,13,12,11,10. S lines217–224 give an adjacent
below-deck group maxHeight8.44,maxWeight20=72,maxWeight40=86.4,vcg15.66.
S ranges: deck heights13.05–20.88,20-foot masses67.5–108 t,
40-foot100.8–161.28 t; hold heights5.83–24.10,20-foot48–216 t,
40-foot57.6–259.2 t. Bay1 tank capacity2634 t atLCG107,TCG−8,
VCG-empty3/full11 is an example of tank-centroid data (lines19–24), but
does not give geometry or free-surface inertia. HydroPoints lines3–18 have
no hull geometry or large-angle stability data; SF/BM units remain unresolved.

## Recommended interpretation and approximations

APPROXIMATION: derive feeder layout topology, exact slot locations,45-foot
positions, hatch covers, reefer allocation, bridge and tank geometry synthetically,
inspired by the LP grouping structure and anchored to owner dimensions/capacity.
Do not assert these are original vessel plans.

APPROXIMATION: use explicitly stated new parametric hull, lightship distribution
and tank solids; no source supplies offsets, lightship CG/distribution, tank
FSM or watertight downflooding openings. These cannot be recovered from TEU.

APPROXIMATION: a closed-top approximately1000 TEU synthetic vessel may use
Dina principal dimensions while clearly declaring its closed-top layout a new
design. Vega Tau is a stronger physical precedent for its hatch/stack limits.
Do not silently seal Dina open holds when integrating GZ at large angles.

Model compatibility inspected in `src/stowage/model/vessel.py`: cells store
container FLOOR `vcg_base_m`, not container-centre VCG; add half actual box
height in consumers. Model TCG is positive PORT, while domain row odd means
STARBOARD (negative TCG). Stack40 mass is half-share across paired cells;
source total40-foot limits must be halved per cell-stack if mapped to that rule.
Do not prepopulate SF/BM or GM computed limits from these anchors.
