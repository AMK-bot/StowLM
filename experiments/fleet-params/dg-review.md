# Synthetic DG authorization review

Read date: 2026-10-27. This is a parameter design review, not an issued
Document of Compliance (DoC), nor an approval of any real ship.

## Public basis

- [SOLAS II-2/19, Tables 19.1 and 19.3, and 19.4](https://www.imorules.com/solas/03_chapter_II-2.html)
  (public transcription, SOLAS 2024): the tables select construction/equipment
  requirements by cargo class and mode of carriage. They do **not** grant cargo
  permission by themselves. Paragraphs 19.3.4 and 19.3.5 address ventilation and
  flammable/toxic-liquid bilge arrangements. Table 19.3 note 16 prohibits class
  5.2 under deck. Paragraph 19.4 distinguishes an Administration's issued DoC
  from assumptions about equipment; classes 6.2 and 7 do not require this DoC.
- [IMO MSC.1/Circ.1266/Rev.1, Annex 1 and its appendix](https://puc.overheid.nl/nsi/doc/PUC_1949_14/2/)
  (official Netherlands Shipping Inspectorate repository of IMO instrument):
  the standard document uses space-specific cargo permissions and remains
  subject to substance-specific IMDG requirements. The appendix distinguishes
  dangerous-goods physical forms/flashpoint categories, so a simple class list
  is only the projection needed by the current Stack model.
- [Hong Kong Marine Department MSIN 80/2025, paragraphs 1-3](https://www.mardep.gov.hk/filemanager/en/share/msnote/pdf/msin2580.pdf):
  confirms the revised format supersedes MSC.1/Circ.1266. It updates references,
  rather than conferring vessel-specific authorization.
- [IMO MSC/Circ.1266, paragraphs 3-4 and 9](https://puc.overheid.nl/nsi/doc/PUC_1949_14/1/)
  (historical instrument in the same official repository): explicitly calls for
  the class-5.2 under-deck prohibition to appear in DoCs. This historical source
  is included for the explanation of the restriction, not as the current format.

## Recommended synthetic policy for all three feeders

APPROXIMATION: Identical equipment fit and authorization policy across the
three synthetic new builds; do not present these as surveyed anchor particulars
or statistically established "typical" feeder permissions.

| Space | `dg_classes_allowed` | Reason for synthetic restrictions |
| --- | --- | --- |
| Weather deck | `["1.4S", "2.1", "2.2", "2.3", "3", "4.1", "4.2", "4.3", "5.1", "5.2", "6.1", "8", "9"]` | Fire-main coverage, suitable separation from ignition/accommodation and emergency equipment assumed. No specialized explosives magazine: class 1 restricted to 1.4S. No radioactive/infectious acceptance program: exclude 6.2 and 7 as synthetic operating policy. |
| Each enclosed hold | `["2.2", "3", "4.1", "4.2", "4.3", "5.1", "6.1", "8", "9"]` | Fixed extinguishing/smoke detection, suitable ignition-protected electrical equipment, non-sparking mechanical extraction and protected/separable bilge drainage assumed. Do not assume toxic-gas or flammable-gas service: exclude 2.1/2.3. No explosives arrangements: exclude class 1. Class 5.2 excluded under deck. Exclude 6.2/7 by operating policy. |

APPROXIMATION: Permission covers only `packaged_in_closed_freight_container`:
IMDG-compliant packages (including pressure receptacles for authorized gases)
inside a closed CTU. No permission is recorded for unpackaged bulk, bulk
containers, portable tanks, tank containers or open CTUs. Although some SOLAS
ventilation notes treat a portable tank as a closed freight container, that is
not a reason to expand this synthetic operating authorization.

For authorized 3/6.1/8 the synthetic policy includes compliant packaged solid
or liquid forms as applicable, including flashpoint subcategories; each shipment
still has to satisfy its Dangerous Goods List entry, packaging instruction,
stowage/temperature restrictions, segregation and subsidiary risks. Class 4.1
and 5.1 liquids versus solids likewise need applicable equipment and shipment
checks. A class-list match alone must never clear the shipment.

APPROXIMATION: For the equipment declaration use at least six empty-hold air
changes/hour with upper/lower extraction as appropriate, avoiding dependence
on the two-air-change closed-CTU concession. Label this numeric equipment fit
with SOLAS 19.3.4.1 as its design basis, not a measured ship fact. Suitable water
supply, detection, extinguishing, PPE and machinery-space boundaries must also
be part of the synthetic equipment premise.

## Proposed keys and mapping

Use `dg_compliance` with an explicit `assumption` and `source`/`provenance`, then
`dg_compliance.spaces` entries containing:

- `id`, `on_deck`, `hold_id` (hold identifier; absent/empty for weather deck),
- `stack_ids` (enumerated existing Stack IDs, disjoint and covering every stack),
- `dg_classes_allowed` (the exact lists above),
- `packing_forms_allowed = ["packaged_in_closed_freight_container"]`,
- `assumption` (space-specific equipment/operating policy).

Assign every deck stack to the weather-deck record and every hold stack to
exactly one enclosed-hold record. If a synthetic hold is defined per 40-foot bay
pair, the two side hatch covers share a hold; a hatch cover is not a transverse
or longitudinal segregation bulkhead. Label the assumed bulkhead geometry
separately before using it for IMDG separation. The 2.2 loader can copy each
space's class list into all its Stack records and should reject duplicate or
unmapped stack assignments rather than defaulting to unrestricted cargo.

## Current checker limitations / handoff

`Stack.dg_classes_allowed` matches the primary class and **every subsidiary
risk** by exact string membership. Therefore use `"1.4S"`, not `"1"` or
`"1.4"`. The loader may preserve the structured table but the current Stack
field cannot encode permitted packing forms or flashpoint distinctions.
The generator/checker must separately enforce CTU/packing-form facts; widening
the class list is not enforcement of this restriction.

`rules_imdg.py` currently rejects Dangerous Goods List stowage categories
outside A-E with the explicit message that class-1 explosives arrangements are
unrepresented. Recording 1.4S deck authorization does not bypass that gate.
Keep the fail-closed check; supporting a class-1 shipment needs the downstream
IMDG owner to represent and verify its category/arrangement requirements.
Existing conditional ventilation, heat, segregation and shipment-fact checks
also remain applicable independently of the DoC table.
