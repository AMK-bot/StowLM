# Lightship review — 2026-10-27

Scope: research only. No generator, TOML or README edits. Re-read reference
ship NOTES and current generator/TOMLs first. Current feeder_1000 lightship
is **7,677,534.589428349 kg**, from 0.35 times integrated design displacement;
the reported 25.8 kt does not match the inspected file. This coefficient is
an explicitly arbitrary heuristic, not a published empirical formula.

## Final recommendation: unified 2025 mass and CG regressions

Szelangiewicz & Żelazny(2025), *Statistical Analysis of the Weight and
Center-of-Gravity Position of an Empty Container Ship*, Applied Sciences
15(14),7886, §3.1:
<https://doi.org/10.3390/app15147886>,
<https://www.mdpi.com/2076-3417/15/14/7886>, read2026-10-27.

```text
Eq26: WLS[t] = 4.0726*TEU + 1150.9          R²=.8874
Eq29: xCG[m] = 6.9392*TEU^.3171            R²=.7139
Eq32: zCG[m] = 2.8088*TEU^.182             R²=.6820
```

**Origin visually verified:** Figure3 shows XCG measured forward from AP,
ZCG upward from baseline. Figure2 is weight shares; equation paragraph's
Figure2 reference is a typo. Diagram inspected directly in memory:
<https://pub.mdpi-res.com/applsci/applsci-15-07886/article_deploy/html/images/applsci-15-07886-g003.png>.

| TEU | Mass kg | LCG m forward AP | KG m |
|---:|---:|---:|---:|
|500|3187200|49.7912229298|8.7042901063|
|1000|5223500|62.0310888891|9.8746296536|
|1700|8074320|73.3980898527|10.8758327455|

APPROXIMATION: applicable size range270–3100TEU; mass dataset96ships,
CG dataset13ships. Geared aft-machinery/deckhouse bulb/transom designs
differ from our synthetic hulls. These are preliminary empirical estimates,
not surveyed anchor values. TCG=0 remains symmetry assumption. Longitudinal
distribution is still synthetic and must reproduce both total mass and LCG.
Above-deck KG is possible because lightship includes deckhouse/cranes.

## Earlier alternative: 2023 published feeder mass regression

Szelangiewicz, T. and Żelazny, K. (2023), *Reducing CO2 Emissions during the
Operation of Unmanned Transport Vessels with Diesel Engines*, Energies
16(12), 4818, §4.3, equation (8):
<https://www.mdpi.com/1996-1073/16/12/4818>,
DOI <https://doi.org/10.3390/en16124818>, read 2026-10-27.

```text
LDT(TEU,V) [tonnes] = 925.722 - 18.5126*V + 4.4492*TEU
```

V is operating speed in **knots**: the paper explicitly gives operating
speed V=17 knots in its worked example, with the same speed symbol in
the regressions. The manned-vessel regression is appropriate here; do not
use the paper's subsequent deductions for unmanned vessels. Its source
database covers ships built 2013–2022 and the first three feeder classes;
the stated application scope is 200–2800 TEU. Reported R²=0.9554 measures
fit to that database, not error bounds for these synthetic ships.

APPROXIMATION: apply this regression to the nominal synthetic TEU class
and anchor service speed. This is an empirical estimate, not a surveyed
mass or a promise that independently specified hull/deadweight matches.

| Synthetic class | TEU | V, knots | Estimated lightship, kg | Speed provenance |
|---|---:|---:|---:|---|
| feeder_500 | 500 | 15 | 2872633.0 | Arctic Trader brochure PDF p1, Speed/Consumption |
| feeder_1000 | 1000 | 18 | 5041695.2 | Dina Trader brochure PDF p1, Speed/Consumption |
| feeder_1700 | 1700 | 19.7 | 8124663.78 | Bangkokmax listing Vessel's Specification, Speed |

Speed sources read 2026-10-27:

* <https://mmred.no/images/filer/Arctic-Trader-Brochure-Rev-0-170226.pdf>
* <https://mmred.no/images/Dina_Trader_Brochure-2022.pdf>
* <https://www.eshiptrading.com/SS94778.html>

The local Bangkokmax HTML lines1001–1002 explicitly state Speed19.7KN;
its listing-identity caveat in reference-ships/NOTES.md remains applicable.
Store source knots as provenance metadata if useful, but physical speed
fields should be SI; convert knots to m/s before storing such a field and
convert back only at the published equation boundary.

## Independent numerical sanity checks

These are separate designs, not claims about our anchor ships. They support
the scale of the regression rather than replacing their weight estimates.

* Broker's own MARIA P sale offering, dated6 March2020:
  <https://www.ship-broker.eu/geared-mpp-container-feeder-2000-06-china/>,
  public particulars section, read2026-10-27. Nominal505TEU, lightweight3026t,
  DWT5550t at6.65m, LOA100.6m/B18.8m/D8.4m. Its particulars are qualified
  “about” and without guarantee. This is a geared multipurpose/container
  ship, so differences from a closed-top synthetic feeder are expected.
* Broker's own12 March2022 offerings:
  <https://www.ship-broker.eu/larger-size-container-ships/>,
  public1118TEU Kouan2007 section, read2026-10-27. LDT5197t,
  DWT13781t at8.51m, LOA147.87m/B23.25m. A second1118TEU Dayang2007
  offering lists LDT5260t and DWT13699t at8.50m. These are broker facts
  with the source's without-guarantee caveat, not verified inclining surveys.
* European Maritime Safety Agency (2026), *Onboard Carbon Capture and
  Storage Systems Study*, corrected5 March2026, AppendixF, page236 of291,
  Table0-23:
  <https://www.emsa.europa.eu/publications/download/8468/5634/23.html>,
  read2026-10-27. Its1700TEU representative case gives approximately
  8500t lightweight and25000t DWT. This is a published case-study estimate,
  not a measured named-ship value. Browser search extraction supplies
  these numbers; direct PDF opening timed out, so page image not inspected.

## Alternative published ratio (less specific than feeder regression)

MAN Energy Solutions, *Propulsion trends in container vessels*, p17,
“Lightweight”:
<https://www.man-es.com/docs/default-source/marine/tools/propulsion-trends-in-container-vessels.pdf?sfvrsn=c48bba16_17>,
read2026-10-27. The stated rule of thumb is lightweight approximately30%
of **deadweight**, varying with vessel type/class (ice class is discussed).
It is not35% of displacement. If applied self-consistently to a synthetic
integrated displacement Delta, then LWT=(0.30/1.30)*Delta. This is a
published broad containership approximation; the feeder-specific regression
above is preferable. Applying30% blindly to Arctic's printed3850t DWT
would yield1155t; do not claim that as a surveyed500TEU lightship.

## CG and longitudinal distribution: honest boundary

No surveyed lightship LCG, KG, TCG or longitudinal weight distribution was
found for Arctic Trader, Dina Trader or the Bangkokmax listing. Neither
the mass regression nor the MAN ratio supplies these quantities. A sourced
mass estimate does not turn the current0.55D KG or five segment fractions
into published values. Replace the old total CG with the2025 regressions
above; distribution details remain assumptions. An alternative is an
auditable component calculation.

Published method for that calculation: Biran, A. and López-Pulido, R.
(2014), *Ship Hydrostatics and Stability*, second edition, Chapter7,
§7.5 “Weight and Trim Calculations” summary, available in publisher excerpt:
<https://www.sciencedirect.com/topics/engineering/longitudinal-centre-of-buoyancy>,
read2026-10-27. Organize fixed masses into hull, outfit and machinery
groups, sum their first moments and divide by total mass to determine CG.
The method is published; the proposed synthetic group masses and positions
must still be labelled assumptions. For each group i record mass m_i,
centroid (x_i,y_i,z_i), and longitudinal extent/distribution, then

```text
LCG = sum(m_i*x_i)/sum(m_i)
TCG = sum(m_i*y_i)/sum(m_i)
KG  = sum(m_i*z_i)/sum(m_i)
```

APPROXIMATION: a defensible next implementation is to assign explicitly
labelled component masses to actual structure, secured hatches, machinery
inside the engine-room solid and deckhouse/outfit inside their declared
geometry, normalizing the unresolved residual structure to the empirical
total mass. Compute all CGs from those placements. Do not describe the
chosen component fractions or placements as measured or empirical unless
they receive their own verified source. Ensure secured hatch masses are
included exactly once and distribution integrates to the total and LCG.

Sree Krishna Prabu et al.(2020), *Study on the lightship characteristics of
merchant ships*, Brodogradnja71(3),37–70,
<https://doi.org/10.21278/brod71304>, publisher PDF
<https://brodogradnja.fsb.hr/wp-content/uploads/sites/25/2023/01/No3_4-10.pdf>,
read2026-10-27, is a promising source of **limiting** CG ranges. Those are
design limits derived from stability/loading models, not actual surveyed
CGs; no numerical limit from it has been adopted here. A generic
“KG=0.65D” found in handbook excerpts was not verified as total-lightship
KG for these feeders and is not recommended as a sourced replacement.

Final status: verified2025 empirical mass/LCG/KG formula and AP/baseline
origin are available. Detailed longitudinal mass distribution remains an
auditable synthetic approximation; constrain its first moment to the new
LCG instead of retaining the old five-segment first moment. No new
real-vessel surveyed CGs were discovered.
