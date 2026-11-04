"""Isolated Claude v3 holdout and schema-constrained, ledgered Spark baseline.

Never opens earlier sealed sets or test-v3. Sentence text is not printed. All
scoring uses original generated JSON; nothing is repaired or relabeled.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

HERE = Path(__file__).resolve().parent
READER = HERE.parent
ROOT = HERE.parents[3]
OUT = HERE / 'sealed'
PURPOSE = 'reader-v3-contingency-sealed-zeroshot'
REVIEW_PURPOSE = 'reader-v3-contingency-independent-claude'
LANGUAGE = 'reader-schema-v3'
REVIEW_CAP = Decimal('0.55')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def packet():
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / 'reviewer_prompt.txt'
    if target.exists():
        raise ValueError('Reviewer packet already frozen')
    instruction = '''You are a separate independent Claude evaluation author. Your only
evidence is the public reader contract and synthetic world supplied below. You
have no tools. You have never seen a training grammar, training examples,
prior sealed sets, model outputs, or validation results. Do not invent access.

Author exactly 128 distinct new operational planner sentences and independently
label them under the public contract. Return JSONL only, no markdown. Each line
has exactly id, sentence, expected, kind, family, loosening. IDs sealed-v3-001
through sealed-v3-128 in order. expected is the complete strict JSON envelope,
kind is its constraint type or refusal code, family a short descriptive category,
loosening a boolean equal to whether the expected code is loosening. The public
fixed vessel context will be attached unchanged by ingestion; omit context and
messages. Labels and sentences must be your own and will never be repaired.

Distribution: 12 close_bay; 12 max_tiers; 12 dg_keep_away; 16 one_port_per_hatch;
48 loosening; 16 ambiguous (hedged or underspecified); 6 unsupported; and 6
not_an_instruction. Total 128. Use diverse natural short sentence forms and
semantic constructions. For relaxation cover exemptions, permissions to override,
temporary exceptions, removal, increased caps, reduced separation, reopening,
mixed-port allowances and mixed tightening/loosening. Contract priority applies.
Vary syntax without referring to earlier test sets. For ambiguity include genuine
uncertainty and unresolved scope. Explicit firm polite requests may be valid.
Use only this synthetic world; no real customer, personnel or private data.
Do not claim feasibility from parsing. Keep each sentence under 35 words and
refusal.reason under 8 words, to keep this authoring run economical.

PUBLIC CONTRACT:\n'''
    target.write_text(instruction + (READER / 'contract.json').read_text()
                      + '\nPUBLIC SYNTHETIC WORLD:\n' + (READER / 'vocabulary.md').read_text())
    write_json(OUT / 'packet-manifest.json', {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'contract_sha256': sha(READER / 'contract.json'),
        'vocabulary_sha256': sha(READER / 'vocabulary.md'),
        'reviewer_prompt_sha256': sha(target),
        'policy': 'Public contract/world only; no grammar, prior sealed, training, or results.'})


def verify_packet():
    manifest = json.loads((OUT / 'packet-manifest.json').read_text())
    for key, path in [('contract_sha256', READER / 'contract.json'),
                      ('vocabulary_sha256', READER / 'vocabulary.md'),
                      ('reviewer_prompt_sha256', OUT / 'reviewer_prompt.txt')]:
        if manifest[key] != sha(path):
            raise ValueError('Frozen reviewer input changed: ' + key)


def acquire():
    from experiments.mvp.reader.v3.admission import admit
    from llm.spark.spend import SpendLedger
    admission = admit()
    if (OUT / 'claude_response.json').exists():
        raise ValueError('Never overwrite a reviewer response')
    if not (OUT / 'reviewer_prompt.txt').exists():
        packet()
    verify_packet()
    command = ['claude', '--print', '--model', 'sonnet', '--effort', 'low',
               '--max-budget-usd', str(REVIEW_CAP), '--output-format', 'json',
               '--safe-mode', '--restricted', '--setting-sources', '',
               '--disable-slash-commands', '--tools', '', '--strict-mcp-config',
               '--mcp-config', '{"mcpServers":{}}', '--no-session-persistence',
               '--system-prompt-snapshot', 'off', '--system-prompt',
               'You are an independent synthetic contract-reader evaluation author. Use only the public packet. No tools.']
    env = dict(os.environ)
    for key in ('MODEL_API_KEY', 'META_API_KEY', 'SPARK_API_KEY', 'META_MODEL_API_KEY',
                'SPARK_CREDIT_READY', 'CLAUDECODE'):
        env.pop(key, None)
    ledger = SpendLedger()
    reservation = uuid4().hex
    base = {'time': datetime.now(timezone.utc).isoformat(), 'model': 'claude-sonnet',
            'purpose': REVIEW_PURPOSE, 'reservation_id': reservation, 'request_id': None,
            'input_tokens': None, 'output_tokens': None, 'cached_tokens': None}
    with ledger.locked():
        prior = [r for r in ledger._records() if r.get('purpose') == REVIEW_PURPOSE]
        if prior:
            raise ValueError('Reviewer already reserved; reconcile existing run, never silently retry')
        ledger.check_capacity(REVIEW_CAP)
        ledger.append({**base, 'phase': 'reservation', 'cost_usd': str(REVIEW_CAP),
                       'status': 'pending', 'cost_kind': 'conservative_hold'})
    with tempfile.TemporaryDirectory(prefix='reader-v3-review-', dir='/private/tmp') as cwd:
        with (OUT / 'reviewer_prompt.txt').open() as inp, (OUT / 'claude_response.json').open('x') as out, (OUT / 'claude_stderr.txt').open('x') as err:
            child = subprocess.Popen(command, cwd=cwd, env=env, stdin=inp, stdout=out, stderr=err)
            write_json(OUT / 'reviewer-process.json', {'pid': child.pid, 'admission': admission,
                       'argv': command, 'cwd': 'new empty /private/tmp/reader-v3-review-*',
                       'tools': [], 'mcp_servers': [], 'session_persistence': False})
            try:
                code = child.wait(timeout=600)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                raise RuntimeError('Reviewer timeout; reservation retained, do not retry automatically') from None
    raw = json.loads((OUT / 'claude_response.json').read_text())
    cost = Decimal(str(raw.get('total_cost_usd', REVIEW_CAP)))
    if not cost.is_finite() or cost < 0:
        raise ValueError('Reviewer cost unknown; conservative hold retained')
    with ledger.locked():
        ledger.append({**base, 'phase': 'settlement', 'cost_usd': str(cost),
                       'status': 'completed' if code == 0 else 'reviewer_error',
                       'cost_kind': 'measured', 'request_id': raw.get('session_id')})
    write_json(OUT / 'reviewer-accounting.json', {'cost_usd': str(cost),
        'cost_source': 'Claude CLI total_cost_usd', 'reservation_id': reservation,
        'purpose': REVIEW_PURPOSE, 'shared_ledger': str(ledger.path.relative_to(ROOT)),
        'exit_code': code, 'raw_response_sha256': sha(OUT / 'claude_response.json')})
    if code or raw.get('is_error') or cost > REVIEW_CAP:
        raise ValueError('Reviewer failed or exceeded review cap; preserve diagnostics')
    print(json.dumps({'reviewer_exit_code': code, 'reviewer_cost_usd': str(cost)}))


def ingest():
    from experiments.mvp.reader.schema import canonicalize, system_prompt
    target = OUT / 'cases.jsonl'
    if target.exists():
        raise ValueError('Never overwrite a sealed set')
    verify_packet()
    raw = json.loads((OUT / 'claude_response.json').read_text())
    if raw.get('is_error') or not raw.get('result'):
        raise ValueError('No successful reviewer result')
    rows = [json.loads(line) for line in raw['result'].splitlines() if line.strip()]
    if len(rows) < 120 or len({r['sentence'].casefold().strip() for r in rows}) != len(rows):
        raise ValueError('Need >=120 unique independently authored sentences')
    if [r['id'] for r in rows] != [f'sealed-v3-{i:03}' for i in range(1, len(rows)+1)]:
        raise ValueError('Non-contiguous reviewer IDs')
    contract = json.loads((READER / 'contract.json').read_text())
    if hashlib.sha256(system_prompt().encode()).hexdigest() != contract['system_prompt_sha256']:
        raise ValueError('Public contract and system prompt differ')
    loose = 0
    for index, row in enumerate(rows):
        if set(row) != {'id', 'sentence', 'expected', 'kind', 'family', 'loosening'}:
            raise ValueError(f'Unexpected reviewer keys at index {index}')
        try:
            label = canonicalize(row['expected'], contract['sealed_context'])
        except Exception:
            raise ValueError(f'Invalid authored label at index {index}; no repair allowed') from None
        is_loose = label.get('refusal', {}).get('code') == 'loosening'
        kind = label.get('constraint', {}).get('type') or label.get('refusal', {}).get('code')
        if type(row['loosening']) is not bool or row['loosening'] != is_loose or row['kind'] != kind:
            raise ValueError(f'Inconsistent authored metadata at index {index}')
        loose += is_loose
        row.update(context=contract['sealed_context'],
                   source='independent-claude-sonnet-reviewer-v3-no-grammar-access')
    if loose < 40:
        raise ValueError('Need >=40 independently authored loosening requests')
    target.write_text(''.join(json.dumps(r, separators=(',', ':'), ensure_ascii=False) + '\n' for r in rows))
    manifest = {'created_utc': datetime.now(timezone.utc).isoformat(), 'rows': len(rows),
        'loosening_count': loose, 'kinds': dict(Counter(r['kind'] for r in rows)),
        'author': 'Separate isolated Claude Sonnet CLI reviewer; sentences and labels independent',
        'cases_sha256': sha(target), 'raw_response_sha256': sha(OUT / 'claude_response.json'),
        'reviewer_prompt_sha256': sha(OUT / 'reviewer_prompt.txt'),
        'invocation_sha256': sha(OUT / 'reviewer-process.json'),
        'contract_sha256': sha(READER / 'contract.json'), 'schema_sha256': sha(READER / 'schema.py'),
        'evaluate_sha256': sha(READER / 'evaluate.py'),
        'system_prompt_sha256': contract['system_prompt_sha256'],
        'reviewer_session_id': raw.get('session_id'), 'reviewer_model_usage': raw.get('modelUsage'),
        'reviewer_cost_usd_reported': raw.get('total_cost_usd'),
        'grammar_seen': False, 'prior_sealed_seen': False, 'model_results_seen': False,
        'ingestion_mutations': ['added fixed public context', 'added provenance source'],
        'policy': 'Evaluation only; never use for training, prompts, thresholds, checkpoint or hyperparameter selection.'}
    write_json(OUT / 'manifest.json', manifest)
    print(json.dumps({k: manifest[k] for k in ('rows', 'loosening_count', 'kinds', 'cases_sha256')}))


def rows_and_verify():
    verify_packet()
    manifest = json.loads((OUT / 'manifest.json').read_text())
    for key, path in [('cases_sha256', OUT / 'cases.jsonl'), ('schema_sha256', READER / 'schema.py'),
                      ('contract_sha256', READER / 'contract.json'), ('evaluate_sha256', READER / 'evaluate.py')]:
        if manifest[key] != sha(path):
            raise ValueError('Frozen evaluation input changed: ' + key)
    return [json.loads(s) for s in (OUT / 'cases.jsonl').read_text().splitlines() if s.strip()]


def spark(resume=False):
    from experiments.mvp.reader.baselines import load_credentials
    from experiments.mvp.reader.evaluate import prompt_messages, score_predictions
    from experiments.mvp.reader.v3.admission import wait_admit
    from experiments.mvp.reader.v3.spark_client import SchemaSparkClient
    from llm.spark.planner_budget import allocation_budget
    from llm.spark.spend import SpendLedger
    wait_admit(600)
    rows = rows_and_verify()
    path = OUT / 'spark-predictions.jsonl'
    if (OUT / 'spark-summary.json').exists():
        summary = json.loads((OUT / 'spark-summary.json').read_text())
        if summary.get('recorded_prediction_count') == len(rows):
            raise ValueError('Completed Spark arm is immutable; do not rerun')
    if path.exists() and not resume:
        raise ValueError('Predictions exist; use --resume for interrupted run')
    predictions = [json.loads(s) for s in path.read_text().splitlines() if s.strip()] if path.exists() else []
    if [p['id'] for p in predictions] != [r['id'] for r in rows[:len(predictions)]]:
        raise ValueError('Prediction checkpoint order differs')
    provenance = {'cases_sha256': sha(OUT / 'cases.jsonl'),
        'schema_sha256': sha(READER / 'schema.py'), 'decoder_sha256': sha(HERE / 'decoder.py'),
        'evaluate_sha256': sha(READER / 'evaluate.py'),
        'system_prompt_sha256': json.loads((READER / 'contract.json').read_text())['system_prompt_sha256'],
        'zero_shot': True, 'prompt_examples': 0, 'purpose': PURPOSE, 'max_output_tokens': 1024,
        'reasoning_effort': 'minimal', 'allocation_usd': '2 including reviewer', 'workers': 1,
        'model': 'muse-spark-1.3', 'schema_constrained': True, 'answer_language': LANGUAGE,
        'decoding_implementation': 'Meta server text.format json_schema, same context answer objects as local logits processor',
        'schema_source': 'https://dev.meta.ai/docs/structured-output', 'no_local_model_inference': True}
    prior_path = OUT / 'spark-provenance.json'
    if prior_path.exists() and json.loads(prior_path.read_text()) != provenance:
        raise ValueError('Frozen Spark baseline inputs changed')
    write_json(prior_path, provenance)
    load_credentials()  # External parent Projects .env -> this process environment only.
    client = SchemaSparkClient(max_retries=0, timeout=60)
    records = [r for r in client.ledger._records() if r.get('purpose') in {PURPOSE, REVIEW_PURPOSE}]
    measured, held = SpendLedger._totals(records)
    # The standard client reserves a full-context worst case (~$1.31) per call;
    # retain that conservative guard within the combined $2 allocation.
    with path.open('a' if resume else 'x') as handle:
        for row in rows[len(predictions):]:
            wait_admit(600)
            item = {'id': row['id']}
            try:
                prompt = '\n\n'.join(m['content'] for m in prompt_messages(row))
                with allocation_budget(client.ledger, allocation_usd=Decimal('2'), prior_job_usd=measured+held):
                    result = client.complete_schema(prompt, context=row['context'], purpose=PURPOSE,
                                             max_output_tokens=1024, reasoning_effort='minimal')
                item.update(prediction=result.text, cost_usd=str(result.cost_usd), request_id=result.request_id)
            except Exception as exc:
                item.update(prediction=None, error=type(exc).__name__)
            handle.write(json.dumps(item, ensure_ascii=False) + '\n')
            handle.flush()
            predictions.append(item)
            if len(predictions) % 16 == 0 or item.get('error'):
                print(f'Spark v3 {len(predictions)}/{len(rows)}' + (' stopped: '+item['error'] if item.get('error') else ''), flush=True)
            if item.get('error'):
                break
    metrics = score_predictions(rows, predictions)
    records = [r for r in client.ledger._records() if r.get('purpose') in {PURPOSE, REVIEW_PURPOSE}]
    measured, held = SpendLedger._totals(records)
    metrics.update(cases_sha256=sha(OUT / 'cases.jsonl'), data_sha256=sha(OUT / 'cases.jsonl'),
        backend='spark', schema_constrained=True, answer_language=LANGUAGE,
        decoder_sha256=sha(HERE / 'decoder.py'), recorded_prediction_count=len(predictions),
        predictions_sha256=sha(path), measured_cost_usd=str(measured), held_cost_usd=str(held))
    write_json(OUT / 'spark-summary.json', metrics)
    print(json.dumps({k: v for k, v in metrics.items() if k not in {'details', 'by_family'}}))


def audit():
    rows = rows_and_verify()
    sealed = {(r['sentence'].casefold().strip(), json.dumps(r['context'], sort_keys=True)) for r in rows}
    seen = set()
    bindings = {}
    for name in ('train.jsonl', 'valid.jsonl'):
        path = HERE / 'data' / name
        bindings[str(path.relative_to(ROOT))] = sha(path)
        for line in path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                seen.add((row['sentence'].casefold().strip(), json.dumps(row['context'], sort_keys=True)))
    report = {'sealed_cases_sha256': sha(OUT / 'cases.jsonl'), 'other_file_sha256': bindings,
        'sentence_context_duplicate_count': len(sealed & seen),
        'prior_sealed_accessed': False, 'test_v3_accessed': False,
        'policy': 'Read-only overlap report; frozen data and labels never changed based on sealed evidence.'}
    write_json(OUT / 'overlap-audit.json', report)
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['packet', 'acquire', 'ingest', 'spark', 'audit'])
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.action == 'spark':
        spark(args.resume)
    else:
        globals()[args.action]()
