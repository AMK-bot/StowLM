# Independent ext-1 recount

The preregistration SHA-256 is checked before every recount. This tool reads
raw run evidence and safe preparation metadata; it neither imports the run's
report/scoring code nor opens map payloads. It writes only in this directory.

```sh
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -m unittest discover -s experiments/review/v6/ext1-recount -p 'test_*.py'
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/review/v6/ext1-recount/recount.py --output partial.json
# Only after runs/ext-1/COMPLETE.json exists or controller PID 25903 has exited:
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/review/v6/ext1-recount/recount.py --final --output final.json
```

`partial.json` retains all 24 maps × three arms. Its counts are explicitly
partial; missing trials become `not_run`, and all unavailable/error/outside
trials remain in their arm's denominator. Finality comes from the run-owned
completion marker or a read-only libproc confirmation that controller PID 25903
has exited, not the presence of reports or the number of rows. No signal is
sent to the controller. Exit without completion retains every not-run trial and
must be described as a provisional interrupted result in the review.

Binding eligibility follows review code11's 3.1 definition: a fresh-audited
L0 pipeline baseline whose saved intended-rule recheck is exactly false.
Already-obeyed and unavailable baselines are separate. The instructed
numerator requires a complete fresh-audited obedient L0 pass. Intended-rule
checks are the raw fresh audit's recorded rechecks; this recount does not
execute the checker again. Exact two-sided 95% Clopper–Pearson intervals use
independent SciPy beta quantiles, including boundary cases and undefined 0/0.

No report numbers are read by `recount.py`. Any report comparison must happen
after `final.json` is written. Raw input hashes and capture time make that
ordering reviewable. The opening-log and seed-proof check is a closed-world
provenance audit: it cannot prove the absence of unrecorded accesses.

After the final independent count is saved, compare both reports, figures, CSV,
exact intervals, paired diagnostic bootstraps and optional secondary pooling:

```sh
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/review/v6/ext1-recount/compare.py
```

`compare.py` refuses to compare without a saved FINAL count and matching raw
input hashes. It records report hashes, definition differences and stable input
checks in `comparison.json`. The verdict and all limitations are in
[code15.md](../code15.md).
