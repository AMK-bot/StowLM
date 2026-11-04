# Visibility and IRS securing — task 3.3

Both frozen signatures are preserved with implemented=True. The physics-review
follow-up supersedes the initial blanket rejection of mixed stacks,45-foot and
OOG cargo. Detailed source checks, proposed decision text and limitations are
in [review-followup.md](review-followup.md). The original verification JSON/logs
are historical results from before this follow-up, not current certification.

## Visibility

SOLAS V/22.1.1: transform actual planned-box/OOG/cover and explicit fixed-load silhouette geometry into the loaded
HullGeometry waterplane. For eye height E, obstacle height H and forward
coordinate X, sea intersection is E*X/(E-H). The maximum within±10 degrees,
measured forward of the bow, must be≤min(2LOA,500m). Analytical polygon clipping
avoids missing narrow obstructions. Trim/list come from numerical equilibrium,
with finite-angle liquid-centroid iteration; no full stability-criteria rerun.

V/22.1.2: blind sectors are computed from full box faces, including below-eye
cargo whose sea-surface shadows extend past the distance boundary. Individual
sectors≤10 degrees (≤5 in the forward view), total≤20, clear gaps≥5 degrees.
APPROXIMATION: the distance boundary extends .1.1 to .1.2, whose text does not
supply one. This follows the physics review's operational interpretation,
not an adopted IMO unified interpretation; maritime/flag review is required
for certified claims. Fixed forward structures are screened only if an explicit FixedLoad silhouette
is provided. Voyage consumption sweeps are absent; this checks the supplied
condition. Missing silhouettes supply no recoverable obstruction geometry.
The eye is centreline at bridge-front x with zero setback; eye_z is absolute.

## Motions and forces

IRS July2026 Main Rules Pt.3 Ch.1 §2.1.6 and Ch.5 §2 symbols,
§§2.1.2–2.1.3,2.2.3 supply:

- CB = moulded volume at scantling draught/(L*B*Tsc), integrated directly from
  HullGeometry. Loading draught still sets fT=max(.5,T/Tsc) and
  R=min(D/4+T/2,D/2).
- a0=min(.52,(1.58−.47CB)*(2.4/sqrt(L)+34/L−600/L²)).
- Tr=min(30,2.3*pi*(.35B)/sqrt(gGM)); phi=9000*(1.25−.025Tr)*fbk/((B+75)*pi)
  degrees, with fbk=1/1.2 with/without bilge keels and phi≥25° for B<32.2m.
- theta=min(20,1350*L^(−.94)*(1+(2.57/sqrt(gL))^1.2)) degrees,
  Tp=sqrt(2*pi*.6*(1+fT)*L/g). Unrestricted extreme-sea fp=fps=1.
- surge=.2*a0*g, sway=.3*a0*g, heave=.7*a0*g/sqrt(CB).
  Angular roll/pitch acceleration is angle-radians*(2*pi/period)².
  July's L<90m pitch formula is .38*theta-degrees/Tp²; the January2027
  amendment is not applied.
- al=.7*sqrt(surge²+max(L,110)/325*(g*sin(theta)+pitch*(z−R))²).
- at=sqrt(sway²+(g*sin(phi)+roll*(z−R))²).
- av=sqrt(heave²+(roll*y)²+(pitch*(x−.45L))²).

Roll/pitch gravity components already included in al/at are not added twice.
Cw is not an operand of these selected combined-acceleration formulas.

IRS-G-DES-19 July2025 §§3.3–3.6.6 supplies the mass floors2.5/3.5t,
30.5t synthetic rating ceiling, wind minima15.7/31.6kN transverse and6.4kN
longitudinal, CG at .45H, end stiffnesses3.7/15.7kN/mm, and component limits:
racking150/75kN transverse/longitudinal, compression848kN, casting tension250kN,
rod horizontal/vertical components150/225kN, and fitting demand≤85% of SWL.
Compression uses g+av and tension uses g. Longitudinal overturning per post is
whole-box force*height/(2*support span), with no transverse rod down-pull.
ISO exterior lengths are20/40/45ft=6.058/12.192/13.716m. A45-foot box's
synthetic support span remains40-foot while its wind envelope is45-foot.

## Support and assembly model

APPROXIMATION: no lashing bridge; rigid supports, zero pretension,28mm knob
rods and turnbuckles to tier2 (tier1 for a single box),2.438m horizontal reach,
locked twistlocks at each corner. LR Table14.9.4 effective assembly E=175GPa
(two tiers) or140GPa (one tier) includes turnbuckle/eyes. Published working
loads are rod/turnbuckle250kN and twistlock tension/shear/compression
250/210/1000kN; these are not breaking loads. This sourced synthetic selection
is not a certified combination of particular manufacturer products.

One node represents one physical box with two transverse end frames. For
40/45-over-two-20 on deck, only the two20-foot outer end frames receive the
upper long-box corner reactions. Inner20-foot frames remain independent.
Longitudinal reactions propagate down the support graph and preserve vertical
force, horizontal force and pitch moment. Supports must be paired, contiguous
and at equal actual height. The IRS20-over-long prohibition and special
consideration for mixed-length holds remain hard failures.45-foot and OOG
containers are evaluated against labelled standard-frame surrogate ratings.

APPROXIMATION: ISO corner adapters absorb nominal cell-coordinate spacing;
independent transverse spring chains omit side-wall coupling. OOG horizontal
CG/wind centres follow the expanded envelope; static eccentricity enters post
loads. No wind shielding is credited; wind=max(table,1kPa*expanded area).
Longitudinal wind and simultaneous fitting shear form conservative envelopes.
The loading-condition cargo CG is half-height; IRS forces use .45 expandedH.
Initial GM uses loaded-waterplane KB+BM−KG−tangent FSC, not a finite-angle
GZ derivative. Post shortening, support deformation, clearances, green seas
and whipping remain unrepresented. Hull/tank solids retain upstream labels.

Holds follow§3.6.4: each horizontal box force is shared by four fittings;
vertical load accumulates through four posts. The two fitting loads sharing
an interface bracket are added for guide stress, while each individual fitting
is checked against its own SWL. APPROXIMATION: continuous steel guides,
0.5m unsupported brackets, area .003m², section modulus .00025m³, material k=1;
normal/shear/vonMises limits200/100/175MPa per§3.6.6.2.

## Verification

Current follow-up command (numerical libraries limited to one thread):
`uv run pytest -q tests/checker/test_visibility* tests/checker/test_lashing*`
passes46 cases:21 visibility and25 lashing. Independent hand checks include
six-tier elastic compatibility, whole-box longitudinal /2L overturning,
scantling CB, mixed outer/inner corner reactions and global force/moment
conservation. Other regressions cover45-foot/OOG pass/fail, lower-than-eye
blind sectors, assembly stiffness, working capacities, bracket vs fitting
loads, invalid supports, and numerical HullGeometry loading equilibrium.
Frozen-interface snapshots also pass. No heavy job was needed in the follow-up.

The original complete-suite driver remains `verify.py`; it reuses full-fleet
assembly tests for two-box checker integration and records JSON/log evidence.
Historical complete-suite outcome:1238 passed,7 skipped,8 failed,8 errors;
short failures passed on a fresh rerun, while linear validity-band setup errors
remained open. A new full suite was not requested or run for this follow-up.

CHECKER_VERSION is owned by4.1 and outside this order's scope fence; route its
required bump to that owner.

Ship-design follow-up: guide defaults are W=2.50e-4m³, bracketspacing0.5m
and area0.003m²; see [guide-design.md](guide-design.md). These replace the
initial section only; GM, gear ratings and IRS stress allowables are unchanged.

D29 background follow-up: structured FixedLoad mass/CG contributes once to
loading equilibrium. Every explicit occupied silhouette uses the same trimmed
visibility pipeline as planned cargo; no mass is added for geometry. Background
stacks without per-box/support/securing inputs now fail securing closed; C3
capacity evidence alone is insufficient. Unknown fixed-load silhouettes also
fail visibility closed. Explicit submerged envelopes can establish no
obstruction in the evaluated condition. Planned boxes retain full wind exposure
(no shelter credit). See [background-silhouettes.md](background-silhouettes.md)
and the current [fix log](../review/fixes/fix-t3-vislash.md).


D42 consumer follow-up: complete per-box background cargo now enters one joint
securing/support graph and individual visibility envelopes, at the evaluated GM.
Cargo aggregate silhouettes are not used as substitutes. Aggregate mass enters
equilibrium once. Unknown classification or missing/inconsistent box/support
evidence fails closed. Non_cargo needs no securing, but visibility still requires
and checks its declared silhouette. See the fix log's D42 step and the current
contract at the top of background-silhouettes.md.
