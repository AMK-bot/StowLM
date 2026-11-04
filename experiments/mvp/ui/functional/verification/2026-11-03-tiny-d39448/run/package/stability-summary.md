Half-ship section: 3 boxes planned in 2 of 12 bays. Other bays hold 284 fixed synthetic background boxes (4,737 t). Stability and strength include them. No ship can sail on this plan alone. Synthetic ships and cargo, simulated port. Full checker passed; local demo receipt. Pipeline: not frozen yet. Checker coverage: 54 active, 8 inactive.

# Stability, strength and weight account

Departure scalars and sampled-condition margins come from checker-report.json. Stability during loading is not checked. List estimates are labelled projections in cargo-work.json.

| Component | Mass (t) |
| --- | ---: |
| Lightship (closed hatch covers included) | 2900.000000 |
| Planned section cargo aboard | 41.414972 |
| Fixed synthetic background cargo | 4736.771658 |
| Other fixed loads | 0.000000 |
| Ballast tanks | 949.544881 |
| Fuel, fresh water and other tanks | 712.882100 |
| Hatch covers left on quay | -0.000000 |
| **Total / checker displacement** | **9340.613611** |

Closure residual: 0.0 kg. Ballast unchanged from arrival: 950 t. Tank contents are listed in tank-plan.csv; no cargo-work transfer programme is checked.

| POD | Boxes | Gross mass (t) |
| --- | ---: | ---: |
| SGSIN | 3 | 41.414972 |
| Fixed background: POD not declared | 284 | 4736.771658 |

Cargo-work operational status: No estimated 1-degree trigger; cargo-work stability remains unchecked

Lashing is our approximation, with synthetic gear, because class rules aren't public.

## Checker statements

- Departure GM 1.98 m; minimum 0.15 m; synthetic securing-model maximum 3.28 m.
- Departure draught aft 6.77 m, forward 6.92 m; hull maximum 7.05 m, forward minimum 2.47 m; berth/channel limit unknown.
- Departure trim 0.14 m by the head (aft minus forward -0.14 m); signed model limits -1.08 to 1.08 m.
- Departure heel 0.156 degrees; absolute model limit 2.00 degrees.
- Departure displacement 9,340.614 t; model limits 2,900.000 to 9,713.353 t. Weight account including fixed background cargo is in stability-summary.md.
- Tight limit 1: forward draught: 0.134 m spare; associated model limit 7.050 m (1.90% spare). Source: condition:straits:call-0:THLCH:departure:stability.draught_fwd_m.
- Tight limit 2: maximum displacement: 372.740 t spare; associated model limit 9,713.353 t (3.84% spare). Source: condition:straits:call-0:THLCH:departure:stability.displacement_maximum_kg.
- Tight limit 3: aft draught: 0.278 m spare; associated model limit 7.050 m (3.94% spare). Source: condition:straits:call-0:THLCH:departure:stability.draught_aft_m.
