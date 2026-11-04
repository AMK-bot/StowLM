# StowLM serialization sizing (task 4.5)

## Current fix-wave prompt 2.1 measurements

[`current_measurements.json`](current_measurements.json) measures the **current
2.1 serializer and local Glimmer tokenizer** on SHA256-verified, unchanged canonical
campaign-2.0.0 shards, the original persisted ladder, and one heterogeneous synthetic
layover. Campaign producer versions predate this fix wave (including D36);
**these are current token measurements, not fresh current-generator qualification**.
Source paths, source/manifest/tokenizer/serializer hashes, provenance, seeds,
heuristic failures, timing and budgets are retained per report. No physics data was
regenerated or altered. The old 2.0 and pre-KCS tables below are historical evidence.

The prompt now retains each relevant box's effective D25 weight class. It also
factors yard block references once per group; parsed yard addresses remain exact.
No CSC ratings, booking-family metadata, provenance, loading-manual policy tables
or unrepresented background cargo were added to the observation. Exact instance
physics and checker gates remain authoritative. Old 2.0 prompt parsing is retained.

| Canonical random-yard rung | Seed | Loads | Prompt | Output incl. EOS | Total SFT |
|---|---:|---:|---:|---:|---:|
| tiny | 0 | 6 | 591 | 43 | 634 |
| small | 100000 | 36 | 1,078 | 253 | 1,331 |
| medium | 200000 | 150 | 2,652 | 1,051 | 3,703 |
| full | 300000 | 280 | 3,931 | 1,961 | 5,892 |

| Dense generator | Noise | Seed | Loads | Prompt | Output incl. EOS | Total SFT |
|---|---:|---:|---:|---:|---:|---:|
| random_yard | 0 | 400000 | 400 | 4,211 | 2,801 | 7,012 |
| random_yard | 0 | 500000 | 800 | 7,316 | 5,601 | 12,917 |
| simulated_yard | 0 | 10400000 | 400 | 3,414 | 2,801 | 6,215 |
| simulated_yard | 0 | 10500000 | 800 | 5,906 | 5,601 | 11,507 |
| random_yard | 0.8 | 400011 | 400 | 5,448 | 2,801 | 8,249 |
| random_yard | 0.8 | 500011 | 800 | 10,615 | 6,041 | 16,656 |
| simulated_yard | 0.8 | 10400011 | 400 | 4,322 | 2,801 | 7,123 |
| simulated_yard | 0.8 | 10500011 | 800 | 7,527 | 5,601 | 13,128 |

The original seed-1 ladder now measures prompt/output/total
**573/43/616**, **1,145/253/1,398**, **2,720/1,051/3,771**,
**4,328/1,961/6,289** for tiny/small/medium/full. It retains its historical
pre-KCS physics snapshot and producer versions. The additional canonical
simulated-yard tiny/medium/full totals are **655 / 3,410 / 5,330**.
The lowest-seed canonical simulated-yard small shard cannot be assigned by the
existing mixed-length greedy sizing heuristic; its prompt is **1,083** tokens.
The diagnostic records the original assignment failure and the all-20ft fallback's
inapplicability. It records no fabricated output, context fit or feasibility claim.
Measuring that specimen's output needs a move-engine-replayable plan from its solver
owner; this bounded heuristic failure does not prove physical infeasibility.

All **20 completed examples** fit the **2,000 + 12N combined prompt target** and
131,072-token context. The separate incremental **12 tokens/load** target fails
on seven small/heterogeneous samples: category and flag costs matter, so this
is not a uniform 12-token bound. All 19 completed non-layover outputs fit
8 tokens/load × 1.05 + EOS. The noisy random 800 case uses 6,041 output tokens
because some indices exceed 999; seven tokens/load is not universal.

The heterogeneous layover has DG/SG, reefer, OOG H/W/L, special stowage, a hard
instruction and a rule override. Its **696 prompt + 54 output = 750 total**
tokens include an actual yard relocation, explicit discharge destination, discharge
and through-box restow. The full expanded 12-lift plan replays through the move
engine. **54 exceeds the tiny load-only expectation of 34.6 tokens**: the
additional discharge/restow actions and one whole relocation are counted explicitly.
The supplementary event accounting (six ordinary actions × eight tokens + exact
ten-token relocation + EOS = 59) fits; it does not replace D18's nominal budget.

Every completed output round-trips and its expanded lifts pass the move engine.
**None is full-checker-certified training data.** No quality/objective-loss,
model latency, masked inference, solver feasibility or zero-loss claim follows.
Sampling covers one lowest seed per profile/generator (except the named failure),
one lowest noise=0.8 seed per dense profile/generator, and one constructed tiny
layover. It does not establish bounds across all seeds, ports, flag combinations,
arbitrary instructions/override frequencies, or future background-box populations.
The prior lossy-observation limitations remain and need the leader's decisions entry.

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false \
UV_CACHE_DIR=/private/tmp/stowage-review-uv nice -n 10 \
uv run --no-sync python experiments/format/current.py

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false \
UV_CACHE_DIR=/private/tmp/stowage-review-uv nice -n 10 \
uv run --no-sync pytest -q -p no:cacheprovider tests/llm/test_format_budgets.py
```

Compute: single-case probe **0.431 s / 386 MB**; scaled estimate **<45 s / <1 GB,
one CPU core, no GPU** for the expanded study. First full completed measurement:
**12.512 s / 407 MB**. No heavy reservation, network, cloud spend or model run.
See [`fix-sizing.md`](fix-sizing.md) for the exact final measurement and leader-only
replacement compute row. The new evidence-coverage regression failed before the
report existed. The expanded noisy-800 budget test failed at **12,050 > 11,600**
before grouped yard block references; final **10,615** passes with exact facts retained.

---

## Archived prompt 2.0 KCS evidence (superseded token counts)

## Current KCS canonical v2 dense profiles

The KCS migration leaves **440 / 840 physical cells** on feeder_500 / feeder_1000.
The canonical dense profiles are **400 / 800 twenty-foot loads**, as specified in
[`../instances-v2/README.md`](../instances-v2/README.md) and the canonical
[`manifest`](../instances-v2/manifest.json). These replace the old synthetic
450/900-box experiments and their historical hull snapshots below.

`extended.py` now loads actual, unchanged manifest shards, selecting the lowest
seed for each realistic profile and each yard generator. It verifies each shard's
SHA256 and family against the manifest. The report records source paths, seeds,
scenario specifications, manifest/shard hashes and instance provenance. No
instances, cargo parameters, hulls or physical limits are regenerated or edited.
These four representative shards all have noise coordinate 0; this is not a
measurement over all 80 canonical dense instances.

| Yard generator | Vessel | Seed | Loads / cells | Prompt | Output incl. EOS | SFT tokens/example | Expanded lifts | Estimated model seconds |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| random_yard | feeder_500 | 400000 | 400 / 440 | 4,678 | 2,801 | 7,479 | 416 | 101.96 |
| random_yard | feeder_1000 | 500000 | 800 / 840 | 8,415 | 5,601 | 14,016 | 820 | 237.36 |
| simulated_yard | feeder_500 | 10400000 | 400 / 440 | 3,273 | 2,801 | 6,074 | 673 | 100.35 |
| simulated_yard | feeder_1000 | 10500000 | 800 / 840 | 5,847 | 5,601 | 11,448 | 820 | 234.40 |

All four action-token round trips and expanded move-engine replays pass. All
prompt/output totals fit the 131,072-token context and the measured output
budgets. These sizing plans are **not full-checker-certified training data**.
The action encoding, forced tokens and shared token-ID builder are unchanged.

Prompts cost 907–1,087 fixed tokens and 5.91–9.43 incremental tokens/load.
Even the total prompt divided by loads is below 12 on these four cases
(11.70 / 10.52 / 8.18 / 7.31 respectively). All meet the 2,000 + 12N target;
arbitrary flag heterogeneity, layovers and instructions still need measurement.

The original box-ID-order sizing heuristic dead-ended on simulated800 because
it tried to relocate a buried load target when no suitable yard position was
available. The experiment now uses an all-20-foot, top-access-first fallback:
load accessible targets into supported hold-before-deck slots before digging
for buried targets. Its complete expansion is independently replayed. The JSON
retains the original failure and names the fallback; no data or checker rule
was changed. Other cases retain the original sizing heuristic.

Model time estimates use prefill 870 tok/s, generation 29 tok/s for 2,801-token
outputs and 24.6 tok/s for 5,601-token outputs, from the existing Glimmer benchmark.
They exclude mask, feasibility, ballast and checker costs and are not measured
end-to-end model latency. SFT totals are prompt + output, before padding or
multiple epochs; the 800-box examples now cost 14,016 / 11,448 tokens each.
The lossy observation policy and its limitations remain as documented below.

Reproduce with one CPU core, no model weights or GPU:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false \
UV_CACHE_DIR=/private/tmp/stowlm-format-uv \
uv run python experiments/format/measure_extended.py

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false \
UV_CACHE_DIR=/private/tmp/stowlm-format-uv \
uv run pytest -q tests/llm
```

Verification: **75 LLM tests pass**, including four real-tokenizer canonical dense
round-trip/budget cases. Details are in
[`extended_measurements.json`](extended_measurements.json).
Compute: one-case probe 0.602 s; estimate <5 s / <1 GB for all four, one core,
no GPU. Final measurement including the fallback took 5.625 s and peaked at
365,805,568 bytes (~348.9 MiB). No cloud spend or heavy job was needed.

Everything below is historical pre-KCS evidence. In particular, its 450/900-box
rows, 504/1,008-cell capacities and old test outcomes are superseded by this section.

---

## Archived pre-KCS study (superseded extended cases)

Measured 2026-10-27, local Muse Glimmer tokenizer, one CPU core, no weights/GPU,
no cloud spend. [`measurements.json`](measurements.json) covers every persisted
3.5 rung (one seed-1 shard each); [`extended_measurements.json`](extended_measurements.json)
covers generator-composed 450/900-box whole-feeder cases. Source snapshot SHA256s,
provenance and timings are recorded with the numbers.

## Format and observation decision

Use native Glimmer system/user framing and explicitly prefill
`<|start|>assistant to=user<|message|>`. SFT labels contain actions directly and
end with `<|eot|>`, without analysis. This fixes the user recipient rather than
letting generation select `to=self`; suppression of reasoning by the trained
adapter remains unverified until inference.

`P000 0010182\n` costs **7 tokens** through index 999: independently encoded opcode,
index, space, bay, row, tier, newline. Forced separators and EOS remain (D9).
Every sequence uses `build_sequence`; retokenizing displayed text is not the
serialization API. Indices cover all containers, including blockers. The 900-box
case costs 6,471 output tokens rather than 6,301 because 170 load indices exceed 999
and use an additional token. Actions never lose box identity.

The prompt is an intentionally **lossy decision observation**. Exact cell IDs,
20/40/45 acceptance, reefer flags and hatch-cover memberships are retained through
factored layout groups. The grouped layout adds 68/144/308/556 tokens on the
persisted ladder and 556/688 on the 450/900-box cases. Use this geometry rather than
vessel ID alone, since the model must learn actual slice cells and cover access.
Cargo is grouped by dimensions, upward-rounded 2,000 kg mass bucket, POD call and
special flags. Yard target/blocker order and their action indices remain exact;
block/bay/row addresses use dense ordinals. Unrelated yard occupancy is aggregated.

Content loss is explicit:

- ISO IDs, exact mass within a bucket, equipment labels and reefer set points are absent; standard heights are represented at millimetre precision.
- DG retains class and segregation-code summaries; exact UN entry, subsidiary details and other declaration fields are absent. OOG retains H/W/L extension categories, not exact overhang metres.
- Coordinates, hull/tank/strength/stack-limit tables, tank contents and other detailed ship physics are absent. Exact load/discharge lists, onboard placements, covers, rotation, port crane/yard limits and explicit instructions/overrides remain.
- Unrelated occupancy lacks exact stack locations, hiding some default-relocation distance/clean-stack choices.

The feasibility engine and full checker retain the original unquantized Instance;
no physics model or hard limit is relaxed. **A mask cannot restore missing model
priors or objective information:** rounded masses can hide support/weight tradeoffs,
DG/OOG summaries can hide future restrictive choices, omitted geometry/stability
information can hide ballast or restow costs, and aggregated yard occupancy can
hide relocation preferences. Quality/objective loss must be measured against
checker-approved gold plans; no zero-loss claim is made. Rare heterogeneous flags
and instructions can also break the measured token target. This approximation
needs a leader entry in `docs/decisions.md`.

## Measured budget and SFT size

Output plans are short-box-first slot assignments, ordered hold-before-deck and
bottom-before-top, expanded through `boxslot@1.0`. Every load is placed and every
expanded plan passes the physical move engine. **They are sizing plans, not
full-checker-certified gold or training data.** Departure DG, strength, stability,
reefers and other stowage constraints are not certified.

| Case | Loads | Yard boxes | Prompt | Output incl. EOS | Total SFT | Expanded lifts | Estimated model seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| tiny | 6 | 8 | 513 | 43 | 556 | 10 | 1.98 |
| small | 36 | 47 | 907 | 253 | 1,160 | 51 | 9.20 |
| medium | 150 | 195 | 2,487 | 1,051 | 3,538 | 207 | 36.76 |
| full ladder | 280 | 364 | 4,179 | 1,961 | 6,140 | 388 | 68.06 |
| whole feeder 500 | 450 | 585 | 5,912 | 3,151 | 9,063 | 609 | 115.45 |
| whole feeder 1000 | 900 | 1170 | 11,084 | 6,471 | 17,555 | 1198 | 275.79 |

All six totals fit the local config's **131,072-token context**, and all measured
outputs fit 3.8's 8 tokens/load + 5% extra actions + EOS planning budget. The superseded exact-facts prompt cost 43,016 tokens for the full ladder;
the compact observation costs 4,179, trading away the information listed above. The previous
1,500-input-token planning assumption in `docs/compute.md` still undercounts
medium/full and the extended cases; re-estimate SFT tokens from the total column.
Three thousand 900-box examples would be 52.665 M tokens/epoch, not a measured
training-time claim. SFT sequence lengths of 1,024/4,096 cannot hold these large
examples intact; future training configuration must handle full sequences or
explicitly justified segmentation.

Fixed costs below keep the same chat, metadata, layout and port/instruction fields,
but empty cargo/load/yard observations. Variable cost is full prompt minus this
baseline; it includes relevant blockers. The combined target is 2,000 + 12N tokens.

| Case | Fixed | Variable | Variable/load | All-in prompt/load | Combined target headroom |
|---|---:|---:|---:|---:|---:|
| tiny | 454 | 59 | 9.83 | 85.50 | 1,559 |
| small | 528 | 379 | 10.53 | 25.19 | 1,525 |
| medium | 702 | 1,785 | 11.90 | 16.58 | 1,313 |
| full ladder | 951 | 3,228 | 11.53 | 14.93 | 1,181 |
| whole feeder 500 | 972 | 4,940 | 10.98 | 13.14 | 1,488 |
| whole feeder 1000 | 1146 | 9,938 | 11.04 | 12.32 | 1,716 |

Every fixed cost is below 2,000, every incremental cost is below 12/load, and
every combined bound passes on these six cases. **Strict all-in prompt/load <=12
still fails all six** because fixed costs remain. This is measured evidence,
not a bound for arbitrary cargo heterogeneity, instructions or layover states.

Model-only time = prompt/870 + output/generation_rate seconds. Generation rates:
31 tok/s for persisted outputs through 2k; 29 tok/s for 450; 24.6 tok/s for 900, from
`experiments/glimmer-bench/run_long.log`. Prefill was measured near 9k input,
so the 11.1k extended prompt and longer combined contexts are extrapolations.
No model latency was measured here. Estimates exclude mask/feasibility, trie
builds, ballast and checker. No GPU run was performed to validate these speeds.

### Extended generation assumptions

`extended.py` composes the **existing production cargo/yard generators**, with
process-local configuration copies: 450 loads on feeder 500 and 900 on feeder 1000,
seed 1, blocking ratio 0.3, eight Laem-Chabang-like or sixteen Singapore-like yard
blocks respectively. Laem-Chabang-like has only 12 blocks and cannot provide 900
separate reserved two-bay target footprints, so the larger synthetic port is used.
Equipment shares are conditioned to 87% dry 20/3% reefer 20/10% dry 40. Mass sampling,
DG, E8, reefer properties and every physical limit remain unchanged. Draw 1 passes
the generator's necessary screens in both cases, which does not certify feasibility.

A 504 TEU vessel cannot carry 500 boxes at the original mostly 40 ft mix; box count
and TEU differ. Measured 450 boxes occupy 486 TEU of 504 physical cells; 900 occupy
975 TEU of 1,008 cells. These are deliberately mostly 20 ft stress bookings, not a
claim of calibrated traffic or 500 mixed boxes on a 500 TEU ship. Full-physics
vessels are unchanged validated, SHA256-pinned historical cache snapshots, analogous to 3.5's
persisted instance snapshots. Their paths/SHA256s are recorded. They were not
rebuilt against the current component fingerprint; generator provenance records
current code and does not certify the snapshot as current physics. No hull
approximation was introduced.

### Override sensitivity

A synthetic, unexpanded sensitivity adds `ceil(.05*loads)` relocation overrides at
measured 10 tokens each: persisted outputs become 53/273/1,131/2,101. Tiny exceeds the
nominal fractional budget 51.4 by 1.6 tokens: one whole override is 16.7% of six loads;
expected fractional 5% output 46 tokens fits. Actual override frequency remains
solver-measurement work. All measured replay plans use zero explicit overrides.

## Prefix trie cost for task 5.1

A trie shares candidate-action token prefixes: outgoing edges give allowed next
IDs. After completing an action, refresh candidates for the new state; insert EOS
only when the feasibility engine permits completion. The measured set pairs each
load box with every initially supported, length-compatible slot. It is a superset,
**not full-checker legality**. Builds include candidate encoding; lookup averages
8,000 prefix queries. No giant extended-case tries were built.

| Rung | Candidates | Nodes | Edges | Build ms | Lookup microseconds |
|---|---:|---:|---:|---:|---:|
| tiny | 24 | 86 | 85 | 0.95 | 0.30 |
| small | 570 | 1,550 | 1,549 | 23.73 | 0.36 |
| medium | 9,642 | 25,228 | 25,227 | 392.26 | 0.55 |
| full | 37,938 | 98,684 | 98,683 | 1456.71 | 0.81 |

Lookup is cheap; rebuilding 1.46 s per full action would add about 6.8 minutes for 280
loads. Earlier runs under other CPU contention measured 2.58 s. Task 5.1 must cache
encoded actions and measure incremental pruning or engine-legal candidate builds.
No mask-speed claim follows from lookup alone.

## Reproduction and compute

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false \
UV_CACHE_DIR=/private/tmp/stow-format-uv-cache \
uv run --offline --no-sync python experiments/format/measure.py

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false \
UV_CACHE_DIR=/private/tmp/stow-format-uv-cache \
uv run --offline --no-sync python experiments/format/measure_extended.py
```

Both scripts check action-token round trips, parsed load facts and physical replay
on every case. Executive verification: **36 format tests pass**, and frozen-interface checks pass.
The whole-suite `uv run pytest -q` reached about 94% with failure
markers before the executive's 150 s bound. Its recorded PID was terminated;
[`pytest-full.log`](pytest-full.log) contains the incomplete run. There is no
whole-suite pass claim. This experiment edits no solver or unrelated test files.
A subsequent `uv run pytest -q --maxfail=1 --tb=short` on the final format stopped
at unrelated `tests/checker/test_stability.py::test_draft_trim_and_list_limits`:
free-trim equilibrium did not converge at 89.99 degrees. See
[`pytest-first-failure.log`](pytest-first-failure.log). Earlier format budget
failures during development are resolved by the final 36 passing format tests.

Compute: tiny initial probe 0.934 s/345 MB peak; estimate <10 s/<1 GB for ladder,
<30 s/<1 GB for extended, one core, no GPU. Final ladder run 5.576 s/362.6 MiB; extended run 7.714 s/387.6 MiB; timings and process RSS are recorded
in the JSON reports; these are whole-process peaks, not isolated trie allocations.
The 45-load extended generator probe took 0.044 s. Earlier all-rung exact-observation
run took 10.033 s/385 MiB before compression; final compact runs are cheaper. No
heavy job, new physics integration or fleet reservation was needed.

Sources: [`../action-space/README.md`](../action-space/README.md) for action budgets;
[`../glimmer-bench/README.md`](../glimmer-bench/README.md) and `run_long.log` for speed;
local `config.json`/`chat_template.jinja` for context/framing; module docstrings and
[Hugging Face tokenization documentation](https://huggingface.co/docs/tokenizers/pipeline).
