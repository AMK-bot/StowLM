"""Render a CPU handoff from frozen artifact and measured shared-ledger evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    artifact = json.loads((HERE / 'results/artifact-audit.json').read_text())
    rules = json.loads((HERE / 'results/rules-summary.json').read_text())
    spark = json.loads((HERE / 'results/spark-summary.json').read_text())
    decontamination = json.loads((HERE / 'results/decontamination.json').read_text())
    removed = sum(len(v['removed']) for v in decontamination['splits'].values())
    preflight = json.loads((HERE / 'results/train-preflight.json').read_text())
    if preflight['device_loaded'] or preflight['config_sha256'] != hashlib.sha256((HERE / 'config.json').read_bytes()).hexdigest():
        raise ValueError('Preflight must be CPU-only and match the final operational config')
    for name in ('train', 'valid'):
        if preflight['data_sha256'][f'{name}.jsonl'] != artifact[name]['sha256']:
            raise ValueError('Preflight data differs from frozen artifact audit')
    ledger = ROOT / 'experiments/spark-spend/calls.jsonl'
    latest = {}
    for line in ledger.read_text().splitlines():
        row = json.loads(line)
        if row.get('purpose') in {'reader-data-paraphrases-v1', 'reader-d199-sealed-zeroshot'}:
            latest[row['reservation_id']] = row
    by_request = {row.get('request_id'): row for row in latest.values()}
    predictions = [json.loads(line) for line in (HERE / 'results/spark-predictions.jsonl').read_text().splitlines()]
    paraphrases = json.loads((HERE / 'data/spark_paraphrases.json').read_text())
    for item in predictions + [paraphrases]:
        ledger_row = by_request[item['request_id']]
        if ledger_row.get('status') != 'completed' or Decimal(ledger_row['cost_usd']) != Decimal(item['cost_usd']):
            raise ValueError('Response/ledger accounting mismatch')
    spend = {}
    for purpose, cap in [('reader-data-paraphrases-v1', '5'), ('reader-d199-sealed-zeroshot', '2')]:
        calls = [r for r in latest.values() if r['purpose'] == purpose]
        measured = sum((Decimal(r['cost_usd']) for r in calls if r.get('cost_kind') == 'measured'), Decimal(0))
        # The authoritative ledger's status distinguishes measured usage and unknown holds.
        total = sum((Decimal(r['cost_usd']) for r in calls), Decimal(0))
        spend[purpose] = {'calls': len(calls), 'latest_records': calls, 'charged_or_held_usd': str(total),
                          'measured_usd': str(measured), 'unknown_hold_usd': str(total - measured),
                          'allocation_usd': cap, 'within_allocation': total <= Decimal(cap)}
    audit = {'time_utc': datetime.now(timezone.utc).isoformat(), 'ledger': str(ledger.relative_to(ROOT)),
             'ledger_snapshot_sha256': hashlib.sha256(ledger.read_bytes()).hexdigest(), 'jobs': spend,
             'response_request_ids_and_costs_verified': len(predictions) + 1}
    (HERE / 'results/spend-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    total = sum((Decimal(v['charged_or_held_usd']) for v in spend.values()), Decimal(0))
    text = f'''# D199 reader CPU handoff

Ready: {artifact['train']['rows']:,} train, {artifact['valid']['rows']:,} family-heldout validation, {artifact.get('test', {}).get('rows', 0):,} generator-family test, and {artifact['sealed']['rows']} independent sealed sentences. All targets pass strict schema and real-vessel context checks. Cross-split sentences and paraphrase families are disjoint. The separate Claude Opus reviewer received only the public contract and ship context, with tools disabled and working directory `/private/tmp`; it never saw the grammar. Sealed sentences were not supplied to the data or training agents. The prompt, optimizer settings, epochs, and adapter selection policy were fixed before evaluation; no training or tuning uses sealed data or its scores.

Hash-only decontamination removed {removed} accidental exact-sentence matches from generated data, using no sealed labels; the sealed artifact remained byte-identical. Original generator metadata and final file hashes are retained in `data/manifest.pre-decontamination.json`, `data/manifest.json`, and `results/decontamination.json`.

CPU verification: 36 reader tests pass, including actual train-instance mask/static-table checks. Full tokenizer preflight validates every train/validation completion boundary without model weights or a device. Maximum example length is 717 tokens; two epochs are approximately 5.71M training tokens. Diagnostic validation batches were capped at 20 after the tokenizer exposed roughly 23.0M full-validation forward tokens under the initial cadence. This resource-only amendment does not select a different optimizer, epoch count, checkpoint, or prompt and uses no sealed labels or performance feedback; old/new config hashes are in `results/validation-budget-amendment.json`. At the inherited SFT estimate of 230 tokens/s, training alone is approximately 6.9 GPU hours before overhead; actual short-sequence throughput remains unmeasured.

Vocabulary retains CloseBay, MaxTiers, DgKeepAway, Refusal and adds only OnePortPerHatch. Its hard rule applies across the hold and deck of a physical cover. `constraints.py` compiles an explicit cover-to-POD allocation into a restriction usable by the production mask and Frontier's copied static table. The allocation is conservative; stock `plan_and_check` is not automatically instruction aware. A finished plan still requires the independent instruction audit and full checker. No binding replan or plan saving is claimed here.

| Sealed arm | Exact structured match | Loosening accepted |
|---|---:|---:|
| rules.parse_rules | {rules['exact_match_count']}/{rules['count']} ({rules['exact_match_pct']:.2f}%) | {rules['false_acceptance_count']}/{rules['loosening_count']} |
| Zero-shot Muse Spark | {spark['exact_match_count']}/{spark['count']} ({spark['exact_match_pct']:.2f}%) | {spark['false_acceptance_count']}/{spark['loosening_count']} |
| Untrained Glimmer | Pending GPU executive | Pending |
| Fine-tuned Glimmer | Pending GPU executive | Pending |

Exact match validates the full structured envelope and refusal code; only free-text refusal reason is excluded. Invalid or missing outputs are wrong. Any emitted constraint on a loosening sentence counts as false acceptance, including invalid-context constraints. Rules has {rules['malformed_count']} context-invalid/malformed outputs; Spark has {spark['malformed_count']}, with {spark['missing_count']} missing. The Spark arm is zero-shot with the same frozen prompt/context and no examples; its 1,024-token cap includes reasoning, while Glimmer's cap is 256 direct completion tokens.

Spark calls are recorded on the authoritative shared ledger and its managed `docs/compute.md` row. Data: ${spend['reader-data-paraphrases-v1']['charged_or_held_usd']} against $5; sealed Spark: ${spend['reader-d199-sealed-zeroshot']['charged_or_held_usd']} against $2; total ${total}. `results/spend-audit.json` binds request IDs to ledger records. Claude reviewer usage is separately retained in `sealed/provenance.json` and is not Meta API spend.

Exact CPU, train, and evaluation commands are in [GPU_HANDOFF.md](GPU_HANDOFF.md). Glimmer base only; no Llama, local model inference, training, or GPU execution was performed in this CPU task. The fixed recipe selects the final adapter after two epochs. R1/R2/R3 remain pending until the GPU arms are scored. R3 interprets “within 3 of Spark” as no more than 3 percentage points below Spark; outperforming Spark by more than 3 does not fail.

Assumptions/limits: English synthetic/public planner language; context from manifest-listed train ships only; at most one supported firm restriction per sentence. Entire paraphrase templates are held out by family. No general multilingual or real-company performance claim. D166/D170 require a violating unconstrained plan and a changed, freshly checked constrained plan before any later binding-replan claim.
'''
    (HERE / 'REPORT.md').write_text(text)
    print(json.dumps({'train': artifact['train']['rows'], 'sealed': artifact['sealed']['rows'],
                      'spark_exact_pct': spark['exact_match_pct'], 'total_spark_usd': str(total)}, indent=2))


if __name__ == '__main__':
    main()
