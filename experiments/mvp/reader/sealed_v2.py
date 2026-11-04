"""Acquire an isolated Claude-authored holdout and run fixed, sealed evaluation.

Only contract.json and vocabulary.md are supplied to the reviewer. This program
never imports a generator or reads sealed-v1. It never runs local model inference.
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
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
OUT = HERE / 'sealed-v2'
PURPOSE = 'reader-d219-sealed-v2-zeroshot'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def packet():
    OUT.mkdir(exist_ok=True)
    target = OUT / 'reviewer_prompt.txt'
    if target.exists():
        raise ValueError('Reviewer packet already frozen')
    instruction = '''You are an independent Claude contract-reader test author. You have never
been shown a training grammar, training sentences, previous sealed test, model
predictions, or evaluation results. Use only the supplied public contract and
synthetic world vocabulary. Do not use tools or outside sources.

Create EXACTLY 128 NEW, distinct synthetic planner sentences and independently
author their expected structured answers. Return JSONL only, no markdown. Each
line has keys id, sentence, expected, kind, family, loosening. IDs are
sealed-v2-001 through sealed-v2-128. expected is the complete strict envelope
specified by the contract. kind is the constraint type or refusal code; family
is a short descriptive category; loosening is a JSON boolean consistent with
the expected refusal code. The fixed context will be added by the ingestion
script; do not emit context or messages. Never emit a model answer other than
your independently judged expected label.

Distribution: 16 close_bay, 16 max_tiers, 16 dg_keep_away, 16 one_port_per_hatch,
40 loosening refusals, 8 ambiguous refusals, 8 unsupported refusals, and 8
not_an_instruction refusals. Exactly 128 in total. At least 30 must explicitly
request relaxation/removal/override; 40 are requested here. Write varied,
natural, short operational sentences with diverse syntax, polite questions,
nonliteral references where clear, and contrasts of information versus commands.
For loosening include mixed tightening/relaxation clauses, contextual exceptions,
temporary permissions, reopening, reduced DG separation, increased tiers, and
mixed-port permissions. The supplied priority determines labels. Avoid merely
enumerating a single fixed template. Keep all data synthetic; no real company,
customer, vessel, or personnel data. Use only the public fixed synthetic world.
Read the contract carefully for subtle scope, whole-class/division validity,
physical maxima, missing scope, distinct restrictions, uncertainty, and 20ft
bay step semantics. Do not claim feasibility from successful parsing. You author
every sentence and label; ingestion must not rewrite them.

PUBLIC CONTRACT follows:\n'''
    target.write_text(instruction + (HERE / 'contract.json').read_text()
                      + '\nPUBLIC SYNTHETIC WORLD VOCABULARY:\n'
                      + (HERE / 'vocabulary.md').read_text())
    write_json(OUT / 'packet-manifest.json', {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'contract_sha256': sha(HERE / 'contract.json'),
        'vocabulary_sha256': sha(HERE / 'vocabulary.md'),
        'reviewer_prompt_sha256': sha(target),
        'policy': 'No grammar, prior sealed set, predictions, or results supplied.'})
    print('Frozen Claude reviewer packet created; contents remain sealed.')


def acquire():
    if (OUT / 'claude_response.json').exists():
        raise ValueError('Never overwrite a reviewer response')
    if not (OUT / 'reviewer_prompt.txt').exists():
        packet()
    verify_packet()
    # Safe mode suppresses CLAUDE.md, memories, skills, hooks, and custom agents.
    # Empty tools plus strict empty MCP prevent any filesystem or network tools.
    command = ['claude', '--print', '--model', 'opus', '--output-format', 'json',
               '--safe-mode', '--restricted', '--setting-sources', '',
               '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
               '--no-session-persistence', '--system-prompt-snapshot', 'off',
               '--system-prompt', 'You are an independent synthetic contract-reader evaluation author. Follow the user packet exactly. You have no tools.']
    with tempfile.TemporaryDirectory(prefix='reader-sealed-v2-', dir='/private/tmp') as cwd:
        with (OUT / 'reviewer_prompt.txt').open() as stdin, (OUT / 'claude_response.json').open('x') as stdout, (OUT / 'claude_stderr.txt').open('x') as stderr:
            process = subprocess.run(command, cwd=cwd, stdin=stdin, stdout=stdout,
                                     stderr=stderr, check=False)
    write_json(OUT / 'invocation.json', {'argv': command, 'exit_code': process.returncode,
        'cwd': 'new empty /private/tmp/reader-sealed-v2-* directory',
        'stdin_sha256': sha(OUT / 'reviewer_prompt.txt'),
        'safe_mode': True, 'tools': [], 'mcp_servers': [], 'session_persistence': False})
    print(json.dumps({'claude_exit_code': process.returncode, 'raw_response_sha256': sha(OUT / 'claude_response.json')}))
    if process.returncode:
        raise ValueError('Claude acquisition failed; inspect sealed diagnostics without disclosing content')


def ingest():
    from experiments.mvp.reader.schema import canonicalize, system_prompt
    target = OUT / 'cases.jsonl'
    if target.exists():
        raise ValueError('Never overwrite a sealed set')
    verify_packet()
    raw = json.loads((OUT / 'claude_response.json').read_text())
    if raw.get('is_error') or not raw.get('result'):
        raise ValueError('Reviewer did not produce a successful result')
    rows = [json.loads(line) for line in raw['result'].splitlines() if line.strip()]
    if len(rows) < 120 or len({r['sentence'].casefold().strip() for r in rows}) != len(rows):
        raise ValueError('Need at least 120 unique reviewer-authored sentences')
    if [r['id'] for r in rows] != [f'sealed-v2-{i:03}' for i in range(1, len(rows)+1)]:
        raise ValueError('Reviewer IDs must be contiguous')
    contract = json.loads((HERE / 'contract.json').read_text())
    if hashlib.sha256(system_prompt().encode()).hexdigest() != contract['system_prompt_sha256']:
        raise ValueError('Runtime system prompt differs from independent reviewer contract')
    loose = 0
    for index, row in enumerate(rows):
        if set(row) != {'id', 'sentence', 'expected', 'kind', 'family', 'loosening'}:
            raise ValueError(f'Unexpected row keys at index {index}')
        try:
            label = canonicalize(row['expected'], contract['sealed_context'])
        except Exception:
            raise ValueError(f'Invalid independently authored expected envelope at index {index}') from None
        is_loose = label.get('refusal', {}).get('code') == 'loosening'
        kind = label.get('constraint', {}).get('type') or label.get('refusal', {}).get('code')
        if type(row['loosening']) is not bool or row['loosening'] != is_loose or row['kind'] != kind:
            raise ValueError(f'Inconsistent reviewer metadata at index {index}')
        loose += is_loose
        row['context'] = contract['sealed_context']
        row['source'] = 'independent-claude-opus-reviewer-v2-no-grammar-access'
    if loose < 30:
        raise ValueError('At least 30 independently authored loosening requests required')
    target.write_text(''.join(json.dumps(r, ensure_ascii=False, separators=(',', ':')) + '\n' for r in rows))
    manifest = {'created_utc': datetime.now(timezone.utc).isoformat(),
        'author': 'Separate Claude Opus CLI process; sentence and expected label both authored by reviewer',
        'rows': len(rows), 'loosening_count': loose,
        'kinds': dict(Counter(r['kind'] for r in rows)),
        'cases_sha256': sha(target), 'raw_response_sha256': sha(OUT / 'claude_response.json'),
        'reviewer_prompt_sha256': sha(OUT / 'reviewer_prompt.txt'),
        'invocation_sha256': sha(OUT / 'invocation.json'),
        'contract_sha256': sha(HERE / 'contract.json'),
        'schema_sha256': sha(HERE / 'schema.py'),
        'evaluate_sha256': sha(HERE / 'evaluate.py'),
        'system_prompt_sha256': contract['system_prompt_sha256'],
        'reviewer_session_id': raw.get('session_id'),
        'reviewer_model_usage': raw.get('modelUsage'),
        'reviewer_cost_usd_reported': raw.get('total_cost_usd'),
        'grammar_seen': False, 'prior_sealed_seen': False, 'model_results_seen': False,
        'ingestion_mutations': ['added fixed public context', 'added provenance source'],
        'policy': 'Evaluation only; never use for training, prompts, vocabulary, thresholds, checkpoint or hyperparameter selection.'}
    write_json(OUT / 'manifest.json', manifest)
    print(json.dumps({k: manifest[k] for k in ('rows', 'loosening_count', 'kinds', 'cases_sha256')}))


def verify_packet():
    manifest = json.loads((OUT / 'packet-manifest.json').read_text())
    for key, path in [('contract_sha256', HERE / 'contract.json'),
                      ('vocabulary_sha256', HERE / 'vocabulary.md'),
                      ('reviewer_prompt_sha256', OUT / 'reviewer_prompt.txt')]:
        if manifest[key] != sha(path):
            raise ValueError('Frozen reviewer input changed: ' + key)


def rows_and_verify():
    manifest = json.loads((OUT / 'manifest.json').read_text())
    for key, path in [('cases_sha256', OUT / 'cases.jsonl'), ('schema_sha256', HERE / 'schema.py'),
                      ('contract_sha256', HERE / 'contract.json'),
                      ('evaluate_sha256', HERE / 'evaluate.py')]:
        if manifest[key] != sha(path):
            raise ValueError('Frozen evaluation input changed: ' + key)
    return [json.loads(s) for s in (OUT / 'cases.jsonl').read_text().splitlines() if s.strip()]


def spark(resume=False):
    from experiments.mvp.reader.baselines import load_credentials
    from experiments.mvp.reader.evaluate import prompt_messages, score_predictions
    from llm.spark.client import SparkClient
    from llm.spark.planner_budget import allocation_budget
    from llm.spark.spend import SpendLedger
    rows = rows_and_verify()
    path = OUT / 'spark-predictions.jsonl'
    if path.exists() and not resume:
        raise ValueError('Predictions exist; --resume only after interrupted frozen run')
    predictions = [json.loads(s) for s in path.read_text().splitlines() if s.strip()] if path.exists() else []
    if [p['id'] for p in predictions] != [r['id'] for r in rows[:len(predictions)]]:
        raise ValueError('Prediction checkpoint order differs')
    load_credentials()  # External parent .env -> environment only; never printed.
    client = SparkClient(max_retries=0, timeout=60)
    prior_records = [r for r in client.ledger._records() if r.get('purpose') == PURPOSE]
    measured, held = SpendLedger._totals(prior_records)
    write_json(OUT / 'spark-provenance.json', {'cases_sha256': sha(OUT / 'cases.jsonl'),
        'schema_sha256': sha(HERE / 'schema.py'), 'evaluate_sha256': sha(HERE / 'evaluate.py'),
        'system_prompt_sha256': json.loads((HERE / 'contract.json').read_text())['system_prompt_sha256'],
        'zero_shot': True, 'prompt_examples': 0,
        'purpose': PURPOSE, 'max_output_tokens': 1024, 'reasoning_effort': 'minimal',
        'allocation_usd': '2', 'workers': 1, 'model': 'muse-spark-1.3',
        'no_local_model_inference': True})
    with path.open('a' if resume else 'x') as handle:
        for row in rows[len(predictions):]:
            prompt = '\n\n'.join(m['content'] for m in prompt_messages(row))
            item = {'id': row['id']}
            try:
                with allocation_budget(client.ledger, allocation_usd=Decimal('2'), prior_job_usd=measured + held):
                    result = client.complete(prompt, purpose=PURPOSE,
                                             max_output_tokens=1024, reasoning_effort='minimal')
                item.update(prediction=result.text, cost_usd=str(result.cost_usd), request_id=result.request_id)
            except Exception as exc:
                item.update(prediction=None, error=type(exc).__name__)
            handle.write(json.dumps(item, ensure_ascii=False) + '\n')
            handle.flush()
            predictions.append(item)
            print(f'sealed-v2 Spark {len(predictions)}/{len(rows)}', flush=True)
            if item.get('error'):
                break
    metrics = score_predictions(rows, predictions)
    records = [r for r in client.ledger._records() if r.get('purpose') == PURPOSE]
    measured, held = SpendLedger._totals(records)
    metrics.update(cases_sha256=sha(OUT / 'cases.jsonl'), data_sha256=sha(OUT / 'cases.jsonl'),
                   backend='spark', recorded_prediction_count=len(predictions),
                   measured_cost_usd=str(measured), held_cost_usd=str(held))
    write_json(OUT / 'spark-summary.json', metrics)
    print(json.dumps({k: v for k, v in metrics.items() if k not in {'details', 'by_family'}}))


def score(predictions, name):
    from experiments.mvp.reader.evaluate import read_jsonl, score_predictions
    if '/' in name or name in {'spark', '.', '..'}:
        raise ValueError('Use a fresh non-Spark arm basename')
    output = OUT / f'{name}-summary.json'
    if output.exists():
        raise ValueError('Never overwrite a sealed evaluation arm')
    metrics = score_predictions(rows_and_verify(), read_jsonl(predictions))
    metrics.update(cases_sha256=sha(OUT / 'cases.jsonl'), data_sha256=sha(OUT / 'cases.jsonl'),
                   predictions_sha256=sha(predictions))
    write_json(output, metrics)
    print(json.dumps({k: v for k, v in metrics.items() if k not in {'details', 'by_family'}}))


def audit(paths):
    rows = rows_and_verify()
    sealed = {(r['sentence'].casefold().strip(), json.dumps(r['context'], sort_keys=True)) for r in rows}
    seen = set()
    count = 0
    bindings = {}
    for path in paths:
        bindings[str(path)] = sha(path)
        for line in path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                seen.add((row['sentence'].casefold().strip(), json.dumps(row['context'], sort_keys=True)))
                count += 1
    report = {'sealed_cases_sha256': sha(OUT / 'cases.jsonl'), 'other_file_sha256': bindings,
              'other_rows': count, 'sentence_context_duplicate_count': len(sealed & seen),
              'prior_sealed_accessed': False}
    write_json(OUT / 'overlap-audit.json', report)
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['packet', 'acquire', 'ingest', 'spark', 'score', 'audit'])
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--predictions', type=Path)
    parser.add_argument('--arm')
    parser.add_argument('--other', type=Path, nargs='+')
    args = parser.parse_args()
    if args.action == 'score' and (args.predictions is None or not args.arm):
        parser.error('score requires --predictions and --arm')
    if args.action == 'audit' and not args.other:
        parser.error('audit requires explicit new data files via --other')
    if args.action == 'spark': spark(args.resume)
    elif args.action == 'score': score(args.predictions, args.arm)
    elif args.action == 'audit': audit(args.other)
    else: globals()[args.action]()
