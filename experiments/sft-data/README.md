# Task 7.1 clean SFT publication

`build.py` reads **published** task-6.1 `tiny-gold-manifest@1.0` records. It has no
solver, model inference, GPU or cloud path. No current real gold exists: 6.1
published zero records after its repair, so an empty manifest is refused without
writing a dataset. A real dataset waits for the gold campaign.

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/sft-data/build.py \
  --gold-manifest experiments/gold-plans/campaign/EPOCH/manifest.json \
  --instances-root PATH/TO/instances-eEPOCH --require-epoch \
  --g1-summary PATH/TO/LEADER-APPROVED-G1.json --production \
  --out experiments/sft-data/EPOCH
```

Those paths illustrate required inputs; they do not assert a completed run.
The leader's summary must have matching `epoch_id`, `g1_met=true`, and filled
`min_yield` thresholds met by each selected rung's solve yield. Gold and the
instance epoch must carry the same current `REQUIRED_RULES.json` freeze pin
(D61/D62/D64 and schema ≥1.9). Editing that leader-owned policy requires a new
freeze. Each input's own generator/condition versions must also be current.

Without approved G1 lineage the builder labels its output `pipeline_test`;
`--production` refuses it. Tiny G1 exemptions have this label even if their
checker and mask passed. `train.py` admits these only with `diagnostic=True`
under `sft-pilot/` or `tiny-chain/`, and taints the adapter lineage. Re-labelling
`stats.json` cannot remove the per-row restriction. Dataset/adapter identities
retain training epochs and catalogue digests for the eval owner to display.
`--split-manifest` selects the trusted frozen v2 release (default current v2).
Membership and claimed labels are checked before shard reads. Manifest entries
bind record bytes; records bind original input bytes. Path traversal, duplicate
specifications, release drift and missing requested splits are refused.

The shared `build_gold_example` requires clean provenance, published shipping
and checker evidence bound to the instance and plan, current component epochs,
and D50 departure/arrival metadata and endpoint reports. Every accepted example
is replayed independently against production mask 1.2 and actual C2-on engine,
using prompt 2.1. Any failure stops publication; failures are never silently
dropped to manufacture a 100% rate.

The production writer records token-length statistics and deterministic size
curriculum (fewest boxes, then shortest sequence, then instance ID), replay
counts, versions and release pins. Curriculum means presenting smaller examples
first, as defined directly by `order_by_size` in the shared builder; it changes
example order without weakening acceptance checks.

Required splits default to `train val test`. The current 6.1 tiny runner is
train-only; `--required-splits train` permits an explicitly partial tiny check,
never a completed full dataset claim. Real task completion requires clean gold
for every required split and 100% current production replay, after G1/source
freeze and an admitted sized campaign. Historical tiny-chain C2-off pilots and
synthetic unit fixtures are not evidence for that campaign.

The frozen campaign entry point is
[`../gold-plans/campaign/dataset.sh`](../gold-plans/campaign/dataset.sh); see
[`DATASET.md`](../gold-plans/campaign/DATASET.md) for the resumable command and
publication contract. It requires shared frozen-epoch validation and adds
per-rung, per-split token-length statistics. Completed-build resume checks input,
source and output hashes; interrupted builds retry full admission and replay.
`--resume` and `--require-epoch` are also available directly on `build.py`.

`real-decoder-fixtures.json` records a separate reproducible diagnostic run from
`verify_fixture.py`: four hand-authored 1/2/3-box examples across all splits,
4/4 replay through the real decoder and hull-free engine, zero production
verified examples. Their tainted rows live in
`experiments/tiny-chain/synthetic-mask-fixtures/`. This proves serialization,
token-mask replay, lengths and size ordering on deterministic diagnostics.

`test_synthetic_all_split_pipeline_statistics_and_curriculum` additionally
exercises this CLI through gold-policy admission and pipeline-test writing with bound
synthetic checker reports and a synthetic action engine, using the real replay
token loop. Its 4/4 replay and train size order 1 then 3 prove policy plumbing;
the authored C2 label and passing reports do not establish physical gold.

CPU regression checks (synthetic policy/orchestration fixtures only):

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 10 uv run pytest -q -p no:cacheprovider tests/llm/test_sft_data*.py
```
