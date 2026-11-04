# Exact design-table corrections — 2026-10-27

Historical design-stage report: current layout/tanks/draught and verification are in [convergence-verification.md](convergence-verification.md).

Reviewed `experiments/physics-review/DESIGN.md`, `design/after.md`,
`design/sourced-values.md`, `design/conditions.md` and calculation source
`design/sourced_values.py`. No NOTES.md exists in the downloaded
hafen-hamburg, magicport or ecfr-46-cfr-42 folders; their MANIFESTs were read
first instead. All local references were read2026-10-27. No downloaded code
was executed; the reviewed local research calculation was imported read-only.

## Corrected reference identity and values

Public Port of Hamburg entry BF Philipp identifies IMO9123324 and gives
DWT6832t at7.05m, matching Arctic Trader's IMO and principal machinery:
<https://www.hafen-hamburg.de/en/vessels/bf-philipp/>.
MagicPort ARCTIC TRADER gives6832t and moulded depth9.2m:
<https://magicport.ai/vessels/container/arctic-trader-mmsi-304688000>.
Local files and hashes are recorded in their respective MANIFESTs. No open
reuse licence is stated; only numerical facts are used. MagicPort is a
third-party aggregator, with internal beam inconsistencies. Confidence**M**
is the design reviewer's assessment of DWT agreement plus physical consistency;
depth9.2m has one database source plus the preliminary freeboard check.
The conflicting owner-brochure3850t/8.25m remain disclosed discrepancies,
not authoritative corrected values. Neither database is a class certificate.

The explicitly requested new synthetic lightship design table is:

|Class|Mass kg|LCG m AP|KG m baseline|Confidence and method|
|---:|---:|---:|---:|---|
|500|2900000|49.6|6.624|MassM; CGsL, reviewer component/geometry estimates|
|1000|5200000|57.2|7.8|MassM/L; CGsL, reviewer estimates|
|1700|8074000|73.6|9.24|MassM; CGsL, reviewer estimates|

APPROXIMATION: values are exact synthetic design inputs selected from the
review's recommendations, not exact measured anchor quantities. The2025
TEU-only total mass/CG regressions are retired as the active source of these
values; their historical checks do not become sources for the newCGs.
Reviewer component coefficients were not all verified against primary
Watson/Kupras texts. Preserve that uncertainty; do not claim an empirically
verified KG formula. The longitudinal distribution must integrate to each
new mass and its explicitly rounded LCG, not0.46L exactly if that differs.

## Approximate Type B freeboard: reproducible raw calculation

Sources: public46CFR42.20-15(b)(1),-25,-30,-60(b)(2) lineI and-65,
captured via eCFR API for2026-01-01 in `data/raw/ecfr-46-cfr-42/`.
MANIFEST includes exact URL/date/hash. Official current depth/table pages
were also checked on2026-10-27:
<https://www.ecfr.gov/current/title-46/chapter-I/subchapter-E/part-42/subpart-42.20/section-42.20-30>,
<https://www.ecfr.gov/current/title-46/chapter-I/subchapter-E/part-42/subpart-42.20/section-42.20-15>.

Raw TypeB table subset, **length feet / freeboard inches**, verified directly
from XML:340/53.2,350/55.7,360/58.2,400/68.2,410/70.7,520/98.1,530/100.6.
Linearly interpolate; use exact1ft=.3048m and1in=.0254m conversions.

APPROXIMATION: use Lpp for load-line length, D for freeboard-deck depth,
actual sheer0, effective enclosed superstructure S=.2L and standard-height
forecastle/no-detached-bridge lineI. No deck-line/stringer/bow-height or
special TypeB reduction is evaluated. These geometry choices are assumptions,
not facts recovered from the offset hull or a complete assigned-freeboard
calculation. Cb≈.6494 means the >.68 multiplier in42.20-25 is inactive.

Reproduce the review's screening method with L,D in metres and ell=L/.3048:

```text
Ftab_mm = 25.4*linear_interpolate(TypeB,ell)
depth_mm = max(0,(D-L/15)*(L/.48 if L<120 else 250))
aft_in = ((.1ell+10)+3*(.0444ell+4.44)+3*(.0111ell+1.11))/8
fore_in = ((.2ell+20)+3*(.0888ell+8.88)+3*(.0222ell+2.22))/8
sheer_mm = 25.4*(aft_in+fore_in)/2*(.75-.2/2)
full_ded_in = 34+8*(ell-279)/(400-279) if ell<400 else 42
superstructure_ded_mm = 25.4*full_ded_in*.10
Fraw_m = (Ftab_mm+depth_mm+sheer_mm-superstructure_ded_mm)/1000
```

The metric depth expression reproduces the review's rounded metric constants;
it is an approximation to the captured imperial statutory expression, not
an exact unit conversion of that expression.

|Class|D−T m|Tabular mm|Depth mm|Sheer mm|Deduction mm|Fraw m|Raw screen|
|---:|---:|---:|---:|---:|---:|---:|---|
|500,D9.2|2.140|1438.113333|452.161111|374.274266|98.900386|2.165648324|Fails by.025648324m|
|1000|2.600|1784.155000|751.500000|419.231566|106.680000|2.848206566|Fails by.248206566m|
|1700|4.480|2523.073333|833.333333|515.560906|106.680000|3.765287573|Passes by.714712427m|

## Calibration recommendation and its honest limits

Dina Trader owner brochure PDFp1 quotes assigned freeboard2.609m:
<https://mmred.no/images/Dina_Trader_Brochure-2022.pdf>.
The raw review calculation2.848206566m is.239206566m above that datum.
Subtracting that observed bias **alone** yields2.609m, still9mm above the
synthetic rounded depth-minus-draught2.600m. An exacttable and strictraw
requirement cannot both pass; test tolerances cannot establish compliance.

If the requested deliverable is explicitly a calibrated approximate design
screen, use a declared constant250mm downward model correction, separated
as239.206566mm observed one-ship bias plus10.793434mm rounding/model allowance:

```text
Fscreen_m = max(0,Fraw_m-.250)
assert D-T >= Fscreen_m
```

This gives1.915648324/2.598206566/3.515287573m, margins
.224351676/.001793434/.964712427m. Store Fraw,observed bias,extra allowance,
Fscreen,margin and both raw/calibrated pass flags, each labelled. The1000
case has only1.793mm margin and no robust uncertainty allowance.

**Do not call this correction conservative or a statutory allowance.** The
underlying zero-sheer calculation is conservative in that individual respect;
subtracting a fitted bias is not established conservative across three hulls.
APPROXIMATION: a single partly open-top real ship calibrates an incomplete
screen for different closed-top synthetic designs. Full assigned freeboard,
bow-height and all relevant construction conditions remain unverified.
No operational draught or checker criterion should change merely because
the calibrated preliminary screen passes. A tolerance-based assertion may
report “consistent within approximate-model accuracy”, but cannot truthfully
report strictraw-rule compliance or replace the explicit calibration record.

No heavy tests were run. The small read-only calculation reproduced the
review numbers using `uv run --no-project --python 3.12` and local source XML.
README final counts/tank and design tables will be reconciled with the
parent's regenerated parameters rather than guessed in advance.


## Current generation contract supplied by parent

Final planned physical cells372/592/1604, with hold/deck156/216,256/336,
572/1032. Hold fractions41.94%/43.24%/35.66%. Declared nominal-class tolerance
is25.6%/40.8%/5.7%; these are actual capacity losses, not recovered nominal
slots. Three hold tiers plus five/six deck tiers could not mathematically
reach40%hold share before any KCS clipping;500/1000 therefore reduce deck
stacks to three tiers.500 also gains a third hold tier at depth9.2m.

Hull-following WB targets3100/4100/7000m³; HFO554/790/1500m³. Polygons and
quadrature are explicit, not an equivalent box. The2.1 `hull_sections` tank
consumer is now implemented in `src/stowage/vessel/hull_tanks.py` and reached
through `TankGeometry.from_params`. No legacy passing test result certifies
the new tank contract.
Final regeneration is complete; current verification outcomes are owned by
the parent in [design-verification.md](design-verification.md).

Read-back verification of the regenerated TOMLs with stdlibtomllib confirms
372/592/1604 cells, tolerance.256/.408/.057, depth9.2/11.3/14m and the exact
new lightship masses. Integrated WB/HFO totals match the stated targets to
numerical quadrature precision. Final regeneration fits the retained DO/FW
prisms and divides HFO equally between fore/aft pairs without altering totals.

|Class|Design displacement kg|Synthetic DWT kg|Reference DWT kg|
|---:|---:|---:|---:|
|500|9727130.878595788|6827130.878595788|6832000|
|1000|16211273.379058883|11011273.379058883|11150000|
|1700|27985049.660270333|19911049.660270333|21413000|

The raw/calibrated freeboard flags read back as false/true,false/true,true/true.
These demonstrate the documented approximation, not statutory certification.
Opening-edge elevations read from the final design contract are hold/ER:
13.7/15.2m,18.8/20.3m,21.5/23.0m.500 has revisedD9.2m without the extra
vent increase;1000/1700 raise both groups3m. The README now records this.

Final read-back:22tanks per ship =16WB+4HFO+DO+FW. Each HFO tank holds
138.5/197.5/375m³, giving equal aft/forward and port/starboard totals. Each
retained DO/FW prism has5.875096/10.154734/20.730474m³, respectively. These
small fitted consumable capacities remain synthetic and do not match brochure
endurance capacities. The finite-angle2.1 consumer hook has landed. The
final generator uses `TankGeometry.from_params` and production `sounding`
rows, independently cross-checking capacity, centroid and free-surface
inertia against its polygon method before storing values. API availability
and regeneration do not establish full-checker acceptance.

Stack-limit read-back and code review: `checker/rules.py` assigns
`gross_mass_kg / len(footprint)` to each supporting stack and uses the stored
`max_mass_40_kg` directly. A40/45ft box has two supporting cells. Thus both
load and whole40ft design allowance are halved once, with no second
allowance division in the checker. Final whole20/40ft ratingskg:500hold
68000/68000,hatch45000/60000;1000hold100000/140000,hatch60000/90000;
1700hold130000/160000,foremosthatch60000/90000,otherhatches70000/100000.
Half40ft allowances stored per support are34000/30000,70000/45000,
80000/45000/50000 respectively. Mixed stacks retain their separately
conservative `max_mass_kg`; lashing remains independently binding.

No heavy tests were run for this documentation finalization. The root owns
the fresh full run and [design-verification.md](design-verification.md). Earlier
runs/log outcomes are historical; final current results will be supplied there
by the parent. No current test pass or checker approval is asserted here.
