# Freshwater pre-final qualification

All original D49 numerical samplers remain unchanged. `profile_run.py` selects
1000kg/m³ port water and pins the adapter in every observation and all three
bank evidence lists. The root retains atomic promotion ownership.

Controllers (all numerical children use the shared central `compute.run`):

```
uv run python experiments/linear-model/epoch_requalification/production-1028c/freshwater/ordinary_campaign.py
uv run python experiments/linear-model/epoch_requalification/production-1028c/freshwater/edges_campaign.py
uv run python experiments/linear-model/epoch_requalification/production-1028c/freshwater/diagnostics_campaign.py --phase all
```

Ordinary collects fresh192 raw states per hull; every original production
training, independent holdout and retained enrichment population; all four
original targeted middle-hull populations; 64+64 LIST boundaries per side;
and fresh-coefficient equivalence. Exact seeds/counts remain in
`population-plan.json`. Edge controllers retain v1/v2/v3 designs and all
independent holdouts; one v3 scheduling shard still measures all800 requests.

The original historical N1 reviewer point has unchanged physical mass above
the freshwater500 family upper displacement. It is never clipped, scaled,
admitted or called a holdout. `necessary_adapter.py` retains the complete
original necessary loader with one training-file requirement conditional on a
mandatory exclusion certificate. That certificate recomputes total mass from
lightship, original fixed point and all original tanks, binding raw response,
reference, domain, stations and band. Every grid/holdout/lineage/disjointness
check remains the original code. Both the adapters and exclusion proof are
pinned in all bank evidence lists. An exact freshwater diagnostic replay still
records the rejected point, separately from qualification populations.

Seven controlled contrasts and both eight-face diagnostic algorithms are
freshly measured at1000. Diagnostic edge records retain all requested states,
explicit physical admissions/rejections, producer/capture pins and ordinary
reference transfer. Candidate gates automatically require all three captures
and check every admitted diagnostic residual and individually exact-feasible
public row. They never fit margins or supply independent holdouts.

Root candidate/promotion entry point:

```
uv run python experiments/linear-model/epoch_requalification/production-1028c/promote_profile.py freshwater --input experiments/linear-model/epoch_requalification/production-1028c/freshwater/ordinary --output CANDIDATE_OUTPUT --include-necessary --necessary-edge-input experiments/linear-model/epoch_requalification/production-1028c/freshwater/edges-v3 --necessary-calibration-enrichment experiments/linear-model/epoch_requalification/production-1028c/freshwater/edges-v1 experiments/linear-model/epoch_requalification/production-1028c/freshwater/edges-v2
uv run python experiments/linear-model/epoch_requalification/production-1028c/freshwater/verification_campaign.py --phase candidate --bank CANDIDATE_OUTPUT/candidate-bank.json --baseline-bank experiments/linear-model/epoch_requalification/production-1028c/bank-before.json
```

The root alone adds `--apply` after all gates and source guards pass. After
promotion, `verification_campaign.py --phase ordinary --workers3` replays all
576 ordinary conditions against exact physics, then checks both public row
directions; `--phase manifest` binds all fresh edge inputs. Use `--workers 3`
(with a space) in the actual command. Qualification remains empirical, and the
exact checker remains the plan certifier.
