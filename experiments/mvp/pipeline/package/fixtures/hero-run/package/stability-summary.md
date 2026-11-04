Half-ship section: 87 boxes planned in 6 of 12 bays. Other bays hold 168 fixed synthetic background boxes (2,664 t). Stability and strength include them. No ship can sail on this plan alone. Synthetic ships and cargo, simulated port. Full checker passed; local demo receipt. Stored plan, computed on 2026-11-02T04:36:38.695362+00:00, rechecked now. Stored synthetic development fixture; source provenance is recorded separately. Live reading: StowLM Reader swa-last-5. Pipeline: frozen. Checker coverage: 54 active, 8 inactive.

# Stability, strength and weight account

Departure scalars and sampled-condition margins come from checker-report.json. Stability during loading is not checked. List estimates are labelled projections in cargo-work.json.

| Component | Mass (t) |
| --- | ---: |
| Lightship (closed hatch covers included) | 2900.000000 |
| Planned section cargo aboard | 957.252890 |
| Fixed synthetic background cargo | 2663.810479 |
| Other fixed loads | 0.000000 |
| Ballast tanks | 921.002439 |
| Fuel, fresh water and other tanks | 712.882100 |
| Hatch covers left on quay | -0.000000 |
| **Total / checker displacement** | **8154.947908** |

Closure residual: 0.0 kg. Ballast departure allocation: 921 t. Tank contents are listed in tank-plan.csv; no cargo-work transfer programme is checked.

| POD | Boxes | Gross mass (t) |
| --- | ---: | ---: |
| MYPKG | 36 | 416.679875 |
| SGSIN | 51 | 540.573015 |
| Fixed background: POD not declared | 168 | 2663.810479 |

Cargo-work operational status: HOLD — chief officer heeling-tank programme required

Lashing is our approximation, with synthetic gear, because class rules aren't public.

## Checker statements

- Departure GM 1.12 m; minimum 0.15 m; synthetic securing-model maximum 3.28 m.
- Departure draught aft 6.02 m, forward 6.27 m; hull maximum 7.05 m, forward minimum 2.47 m; berth/channel limit unknown.
- Departure trim 0.25 m by the head (aft minus forward -0.25 m); signed model limits -1.08 to 1.08 m.
- Departure heel 0.001 degrees; absolute model limit 2.00 degrees.
- Departure displacement 8,154.948 t; model limits 2,900.000 to 9,713.353 t. Weight account including fixed background cargo is in stability-summary.md.
- Tight limit 1: approximate securing: max lashing ratio: 0.060 of limit spare; associated model limit 1.000 of limit (5.99% spare). Source: condition:straits:call-0:THLCH:departure:lashing.max_lashing_ratio.
- Tight limit 2: stack height at stack 01101H: 0.676 m spare; associated model limit 8.449 m (8.00% spare). Source: rules.stack_height_m.
- Tight limit 3: approximate securing: max fitting ratio: 0.085 of limit spare; associated model limit 1.000 of limit (8.46% spare). Source: condition:straits:call-0:THLCH:departure:lashing.max_fitting_ratio.
