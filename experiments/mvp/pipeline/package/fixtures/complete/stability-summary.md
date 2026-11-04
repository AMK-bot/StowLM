Half-ship section: 190 boxes planned in 8 of 14 bays. Other bays hold 268 fixed synthetic background boxes (3,856 t). Stability and strength include them. No ship can sail on this plan alone. Synthetic ships and cargo, simulated port. Full checker passed; local demo receipt. Stored plan; original computation date unrecorded; rechecked now. Stored synthetic development fixture; source provenance is recorded separately. Pipeline: not frozen yet. Checker coverage: 54 active, 8 inactive.

# Stability, strength and weight account

Departure scalars and sampled-condition margins come from checker-report.json. Stability during loading is not checked. List estimates are labelled projections in cargo-work.json.

| Component | Mass (t) |
| --- | ---: |
| Lightship (closed hatch covers included) | 5200.000000 |
| Planned section cargo aboard | 2001.809077 |
| Fixed synthetic background cargo | 3856.193350 |
| Other fixed loads | 0.000000 |
| Ballast tanks | 1551.753565 |
| Fuel, fresh water and other tanks | 954.528500 |
| Hatch covers left on quay | -0.000000 |
| **Total / checker displacement** | **13564.284492** |

Closure residual: 0.0 kg. Ballast unchanged from arrival: 1,552 t. Tank contents are listed in tank-plan.csv; no cargo-work transfer programme is checked.

| POD | Boxes | Gross mass (t) |
| --- | ---: | ---: |
| MYPKG | 61 | 668.595609 |
| SGSIN | 129 | 1333.213469 |
| Fixed background: POD not declared | 268 | 3856.193350 |

Cargo-work operational status: HOLD — chief officer heeling-tank programme required

Lashing is our approximation, with synthetic gear, because class rules aren't public.

## Checker statements

- Departure GM 2.54 m; minimum 0.15 m; synthetic securing-model maximum 3.96 m.
- Departure draught aft 7.47 m, forward 7.63 m; hull maximum 8.70 m, forward minimum 3.04 m; berth/channel limit unknown.
- Departure trim 0.16 m by the head (aft minus forward -0.16 m); signed model limits -1.24 to 1.24 m.
- Departure heel 0.006 degrees; absolute model limit 2.00 degrees.
- Departure displacement 13,564.284 t; model limits 5,200.000 to 16,211.274 t. Weight account including fixed background cargo is in stability-summary.md.
- Tight limit 1: stack height at stack 02504H: 0.028 m spare; associated model limit 5.820 m (0.48% spare). Source: rules.stack_height_m.
- Tight limit 2: forward draught: 1.066 m spare; associated model limit 8.700 m (12.25% spare). Source: condition:straits:call-0:THLCH:departure:stability.draught_fwd_m.
- Tight limit 3: aft draught: 1.230 m spare; associated model limit 8.700 m (14.13% spare). Source: condition:straits:call-0:THLCH:departure:stability.draught_aft_m.
