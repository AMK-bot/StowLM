Frozen manifest SHA-256: a11b42021a33f939739b6ea93d9517cd59209476b1dec840700503a10b14fdbd

Frozen tree digest: 80df1b64763969992cd0f87c4eb19bb80ea02fd17fc1d7953ffdbfb6d8d45c0b

# Pipeline sealed report

held-out maps, frozen pipeline, run after two interrupted attempts; not the pre-registered sealed claim

Synthetic ships and cargo, simulated port; fresh draws from the same generator, not new voyage types.
planning step from a clean load list, templated sentences; frozen standard profile.
Pass means L0 full checker pass with completion and every confirmed instruction obeyed.
Every pass count has an exact two-sided Clopper-Pearson 95% interval; unavailable trials remain in its denominator.
Out-of-envelope maps remain in the all-map denominator and are never passes.
Lift means use passing maps only; do not compare means across envelope cells or passing subsets.
Fewer extra crane lifts excludes productive loading/discharge.
Lifts include projected later-port lifts (D15), shown separately from current-port lifts.
Paired comparisons use mutual passes only; negative left-minus-right differences favour left.
Instruction comparisons reuse the no-rule pipeline result; no duplicate baseline run.
Binding obedient passes require a fresh-audited baseline breaking the rule and an instructed fresh-audited L0 pass.
Already-obeyed instruction pairs are a search-noise floor; negative instruction cost is search variation, not rule savings.
No-rule baseline counterfactual instruction rechecks never gate its pass.
Confirmation source sealed_expected_match is exact expected-rule equality, not a human planner confirmation.
Per-map wall_s records the planning child; reading_wall_s records its separate serial prepass reading once.
Port blocking and different relocation policies can explain savings; experienced planners may already block.
Waived weight-tolerance sensitivity (evaluation.md:85-87; 0/500/2000 kg): the labelled simple baseline is tested with finisher saturation instead.
No intake, voice, free wording, reader accuracy, live-profile, real-ship or industry-performance claim is measured here.

Two-step: a simple two-step planner, modelled from one field expert's description.

| Cohort / ship population | Arm | Rows | L0 passes (exact 95%) | Out of envelope | Relaxed levels | Mean map seconds |
|---|---|---:|---|---:|---|---:|
| tiny15 / 1700_TEU | pipeline | 5 | 5/5 (exact 95% [0.4782, 1.0000]) | 0 | {} | 53.198386591416785 |
| tiny15 / 1700_TEU | twostep | 5 | 0/5 (exact 95% [0.0000, 0.5218]) | 0 | {} | 8.693294241593685 |
| tiny15 / 500-800_TEU | pipeline | 10 | 10/10 (exact 95% [0.6915, 1.0000]) | 0 | {} | 41.38087992899818 |
| tiny15 / 500-800_TEU | twostep | 10 | 0/10 (exact 95% [0.0000, 0.3085]) | 0 | {} | 8.54865055840346 |
| small30 / 1700_TEU | pipeline | 10 | 9/10 (exact 95% [0.5550, 0.9975]) | 0 | {"L5": 1} | 475.46396118749396 |
| small30 / 1700_TEU | twostep | 10 | 0/10 (exact 95% [0.0000, 0.3085]) | 0 | {"L5": 9} | 45.91533242489677 |
| small30 / 500-800_TEU | pipeline | 20 | 20/20 (exact 95% [0.8316, 1.0000]) | 0 | {} | 409.4407867979462 |
| small30 / 500-800_TEU | twostep | 20 | 0/20 (exact 95% [0.0000, 0.1684]) | 0 | {"L5": 18} | 41.97025898544234 |
| envelope24 | pipeline | 24 | 24/24 (exact 95% [0.8575, 1.0000]) | 0 | {} | 563.1031777307871 |
| envelope24 | twostep | 24 | 0/24 (exact 95% [0.0000, 0.1425]) | 0 | {"L4": 9, "L5": 1} | 27.13477586449638 |
| envelope24 | instruction | 24 | 18/24 (exact 95% [0.5329, 0.9023]) | 0 | {"L4": 1, "L5": 2} | 694.499566244786 |
| envelope24 | twostep_300 | 24 | 1/24 (exact 95% [0.0011, 0.2112]) | 0 | {"L4": 8, "L5": 1} | 134.1419198438307 |
| envelope24 / feeder_500 | pipeline | 12 | 12/12 (exact 95% [0.7354, 1.0000]) | 0 | {} | 560.2488563819157 |
| envelope24 / feeder_500 | twostep | 12 | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | {"L4": 5, "L5": 1} | 27.020967131820118 |
| envelope24 / feeder_500 | instruction | 12 | 7/12 (exact 95% [0.2767, 0.8483]) | 0 | {"L5": 2} | 785.1174633437428 |
| envelope24 / feeder_500 | twostep_300 | 12 | 1/12 (exact 95% [0.0021, 0.3848]) | 0 | {"L4": 4, "L5": 1} | 146.5021778716667 |
| envelope24 / feeder_800 | pipeline | 12 | 12/12 (exact 95% [0.7354, 1.0000]) | 0 | {} | 565.9574990796585 |
| envelope24 / feeder_800 | twostep | 12 | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | {"L4": 4} | 27.24858459717264 |
| envelope24 / feeder_800 | instruction | 12 | 11/12 (exact 95% [0.6152, 0.9979]) | 0 | {"L4": 1} | 603.8816691458293 |
| envelope24 / feeder_800 | twostep_300 | 12 | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | {"L4": 4} | 121.7816618159947 |

Primary: unweighted lifts_total, small, 500-800 TEU; quotable: False. D284: post-registration run; no pre-registered sealed claim.
Primary total and current-port intervals are always shown together:

- Total extra lifts: None; paired 95% None.
- Current-port extra lifts: None; paired 95% None.
D15 scope: None.

Port blocking and different relocation policies can explain savings; experienced planners may already block.

| Cohort / ship population | Comparison | Both pass (exact 95%) | Left only (exact 95%) | Right only (exact 95%) | Neither pass (exact 95%) |
|---|---|---|---:|---:|---:|
| tiny15 / 1700_TEU | pipeline_vs_twostep | 0/5 (exact 95% [0.0000, 0.5218]) | 5/5 (exact 95% [0.4782, 1.0000]) | 0/5 (exact 95% [0.0000, 0.5218]) | 0/5 (exact 95% [0.0000, 0.5218]) |
| tiny15 / 500-800_TEU | pipeline_vs_twostep | 0/10 (exact 95% [0.0000, 0.3085]) | 10/10 (exact 95% [0.6915, 1.0000]) | 0/10 (exact 95% [0.0000, 0.3085]) | 0/10 (exact 95% [0.0000, 0.3085]) |
| small30 / 1700_TEU | pipeline_vs_twostep | 0/10 (exact 95% [0.0000, 0.3085]) | 9/10 (exact 95% [0.5550, 0.9975]) | 0/10 (exact 95% [0.0000, 0.3085]) | 1/10 (exact 95% [0.0025, 0.4450]) |
| small30 / 500-800_TEU | pipeline_vs_twostep | 0/20 (exact 95% [0.0000, 0.1684]) | 20/20 (exact 95% [0.8316, 1.0000]) | 0/20 (exact 95% [0.0000, 0.1684]) | 0/20 (exact 95% [0.0000, 0.1684]) |
| envelope24 | pipeline_vs_twostep | 0/24 (exact 95% [0.0000, 0.1425]) | 24/24 (exact 95% [0.8575, 1.0000]) | 0/24 (exact 95% [0.0000, 0.1425]) | 0/24 (exact 95% [0.0000, 0.1425]) |
| envelope24 | instruction_vs_pipeline | 18/24 (exact 95% [0.5329, 0.9023]) | 0/24 (exact 95% [0.0000, 0.1425]) | 6/24 (exact 95% [0.0977, 0.4671]) | 0/24 (exact 95% [0.0000, 0.1425]) |
| envelope24 | pipeline_vs_twostep_300 | 1/24 (exact 95% [0.0011, 0.2112]) | 23/24 (exact 95% [0.7888, 0.9989]) | 0/24 (exact 95% [0.0000, 0.1425]) | 0/24 (exact 95% [0.0000, 0.1425]) |
| envelope24 / feeder_500 | pipeline_vs_twostep | 0/12 (exact 95% [0.0000, 0.2646]) | 12/12 (exact 95% [0.7354, 1.0000]) | 0/12 (exact 95% [0.0000, 0.2646]) | 0/12 (exact 95% [0.0000, 0.2646]) |
| envelope24 / feeder_500 | instruction_vs_pipeline | 7/12 (exact 95% [0.2767, 0.8483]) | 0/12 (exact 95% [0.0000, 0.2646]) | 5/12 (exact 95% [0.1517, 0.7233]) | 0/12 (exact 95% [0.0000, 0.2646]) |
| envelope24 / feeder_500 | pipeline_vs_twostep_300 | 1/12 (exact 95% [0.0021, 0.3848]) | 11/12 (exact 95% [0.6152, 0.9979]) | 0/12 (exact 95% [0.0000, 0.2646]) | 0/12 (exact 95% [0.0000, 0.2646]) |
| envelope24 / feeder_800 | pipeline_vs_twostep | 0/12 (exact 95% [0.0000, 0.2646]) | 12/12 (exact 95% [0.7354, 1.0000]) | 0/12 (exact 95% [0.0000, 0.2646]) | 0/12 (exact 95% [0.0000, 0.2646]) |
| envelope24 / feeder_800 | instruction_vs_pipeline | 11/12 (exact 95% [0.6152, 0.9979]) | 0/12 (exact 95% [0.0000, 0.2646]) | 1/12 (exact 95% [0.0021, 0.3848]) | 0/12 (exact 95% [0.0000, 0.2646]) |
| envelope24 / feeder_800 | pipeline_vs_twostep_300 | 0/12 (exact 95% [0.0000, 0.2646]) | 12/12 (exact 95% [0.7354, 1.0000]) | 0/12 (exact 95% [0.0000, 0.2646]) | 0/12 (exact 95% [0.0000, 0.2646]) |

Pass counts above are separate from mutual-pass lift savings below. Port blocking and different relocation policies can explain savings; experienced planners may already block.

| Cohort / ship population | Comparison | Metric | Mutual passes (exact 95%) | Metric n | Wins / ties / losses | Signed mean left-minus-right | Paired 95% interval | Relocation-policy confound |
|---|---|---|---|---:|---|---:|---|---|
| tiny15 / 1700_TEU | pipeline_vs_twostep | weighted_lifts | 0/5 (exact 95% [0.0000, 0.5218]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| tiny15 / 1700_TEU | pipeline_vs_twostep | lifts_total | 0/5 (exact 95% [0.0000, 0.5218]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| tiny15 / 1700_TEU | pipeline_vs_twostep | lifts_current_port | 0/5 (exact 95% [0.0000, 0.5218]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| tiny15 / 1700_TEU | pipeline_vs_twostep | lifts_projected_later_port | 0/5 (exact 95% [0.0000, 0.5218]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| tiny15 / 500-800_TEU | pipeline_vs_twostep | weighted_lifts | 0/10 (exact 95% [0.0000, 0.3085]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| tiny15 / 500-800_TEU | pipeline_vs_twostep | lifts_total | 0/10 (exact 95% [0.0000, 0.3085]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| tiny15 / 500-800_TEU | pipeline_vs_twostep | lifts_current_port | 0/10 (exact 95% [0.0000, 0.3085]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| tiny15 / 500-800_TEU | pipeline_vs_twostep | lifts_projected_later_port | 0/10 (exact 95% [0.0000, 0.3085]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| small30 / 1700_TEU | pipeline_vs_twostep | weighted_lifts | 0/10 (exact 95% [0.0000, 0.3085]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| small30 / 1700_TEU | pipeline_vs_twostep | lifts_total | 0/10 (exact 95% [0.0000, 0.3085]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| small30 / 1700_TEU | pipeline_vs_twostep | lifts_current_port | 0/10 (exact 95% [0.0000, 0.3085]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| small30 / 1700_TEU | pipeline_vs_twostep | lifts_projected_later_port | 0/10 (exact 95% [0.0000, 0.3085]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| small30 / 500-800_TEU | pipeline_vs_twostep | weighted_lifts | 0/20 (exact 95% [0.0000, 0.1684]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| small30 / 500-800_TEU | pipeline_vs_twostep | lifts_total | 0/20 (exact 95% [0.0000, 0.1684]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| small30 / 500-800_TEU | pipeline_vs_twostep | lifts_current_port | 0/20 (exact 95% [0.0000, 0.1684]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| small30 / 500-800_TEU | pipeline_vs_twostep | lifts_projected_later_port | 0/20 (exact 95% [0.0000, 0.1684]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 | pipeline_vs_twostep | weighted_lifts | 0/24 (exact 95% [0.0000, 0.1425]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 | pipeline_vs_twostep | lifts_total | 0/24 (exact 95% [0.0000, 0.1425]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 | pipeline_vs_twostep | lifts_current_port | 0/24 (exact 95% [0.0000, 0.1425]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 | pipeline_vs_twostep | lifts_projected_later_port | 0/24 (exact 95% [0.0000, 0.1425]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 | instruction_vs_pipeline | weighted_lifts | 18/24 (exact 95% [0.5329, 0.9023]) | 18 | 5 / 5 / 8 | 6.277777777777778 | [-0.2777777777777778, 13.61111111111111] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 | instruction_vs_pipeline | lifts_total | 18/24 (exact 95% [0.5329, 0.9023]) | 18 | 5 / 5 / 8 | 6.277777777777778 | [-0.2777777777777778, 13.61111111111111] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 | instruction_vs_pipeline | lifts_current_port | 18/24 (exact 95% [0.5329, 0.9023]) | 18 | 3 / 10 / 5 | 0.9444444444444444 | [-0.6666666666666666, 2.6666666666666665] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 | instruction_vs_pipeline | lifts_projected_later_port | 18/24 (exact 95% [0.5329, 0.9023]) | 18 | 4 / 8 / 6 | 5.333333333333333 | [-0.7777777777777778, 12.11111111111111] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 | pipeline_vs_twostep_300 | weighted_lifts | 1/24 (exact 95% [0.0011, 0.2112]) | 1 | 1 / 0 / 0 | -55.0 | [-55.0, -55.0] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 | pipeline_vs_twostep_300 | lifts_total | 1/24 (exact 95% [0.0011, 0.2112]) | 1 | 1 / 0 / 0 | -55 | [-55.0, -55.0] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 | pipeline_vs_twostep_300 | lifts_current_port | 1/24 (exact 95% [0.0011, 0.2112]) | 1 | 1 / 0 / 0 | -5 | [-5.0, -5.0] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 | pipeline_vs_twostep_300 | lifts_projected_later_port | 1/24 (exact 95% [0.0011, 0.2112]) | 1 | 1 / 0 / 0 | -50 | [-50.0, -50.0] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_500 | pipeline_vs_twostep | weighted_lifts | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_500 | pipeline_vs_twostep | lifts_total | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_500 | pipeline_vs_twostep | lifts_current_port | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_500 | pipeline_vs_twostep | lifts_projected_later_port | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_500 | instruction_vs_pipeline | weighted_lifts | 7/12 (exact 95% [0.2767, 0.8483]) | 7 | 2 / 2 / 3 | 5.428571428571429 | [-1.4285714285714286, 16.857142857142858] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_500 | instruction_vs_pipeline | lifts_total | 7/12 (exact 95% [0.2767, 0.8483]) | 7 | 2 / 2 / 3 | 5.428571428571429 | [-1.4285714285714286, 16.857142857142858] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_500 | instruction_vs_pipeline | lifts_current_port | 7/12 (exact 95% [0.2767, 0.8483]) | 7 | 2 / 4 / 1 | -0.5714285714285714 | [-3.0, 1.5714285714285714] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_500 | instruction_vs_pipeline | lifts_projected_later_port | 7/12 (exact 95% [0.2767, 0.8483]) | 7 | 0 / 4 / 3 | 6 | [0.2857142857142857, 16.857142857142858] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_500 | pipeline_vs_twostep_300 | weighted_lifts | 1/12 (exact 95% [0.0021, 0.3848]) | 1 | 1 / 0 / 0 | -55.0 | [-55.0, -55.0] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_500 | pipeline_vs_twostep_300 | lifts_total | 1/12 (exact 95% [0.0021, 0.3848]) | 1 | 1 / 0 / 0 | -55 | [-55.0, -55.0] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_500 | pipeline_vs_twostep_300 | lifts_current_port | 1/12 (exact 95% [0.0021, 0.3848]) | 1 | 1 / 0 / 0 | -5 | [-5.0, -5.0] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_500 | pipeline_vs_twostep_300 | lifts_projected_later_port | 1/12 (exact 95% [0.0021, 0.3848]) | 1 | 1 / 0 / 0 | -50 | [-50.0, -50.0] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_800 | pipeline_vs_twostep | weighted_lifts | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_800 | pipeline_vs_twostep | lifts_total | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_800 | pipeline_vs_twostep | lifts_current_port | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_800 | pipeline_vs_twostep | lifts_projected_later_port | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_800 | instruction_vs_pipeline | weighted_lifts | 11/12 (exact 95% [0.6152, 0.9979]) | 11 | 3 / 3 / 5 | 6.818181818181818 | [-1.9090909090909092, 16.454545454545453] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_800 | instruction_vs_pipeline | lifts_total | 11/12 (exact 95% [0.6152, 0.9979]) | 11 | 3 / 3 / 5 | 6.818181818181818 | [-1.9090909090909092, 16.454545454545453] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_800 | instruction_vs_pipeline | lifts_current_port | 11/12 (exact 95% [0.6152, 0.9979]) | 11 | 1 / 6 / 4 | 1.9090909090909092 | [-0.09090909090909091, 4.090909090909091] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_800 | instruction_vs_pipeline | lifts_projected_later_port | 11/12 (exact 95% [0.6152, 0.9979]) | 11 | 4 / 4 / 3 | 4.909090909090909 | [-3.272727272727273, 13.818181818181818] | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_800 | pipeline_vs_twostep_300 | weighted_lifts | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_800 | pipeline_vs_twostep_300 | lifts_total | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_800 | pipeline_vs_twostep_300 | lifts_current_port | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |
| envelope24 / feeder_800 | pipeline_vs_twostep_300 | lifts_projected_later_port | 0/12 (exact 95% [0.0000, 0.2646]) | 0 | 0 / 0 / 0 | None | None | Port blocking and different relocation policies can explain savings; experienced planners may already block. |

| Instruction population | Binding obedient passes / baseline rule-broken maps (exact 95%) | Already obeyed (exact 95%) | Counterfactual unavailable | Reader failures | Engine failures |
|---|---|---|---:|---:|---:|
| all types | 13/18 (exact 95% [0.4652, 0.9031]) | 6/24 (exact 95% [0.0977, 0.4671]) | 0 | 0 | 6 |
| close_bay | 4/6 (exact 95% [0.2228, 0.9567]) | 0/6 (exact 95% [0.0000, 0.4593]) | 0 | 0 | 2 |
| dg_keep_away | 2/2 (exact 95% [0.1581, 1.0000]) | 4/6 (exact 95% [0.2228, 0.9567]) | 0 | 0 | 0 |
| max_tiers | 5/6 (exact 95% [0.3588, 0.9958]) | 0/6 (exact 95% [0.0000, 0.4593]) | 0 | 0 | 1 |
| one_port_per_hatch | 2/4 (exact 95% [0.0676, 0.9324]) | 2/6 (exact 95% [0.0433, 0.7772]) | 0 | 0 | 3 |

| Instruction population | Signed instructed-minus-baseline total extra lifts (paired 95%) | Already-obeyed noise floor (paired 95%) | Mean boxes in different cells |
|---|---|---|---:|
| all types | 6.277777777777778 [-0.2777777777777778, 13.61111111111111] | -4.4 [-12.4, 1.2] | 134.1904761904762 |
| close_bay | 18.75 [2.0, 35.5] | None None | 127.33333333333333 |
| dg_keep_away | -4.666666666666667 [-11.0, -0.5] | -6.25 [-15.0, 0.0] | 128.83333333333334 |
| max_tiers | 13.2 [2.0, 24.4] | None None | 143.33333333333334 |
| one_port_per_hatch | 0 [-2.0, 3.0] | 3 [3.0, 3.0] | 140.33333333333334 |

| Instruction type | L0 passes (exact 95%) | Recheck pass (exact 95%) | Recheck fail | Unavailable |
|---|---|---|---:|---:|
| close_bay | 4/6 (exact 95% [0.2228, 0.9567]) | 6/6 (exact 95% [0.5407, 1.0000]) | 0 | 0 |
| dg_keep_away | 6/6 (exact 95% [0.5407, 1.0000]) | 6/6 (exact 95% [0.5407, 1.0000]) | 0 | 0 |
| max_tiers | 5/6 (exact 95% [0.3588, 0.9958]) | 6/6 (exact 95% [0.5407, 1.0000]) | 0 | 0 |
| one_port_per_hatch | 3/6 (exact 95% [0.1181, 0.8819]) | 3/6 (exact 95% [0.1181, 0.8819]) | 0 | 3 |

Per-map status, gate, lift, recheck, plan-difference and timing values: `per-map.csv`.
All passing-only means and missing metrics: `report.json`. Missing metrics are unavailable, never zero.
