# KCS independent large-angle review — 28 October 2026

**Follow-up:** [Matching-envelope and feeder results](FOLLOWUP.md) supersede
the interpretation of the mismatched 16 m/19 m comparison below. Published
IGES upper geometry reproduces all 14 KN/GZ ordinates at20–50° within the
diagnostic L5 bars. Production inputs remain unchanged; feeder-specific
sensitivities and the historical500 arrival failure are reported separately.

**Verdict: public large-angle sources exist; external KCS accuracy is NOT validated.** The closest explicit KN/GZ reference agrees with our unscaled loft through 20° but disagrees materially above that range. An independent integration method verifies the numerical calculation of our **declared 16 m sealed envelope** to about one micrometre. It does not validate the missing real-KCS upper hull. No `src/` changes were made.

## Best explicit KN/GZ reference and comparison

[Keramati et al., arXiv:2610.12870v1, 9 October 2026](https://arxiv.org/abs/2610.12870v1), §3.3.1, Table 5 and Figure 8, provides both KCS cross curves KN and GZ at KG 13 m. This is a **preprint**; journal publication was not independently verified. Its as-meshed KCS volume is 51,765 m³ at draft 10.8 m, KM 14.93 m; hulls are truncated at depth 19 m. The source says “upright trim.” Its archive contains no numerical curve table, hull mesh/offsets or stability solver for reproduction.

**ASSUMPTION:** interpret upright trim as fixed zero trim throughout the heel curve. Compare at the source volume, KG and trim. We also computed free-trim KN/GZ and recorded them separately. The maximum free/fixed-trim GZ difference over these samples is 0.027 m, much smaller than the large-angle disagreement.

Our calculation retains the original 230 m × 32.2 m source geometry, sealed at its supplied 16 m cutoff, with no coamings or superstructure. At volume 51,765 m³ it floats at 10.77071 m upright draft, KM 14.94703 m; no parameter was fitted. These small upright differences do not establish that the large-angle envelopes match.

| Heel ° | Published KN m | Our KN m | Published GZ m | Our GZ m |
|---:|---:|---:|---:|---:|
| 10 | 2.600 | 2.609 | 0.349 | 0.351 |
| 20 | 5.213 | 5.210 | 0.773 | 0.764 |
| 30 | 7.599 | 7.226 | 1.113 | 0.726 |
| 40 | 9.257 | 8.566 | 0.903 | 0.210 |
| 50 | 10.251 | 9.379 | 0.289 | -0.580 |
| 57.5 | 10.641 | 9.699 | -0.324 | -1.265 |

Full range: **22 angles, 5–57.5°**, two quantities per angle. Fourteen of 44 ordinates fall within the diagnostic L5 bars; all 14 are at or below 20°. Above 25°, every disagreement also exceeds the bar plus our graphical reading allowance. Maximum errors: KN 0.943 m, GZ 0.942 m. No extrapolation is included.

**APPROXIMATION:** source ordinates were digitized from the embedded 2549 × 1155 pixel figure. Assigned reading allowances are ±0.04 m KN, ±0.01 m GZ; these are conservative graphical allowances, not bounds on the authors' method or interpolation error. GZ read independently from the right panel is consistent with KN−13 sin(heel) from the left to 0.0141 m over the retained samples.

[Comparison plot](comparison.png) | [SVG](comparison.svg) | [All numerical results](comparison.csv) | [Conditions, hashes and residuals](comparison.json)

## Other independent published evidence

[Marlantes, Kim and Hurt, JMSE 2022, 10, 41](https://doi.org/10.3390/jmse10010041), Tables 23–24/Figure 22, is peer reviewed and explicitly supplies KG 13.674 m, volume 54,148 m³ and fore/aft draft 10.8 m. It uses depth 17.2 m, unlike both canonical KCS depth 19 m and our 16 m closure. At 10.8 m our volume is 51,944.99 m³, **4.07% below** that table. We cannot match both stated draft and volume with these offsets. Comparing at its volume requires our draft 11.15495 m. Source LCG and heeled trim policy are unspecified; we assumed our upright LCB and free trim. The linked author geometry/run-file page returned HTTP 404.

| Heel ° | ABS GZ m | CSI GZ m | Our GZ m at source volume/KG |
|---:|---:|---:|---:|
| 10 | 0.242 | 0.236 | 0.234 |
| 30 | 0.661 | 0.740 | 0.224 |
| 40 | 0.279 | 0.372 | -0.423 |
| 50 | -0.400 | -0.369 | -1.283 |

The ABS/CSI numerical references themselves differ by about 0.107 m at 35°. They corroborate the large-angle disagreement, but are not an identical-envelope approval reference. Their published vector points were extracted directly, with ±0.02/±0.01 m graphical allowances respectively; no source curve was extrapolated to 60°.

[Trang et al., JMSE 2025, 13, 412](https://doi.org/10.3390/jmse13030412), Figure 28, supplies another bare-KCS GZ curve. Table 5 gives AQWA volume 51,906 m³, KG 14.38 m and GM 0.599 m. However the drawn curve's initial tangent is 1.14663 m/rad. The figure does not repeat KG: loading is ambiguous, or the graphic/table differ. Both tabulated loading interpretations were calculated and retained as **conditional diagnostics**, not matched-load validation. A replacement KG was not fitted.

## IACS L5 interpretation

[IACS UR L5 Rev.4, June 2020](https://iacs.org.uk/resolutions/unified-requirements/ur-l/ur-l5-rev4-cln), §5.2 Table 1, gives KN ±0.05 m; GZ the greater of ±5% and ±0.05 m; areas the greater of ±5% and ±0.0012 m·rad. The paired tolerance uses the greater value. L5 requires identical input information. Our figures use these bars **diagnostically**, with absolute reference magnitude for percentage errors. The mismatched envelopes and graphical source data prevent a formal L5 approval claim. Areas are not claimed validated by sparse digitized ordinates.

## Genuinely independent same-envelope check

[Method and full tables](independent-method.md) | [Oracle results](oracle-results.json)

`independent_geometry.py` separately parses the pinned raw offsets and lofts sections. Production clips polygons and sums their boundary moments. The oracle rotates the section and analytically integrates polynomial widths and first moments over horizontal scanline strips, then uses separate nested immersion/trim brackets. It does not call production clipping, moment, geometry-building or equilibrium functions.

At volume 51,906 m³, KG 14.38 m, explicitly shared LCG 111.6013747622 m, free trim:

- Maximum production/oracle GZ error, 0–60°: **9.994e-7 m**.
- Maximum KN error: **4.071e-7 m**.
- Order-2 to order-4 quadrature change: GZ **4.851e-6 m**, KN **2.175e-6 m**.
- Source/code hashes were unchanged during both numerical runs.
- At the exact primary source loading and imposed zero trim, four additional independent GZ/KN checks agree with production within **2.375e-6 m**; see [primary-case oracle](primary-oracle-results.json). Runtime 13.96 s/150.4 MiB, estimated ~20 s/<0.3 GB.

**ASSUMPTION:** the shared LCG is the oracle upright LCB at draft 10.8 m, not a published loading LCG. **APPROXIMATION:** both methods deliberately retain the same normalized-arc-length ruled loft and the synthetic 16 m closure. The oracle verifies integration and free-trim equilibrium, not those geometry assumptions.

No evidence of an implementation bug was found. The place requiring envelope-owner attention is `src/stowage/vessel/hull_offsets.py::build_offset_geometry` and the `reference_depth_m=16` input convention, already documented as an approximation; `HullGeometry.kn/kn_curve` and actual-KG fixed-heel `_solve` reproduce the declared geometry. The external discrepancy is consistent with omitted reserve buoyancy and other source geometry differences; this experiment does not isolate their individual contributions. No out-of-fence correction was attempted.

## Verification, commands and compute

All commands used Python 3.12 via uv. Environment:

```sh
export UV_CACHE_DIR=/private/tmp/stowage-review-uv UV_NO_SYNC=1
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
uv run --python 3.12 python experiments/hydrostatics/large-angle-1028/compare.py
uv run --python 3.12 python experiments/hydrostatics/large-angle-1028/independent_geometry.py --compare-production --output experiments/hydrostatics/large-angle-1028/oracle-results.json
uv run --python 3.12 pytest -o addopts='' -q tests/vessel/test_hydrostatics_large_angle_reference.py experiments/hydrostatics/large-angle-1028/test_comparison.py experiments/hydrostatics/large-angle-1028/test_independent_geometry.py
uv run --python 3.12 python experiments/hydrostatics/large-angle-1028/verify_artifacts.py
uv run --python 3.12 --with matplotlib python experiments/hydrostatics/large-angle-1028/plot_comparison.py
```

Results: **19 tests passed in 3.56 s**; comparison and oracle exited 0; artifact verification passed eight archived-file hashes, frozen code/CSV hashes, 102 comparison rows and oracle residuals. Maximum volume residual 2.18e-6 m³. Plot inspected visually. Extraction commands and searches are in [primary search](search-primary.md) and [secondary search](search-secondary.md). Source files are untrusted and were parsed only, never executed; all downloads have URL/date/licence/SHA256 manifests.

No heavy job, GPU, cloud, git mutation or process termination was used. Every calculation was single-threaded. Production pilot 11.85 s/176 MiB; final comparison estimated under 90 s/0.25 GB from the pilot, actual **72.83 s/201.2 MiB**. Independent expanded run estimated under 90 s/0.2 GB, actual **50.98 s/240.8 MiB** (RAM estimate slightly exceeded). No shared fleet ledger change was required. Live checks showed load 10.32 and abundant free memory; `ps` was sandbox-denied. All project artifacts are under this experiment or the two new raw-source directories; package/render caches use writable temporary directories.

## Proposed leader-only status text

“Independent public KCS large-angle sources found and compared (28 Oct 2026). Original 16 m sealed KCS loft agrees with a separate scanline integration/free-trim oracle to 1e-6 m through 60°, but does not reproduce published 19 m KCS KN/GZ at large heel (40° GZ 0.210 vs 0.903 m in the explicit KG13 reference). Numerical integration verified; external real-KCS envelope validation remains open, now with measured disagreement and source provenance. Do not mark the original ‘independent large-angle KCS accuracy’ criterion passed. A matching authoritative upper envelope and reference loading are required before claiming physical validation.”

No decision/plan/evaluation file was edited. Recommend retaining the D20/D22 envelope approximation explicitly and assigning full-upper-envelope reconstruction plus rerun to its owner; do not tune KG, tolerance or hull depth to fit a curve.
