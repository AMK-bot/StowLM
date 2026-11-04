"""Synthetic baseline only, shared Spark accounting plus a locked $2 task cap."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import json
from types import FunctionType, SimpleNamespace
import urllib.error
import urllib.request

from .schema import context_json_schema, prompt_messages

PURPOSE = 'intake-lora-baseline'
CAP = Decimal('2')


class TaskLedger:
    """Hold the shared ledger lock across task reservation and HTTP settlement."""
    def __init__(self, shared):
        self.shared = shared

    @contextmanager
    def locked(self):
        with self.shared.locked():
            yield self

    def spent(self):
        rows = [r for r in self.shared._records() if r.get('purpose') == PURPOSE]
        actual, held = self.shared._totals(rows)
        return actual + held

    def check_capacity(self, reservation):
        from llm.spark.spend import SpendCapExceeded
        self.shared.check_capacity(reservation)
        if self.spent() + reservation > CAP:
            raise SpendCapExceeded('intake baseline $2 cap would be exceeded')

    def append(self, record):
        if record.get('phase') != 'settlement':
            self.check_capacity(Decimal(record['cost_usd']))
        self.shared.append(record)


def schema_completion(client, messages, schema, *, max_output_tokens=256):
    """Reuse the v3 request proxy; all shared client safety/accounting stays intact."""
    from llm.spark.client import SparkClient
    original = SparkClient.complete

    def request(url, data=None, **kwargs):
        body = json.loads(data)
        body['input'] = messages
        body['text'] = {'format': {'type': 'json_schema', 'name': 'intake_row',
                                   'strict': False, 'schema': schema}}
        return urllib.request.Request(url, data=json.dumps(body).encode(), **kwargs)

    proxy = SimpleNamespace(request=SimpleNamespace(Request=request, urlopen=urllib.request.urlopen),
                            error=urllib.error)
    method = FunctionType(original.__code__, dict(original.__globals__, urllib=proxy),
                          original.__name__, original.__defaults__, original.__closure__)
    method.__kwdefaults__ = original.__kwdefaults__
    return method(client, 'Synthetic intake baseline', purpose=PURPOSE,
                  max_output_tokens=max_output_tokens, reasoning_effort='minimal')


def run_spark(rows, *, client=None, on_prediction=None):
    if client is None:
        from llm.spark.client import SparkClient
        client = SparkClient(max_retries=0)
        client.ledger = TaskLedger(client.ledger)
    elif not isinstance(client.ledger, TaskLedger):
        client.ledger = TaskLedger(client.ledger)
    predictions = []
    for row in rows:
        response = schema_completion(client, prompt_messages(row['text'], row['context']),
                                     context_json_schema(row['context']))
        predictions.append({'id': row['id'], 'prediction': response.text,
                            'cost_usd': str(response.cost_usd), 'request_id': response.request_id})
        if on_prediction:
            on_prediction(predictions[-1])
    return predictions
