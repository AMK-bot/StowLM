# Recorded results charts

Static, self-contained SVGs for the StowLM site and README. No new model or planning runs.
The opaque white canvas keeps labels readable when embedded on light or dark pages;
labels and marker shapes supplement a colour-blind-safe palette. No external fonts or scripts.

Regenerate from the repository root:

```sh
UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/mvp/charts/generate.py
UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -m unittest discover -s experiments/mvp/charts -p 'test_*.py' -v
```

`--output-dir PATH` writes a fresh set elsewhere. Python standard library only.
Intervals are the recorded exact two-sided Clopper–Pearson 95% intervals, not newly estimated intervals.
The tests regenerate all charts, compare bytes, compare visible values with the recorded sources,
and independently derive each row's marker, interval and bar geometry from source numbers and axis ticks.

## Charts and sources

Paths below are relative to the repository root; SHA-256 covers the exact source bytes.
Embedded SVG metadata also records the plotted values and source provenance.

### [decode-mask-overrides.svg](decode-mask-overrides.svg)

SVG SHA-256: `9859a4ba6e38fb2e774f1a0b2c065ace385bd5bd78f0ce33cc85500dba4a4972`

- `docs/headline.md` — `48f8315729481c665f15807dc468911ac313ad7f71a07bad5e505573348f3d06`
- `experiments/mvp/reader/v3/results-final/mask-study/report-base.json` — `795009c1dc3e277365279adc5902860a2eab847b0ab1f08c37b1d768976bc077`
- `experiments/mvp/reader/v3/results-final/mask-study/report.json` — `4774b875747953fbd1dd7652e3ed12ec04d905545c937baa40e300f95ec84173`

### [ext1-passes.svg](ext1-passes.svg)

SVG SHA-256: `413b6be8a1321e4180b217431f2341a15e0049c6147dd0006e2ac2f5a3c4c857`

- `docs/headline.md` — `48f8315729481c665f15807dc468911ac313ad7f71a07bad5e505573348f3d06`
- `experiments/mvp/pipeline/sealed/measurements/ext-1/FIGURES.json` — `3d47d1f602dc2b25cc5e69aa7d154c1a9989038aec4479897ca2ac5d58ee6627`

### [instruction-binding.svg](instruction-binding.svg)

SVG SHA-256: `c34e9b667bea6be8f5592b037d9702ddbee3672d299eafbce6608b37f514e8fc`

- `docs/headline.md` — `48f8315729481c665f15807dc468911ac313ad7f71a07bad5e505573348f3d06`
- `experiments/mvp/pipeline/sealed/measurements/ext-1/FIGURES.json` — `3d47d1f602dc2b25cc5e69aa7d154c1a9989038aec4479897ca2ac5d58ee6627`
- `experiments/mvp/pipeline/sealed/runs/final-3/report.json` — `282765330f4d3592d88f04768a1c7abf8ad3c092b2c76448c4dfc47fc43b765c`
- `experiments/mvp/pipeline/sealed/runs/final-3/story-figures.json` — `87c3e49689d4ef15258ec01867c717615690d1b21c85eae904704c34b6f3b029`

### [pipeline-passes.svg](pipeline-passes.svg)

SVG SHA-256: `82597366a61c0b468f7ecdf23bac5130408ed57c7493ac749c456dd95d04618f`

- `docs/headline.md` — `48f8315729481c665f15807dc468911ac313ad7f71a07bad5e505573348f3d06`
- `experiments/mvp/pipeline/sealed/runs/final-3/report.json` — `282765330f4d3592d88f04768a1c7abf8ad3c092b2c76448c4dfc47fc43b765c`
- `experiments/mvp/pipeline/sealed/runs/final-3/story-figures.json` — `87c3e49689d4ef15258ec01867c717615690d1b21c85eae904704c34b6f3b029`

### [reader-exact-match.svg](reader-exact-match.svg)

SVG SHA-256: `539af429ecd96772bebbdbba0c28c8df6f41a03c639d989b139ebadd41d0cafe`

- `docs/headline.md` — `48f8315729481c665f15807dc468911ac313ad7f71a07bad5e505573348f3d06`
- `experiments/mvp/reader/v3/parser/sealed-v3-report.json` — `1b89bf29791e100b2c462a93c26e1c5b0975c22d39851097c2b770d6414cc44d`
- `experiments/mvp/reader/v3/results-final/final-adoption-d266.json` — `58f47abb91e7d36c905faa17262c130cd42429ecc4cbbeada86f7818944f3b78`
- `experiments/mvp/reader/v3/results-final/sealed-summary.json` — `af418c22b95e575ae6b1d7335f0379dc69c0297ec2d6a9d2f0c9e74c3900287c`
- `experiments/mvp/reader/v3/sealed/spark-summary.json` — `04db7db2bc4552967cc9dfb9f6d8aaa19a20c098045cf9969c6fc3c008dc7f09`

## Claim labels and assumptions

- `docs/headline.md` — `48f8315729481c665f15807dc468911ac313ad7f71a07bad5e505573348f3d06`
- Original 3.1 results report frozen pipeline v3, held-out maps after two interrupted attempts;
  they are not the pre-registered sealed claim. Interrupted attempts are disclosed and never pooled.
- `ext1-passes.svg` shows 3.1 beside the fresh held-out extension, run after the freeze;
  not the pre-registered sealed claim. The extension ran on v4, with planning code byte-identical to v3.
  All extension and secondary pooled numbers come only from ext-1/FIGURES.json.
- `instruction-binding.svg` keeps the 3.1 binding result beside the fresh extension result.
  Binding requires a complete fresh-audited L0 baseline that breaks the intended rule,
  followed by a complete, obedient, fresh-audited instructed L0 pass; already-obeyed cases stay separate.
- Pooled rows are SECONDARY descriptive figures combining completed 3.1 and ext-1 envelope maps,
  not one experiment. They exclude interrupted attempts, tiny/small maps and sensitivity arms.
- Tiny/small results distinguish 500–800 TEU from 1,700 TEU; the latter is outside the envelope.
  Envelope results refer to a half-ship section, up to 195 boxes, up to 70% fill,
  empty on arrival with one loading call. Synthetic ships and cargo, simulated port.
- Full-checker passes are L0; relaxed plans are excluded. No lift saving is claimed.
- Reader results are the spent sealed-v3 set; Spark is the fixed original reference, not rerun.
  D266 adopted v3 despite the earlier retention decision; R1 passes, R2 and R3 fail.
- Mask results are validation diagnostics of first-choice overrides, with terminal-token swaps excluded.
  Almost all concern output format, none an illegal bay or tier; both residual sentences should
  have been refused, and legal output can still be wrong, so a person confirms.
