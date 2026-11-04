"""D271: one disclosed Spark-only sealed rerun after the 256-token cap bug.

Run once with --execute-once. No retries, prompt changes or adoption writes.
The shared JSONL is authoritative; its docs summary is left to the leader.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import urllib.error

from llm.spark.client import (CONTEXT_TOKENS, SparkClient, SparkError,
                              SparkHTTPError, SparkIncompleteError, SparkProtocolError)
from llm.spark.spend import SpendCapExceeded, SpendError, Usage, calculate_cost

from . import spark_baseline as baseline
from .evaluate import HERE, ROOT, arm_report, digest
from .schema import context_json_schema, prompt_messages
from .score_final import SEALED

PURPOSE = 'intake-lora-sealed-spark-rerun-d271'
CAP = Decimal('2.00')
MAX_OUTPUT_TOKENS = 2048
ORIGINAL = HERE / 'results/sealed-final'
OUTPUT = HERE / 'results/sealed-final-spark-rerun'
REASON = ('D271: rerun after the original Spark arm stopped on its first call at the '
          '256-token output cap; only the cap changes to 2048, reasoning stays minimal')
RESERVATION = calculate_cost(Usage(CONTEXT_TOKENS - MAX_OUTPUT_TOKENS,
                                   MAX_OUTPUT_TOKENS, 0))


class OrderLedger(baseline.TaskLedger):
    def totals(self):
        rows = [r for r in self.shared._records() if r.get('purpose') == PURPOSE]
        return self.shared._totals(rows)

    def spent(self):
        actual, held = self.totals()
        return actual + held

    def check_capacity(self, reservation):
        self.shared.check_capacity(reservation)
        if self.spent() + reservation > CAP:
            raise SpendCapExceeded('D271 Spark rerun $2 cap would be exceeded')

    def append(self, record):
        entry = {**record, 'purpose': PURPOSE}
        if entry.get('phase') != 'settlement':
            self.check_capacity(Decimal(entry['cost_usd']))
        self.shared.append(entry)


def verify_inputs(freeze_path, original_path, sealed_path):
    frozen = json.loads(Path(freeze_path).read_text())
    original = json.loads(Path(original_path).read_text())
    schema_hash = digest(HERE / 'schema.py')
    if (schema_hash != original['prompt_sha256']
            or schema_hash != original['schema_sha256']):
        raise ValueError('original prompt/schema hash mismatch')
    # Also bind the unchanged request builder and scoring implementation.
    for name in ('spark_baseline.py', 'evaluate.py', 'decoder.py'):
        path = HERE / name
        registered = frozen.get('files_sha256', {}).get(str(path.relative_to(ROOT)))
        if registered is not None and digest(path) != registered:
            raise ValueError('frozen runtime hash mismatch')
    if original.get('freeze_sha256', digest(freeze_path)) != digest(freeze_path):
        raise ValueError('original freeze hash mismatch')
    registration = frozen['sealed_registration']
    if registration['count'] != 200:
        raise ValueError('registered sealed population must be 200')
    if original.get('data_sha256', registration['sha256']) != registration['sha256']:
        raise ValueError('original sealed hash mismatch')
    raw = Path(sealed_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != registration['sha256']:
        raise ValueError('sealed bytes differ from registration')
    # Decode precisely the checked buffer, as score_final does; never reopen it.
    rows = [json.loads(line) for line in raw.decode().splitlines()]
    if len(rows) != 200:
        raise ValueError('sealed count mismatch')
    if len({r['id'] for r in rows}) != 200:
        raise ValueError('duplicate sealed id')
    return rows, frozen, original


def _client():
    client = SparkClient(max_retries=0)
    # The order forbids existing-file edits except its explicit shared ledger.
    # Keep global locking, binding, caps and durable append; defer docs summary.
    client.ledger._sync_compute = lambda: None
    return client


def run_spark(rows, *, client=None, on_prediction=None):
    client = _client() if client is None else client
    client.max_retries = 0
    shared = client.ledger.shared if isinstance(client.ledger, baseline.TaskLedger) else client.ledger
    if not isinstance(client.ledger, OrderLedger):
        client.ledger = OrderLedger(shared)
    predictions = []
    attempted = incomplete = transport = consecutive = 0
    error = None
    for row in rows:
        prediction = {'id': row['id'], 'rerun': True, 'rerun_reason': REASON,
                      'original_max_output_tokens': 256, 'max_output_tokens': MAX_OUTPUT_TOKENS}
        stop = False
        try:
            with client.ledger.locked():
                client.ledger.check_capacity(RESERVATION)
                attempted += 1
                response = baseline.schema_completion(
                    client, prompt_messages(row['text'], row['context']),
                    context_json_schema(row['context']), max_output_tokens=MAX_OUTPUT_TOKENS)
            prediction.update(prediction=response.text, cost_usd=str(response.cost_usd),
                              request_id=response.request_id, status='completed')
            consecutive = 0
        except SparkIncompleteError as exc:
            incomplete += 1
            consecutive = 0
            prediction.update(prediction='', decoding_error='incomplete', status='incomplete',
                              incomplete_reason=exc.reason, cost_usd=str(exc.cost_usd),
                              request_id=exc.request_id)
        except SpendCapExceeded:
            error = 'SpendCapExceeded'
            break
        except (urllib.error.URLError, TimeoutError, OSError, SparkError) as exc:
            is_transport = (isinstance(exc, (urllib.error.URLError, TimeoutError, OSError))
                            or type(exc) is SparkError
                            or isinstance(exc, SparkHTTPError) and (exc.status_code == 429
                                                                   or exc.status_code >= 500))
            if is_transport:
                transport += 1
                consecutive += 1
                stop = consecutive >= 3
                error = 'three_consecutive_transport_failures' if stop else None
                prediction.update(prediction='', decoding_error='transport_error', status='transport_error')
            else:
                error = type(exc).__name__
                stop = True
                prediction.update(prediction='', decoding_error=error, status='error')
        except (SpendError, ValueError, TypeError, KeyError) as exc:
            error = type(exc).__name__
            stop = True
            prediction.update(prediction='', decoding_error=error, status='error')
        predictions.append(prediction)
        if on_prediction:
            on_prediction(prediction)
        if stop:
            break
    with client.ledger.locked():
        actual, held = client.ledger.totals()
    result = {'predictions': predictions, 'attempted_count': attempted,
              'incomplete_count': incomplete, 'transport_failure_count': transport,
              'spend_usd': str(actual), 'held_usd': str(held),
              'committed_usd': str(actual + held)}
    if error:
        result['execution_error'] = error
    return result


def execute(*, freeze_path=ORIGINAL / 'freeze.json', original_path=ORIGINAL / 'spark.json',
            sealed_path=SEALED, output=OUTPUT, client=None):
    output = Path(output)
    # The directory is the consumed marker; an interrupted run cannot resume.
    output.mkdir(parents=True, exist_ok=False)
    rows, frozen, original = verify_inputs(freeze_path, original_path, sealed_path)
    started = datetime.now(timezone.utc).isoformat()
    with (output / 'run.json').open('x') as stream:
        stream.write(json.dumps({'rerun': True, 'rerun_reason': REASON, 'started_utc': started,
                    'freeze_sha256': digest(freeze_path), 'original_report_sha256': digest(original_path),
                    'data_sha256': frozen['sealed_registration']['sha256'],
                    'prompt_sha256': original['prompt_sha256'], 'schema_sha256': original['schema_sha256'],
                    'original_max_output_tokens': 256, 'max_output_tokens': MAX_OUTPUT_TOKENS,
                    'reasoning_effort': 'minimal', 'purpose': PURPOSE, 'cap_usd': str(CAP),
                    'reservation_usd': str(RESERVATION), 'retry_policy': 'never',
                    'adoption_decision': 'cut; unchanged'}, indent=2) + '\n')
    with (output / 'spark.predictions.jsonl').open('x') as stream:
        def persist(prediction):
            stream.write(json.dumps(prediction, ensure_ascii=False) + '\n')
            stream.flush()
        try:
            result = run_spark(rows, client=client, on_prediction=persist)
        except (SparkError, SpendError, OSError, ValueError) as exc:
            result = {'predictions': [], 'attempted_count': 0, 'incomplete_count': 0,
                      'transport_failure_count': 0, 'execution_error': type(exc).__name__}
    predictions = result.pop('predictions')
    report = arm_report(rows, predictions, arm='spark',
                        data_sha256=frozen['sealed_registration']['sha256'])
    report.update(result, rerun=True, rerun_reason=REASON, original_max_output_tokens=256,
                  max_output_tokens=MAX_OUTPUT_TOKENS, reasoning_effort='minimal',
                  freeze_sha256=digest(freeze_path), original_report_sha256=digest(original_path),
                  purpose=PURPOSE, cap_usd=str(CAP), retry_policy='never',
                  adoption_decision='cut; unchanged', started_utc=started,
                  completed_utc=datetime.now(timezone.utc).isoformat())
    with (output / 'spark.json').open('x') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-once', action='store_true')
    args = parser.parse_args()
    if not args.execute_once:
        parser.error('explicit --execute-once required; consumes this rerun')
    from .admission import wait_admit
    wait_admit()
    report = execute()
    print(json.dumps(report, indent=2))
    return 0 if report['attempted_count'] == 200 else 1


if __name__ == '__main__':
    raise SystemExit(main())
