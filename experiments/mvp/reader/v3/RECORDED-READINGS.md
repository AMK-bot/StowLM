# How recorded readings were produced (logic review 3, X1; checked 1 Nov 2026)

**Every stored reading checked came from Muse Spark, not Glimmer, and none went through the v3 mask.**

| Store | Evidence records | Parser, model | Adapter |
|---|---:|---|---|
| `voice/runs/v5-1.3/checkpoints` | 160 | SparkParser, muse-spark-1.3 | none |
| `reader/runs/refusals` | 12 | muse-spark-1.3 (8 name SparkParser) | none |
| `reader/runs/brief` | 120 | SparkParser, muse-spark-1.3 | none |
| `reader/runs/brief2` | 120 | SparkParser, muse-spark-1.3 | none |

- `SparkParser` calls `client.complete` on the cloud. There is no logits mask. The answer is validated after it returns.
- The demo's hero reading (`app/v6.py` `reading()`, clip `en-reader-0449f2adc144ea36-clean.json`) is Spark:
  "Planner, restrict Bay 27 hold to one max.", $0.00217. `app/v6_hero.json` marks it a stand-in until the final reader.
- `--reader glimmer` (`app/brief.py`, `glimmer_reader`) calls `mlx_generate` with no `logits_processors`: unmasked, as
  X1 says. It has produced no stored reading in these stores.
- Masked Glimmer exists only in `v3/evaluate.py`, `reader/parser_check.py`, `xray/record.py` and `v3/mask_study.py`.

Consequence: until the app's reading is re-recorded through the adopted adapter with the mask, the reading chapter shows
Spark, and sealed Glimmer accuracy doesn't describe `--reader glimmer`.
