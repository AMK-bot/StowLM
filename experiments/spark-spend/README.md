# Muse Spark spend harness

The initial implementation used offline tests. A subsequent authorized single
request on **2026-10-27** is recorded in `calls.jsonl`: 21 input tokens and 128
output tokens cost **$0.00057025**. Its status was **incomplete**, so it does not
establish that a completed model answer was received. The charge went to the
card; the claimed $150 hackathon credit was still pending. No further live calls
were made during the review fixes. Synthetic mock records use temporary files.

Calls are blocked by default. Only after the user verifies the credit is applied
and authorizes paid work should they set `SPARK_CREDIT_READY=1` (or explicitly
pass `credit_ready=True` to `SparkClient`) and provide `MODEL_API_KEY` outside the
repository. The gate is checked on every `complete`, including reused clients;
remove the flag to hold further calls. A key alone does not enable paid calls.
Do not load keys from a repository file or put them in command arguments or logs.

The already implemented `--test-call` CLI sends at most one HTTP request (no
retries), using a synthetic prompt. Do not run it while credit is on hold.
Incomplete responses raise `SparkIncompleteError` after measured usage is logged;
empty and nonempty truncated answers have the same error behavior.

Offline summary (no key or network needed):

```sh
uv run python -m llm.spark.client --summary
```

Use `UV_CACHE_DIR=$PWD/.uv-cache` if the default uv cache is inaccessible.
`--spend-cap-usd 10` sets a $10 total-project ceiling. The default ceiling is
$150; prior manual Meta Model API spend plus authoritative JSONL spend and holds
reduce remaining capacity. A lower ceiling persists in the managed ledger row;
raising it requires explicit reconciliation by the user. Never remove accounting
records or markers just to free capacity.

Every append automatically and durably updates one **managed `spark-jsonl` row**
in `docs/compute.md`. Other text and ledger rows are preserved. That row binds
the compute budget to one authoritative JSONL path (relative to the compute
file, URL-encoded) and its enforced ceiling. Historical Spark rows carrying the
`spark-jsonl` marker describe calls already in JSONL and are excluded from
manual spend. Neither historical rows nor cumulative managed summaries are
additional charges. Keep the authoritative JSONL file alongside the compute
file; a missing bound file fails closed. If a write fails after JSONL changes,
that file remains authoritative and `SpendLedger.sync_compute()` can rebuild
the managed summary offline, without sending a request.

`compute.md.spark.lock` serializes the entire check/reserve/request/settle
transaction across processes, even when clients request different JSONL paths.
Symlink compute paths resolve to the same budget. The existing JSONL `.lock` is
also retained. A client requesting a different empty path adopts the bound
JSONL; an alternate path containing records is refused until reconciled, rather
than ignoring that spend. Callers sharing a project budget must share its
canonical compute file. Supplying a different compute file creates a different
budget identity; it is not a supported way to bypass the project ceiling.

Each HTTP attempt fsyncs a reservation and its managed compute summary **before
sending**, then a settlement. Settlement replaces its reservation for totals;
do not blindly sum JSONL rows. A crash leaves the reservation charged against
capacity. Known usage uses exact Decimal pricing: $1.25 per million uncached
input tokens, $0.15 cached input, $4.25 output (including reasoning). Cached
tokens are a subset of total input.

To avoid an unverified tokenizer estimate or an extra API request, each attempt
reserves the largest possible cost within the documented context window and its
output limit (about $1.31 with the test's 128-token output limit). The remaining
budget must cover that bound before sending even a tiny prompt. HTTP 4xx
rejections release the reservation. Timeouts, 5xx, malformed responses and
interrupted attempts retain a conservative hold: their actual billing is unknown.
The summary distinguishes measured spend from pending/unknown holds. Reconcile
unknown outcomes against the provider before editing accounting records. Standard
client retries can duplicate charged work; each attempt has its own reservation.

Public sources checked during the original implementation on 2026-10-27:
- https://dev.meta.ai/docs/quickstart
- https://dev.meta.ai/docs/protocols/responses
- https://dev.meta.ai/docs/pricing-rate-limits
- https://dev.meta.ai/docs/error-handling

The recorded incomplete response provides historical connectivity evidence.
Current eligibility, credit readiness and completed-answer compatibility remain
unverified; live rechecking requires the user's applied credit and authorization.
