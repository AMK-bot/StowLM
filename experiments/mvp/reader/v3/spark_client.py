"""Experiment-only Spark Responses adapter with server-side schema decoding.

Preserves the shared SparkClient transport/accounting unchanged. A dedicated
transport proxy injects text.format before forwarding the request; no global
urllib mutation, key argument, post-processing or output repair is involved.
API contract: https://dev.meta.ai/docs/structured-output (2026-10-31).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
import urllib.request

from llm.spark.client import SparkClient


class SchemaSparkClient(SparkClient):
    def complete_schema(self, prompt, *, context, purpose, max_output_tokens=1024,
                        reasoning_effort='minimal'):
        from experiments.mvp.reader.v3.decoder import context_json_schema

        # Give this one inherited method a private globals dictionary, replacing
        # only urllib.request.Request. All original locking, credit, reservation,
        # redaction, retries and usage settlement run in the existing implementation.
        # No credential is copied into these globals or emitted into provenance.
        from types import FunctionType
        schema = context_json_schema(context)
        original = SparkClient.complete

        def request(url, data=None, **kwargs):
            body = json.loads(data)
            body['text'] = {'format': {'type': 'json_schema', 'name': 'reader_v3',
                                      'strict': False, 'schema': schema}}
            return urllib.request.Request(url, data=json.dumps(body).encode(), **kwargs)

        proxy = SimpleNamespace(request=SimpleNamespace(Request=request,
                  urlopen=urllib.request.urlopen), error=__import__('urllib.error', fromlist=['error']))
        namespace = dict(original.__globals__, urllib=proxy)
        method = FunctionType(original.__code__, namespace, original.__name__,
                              original.__defaults__, original.__closure__)
        method.__kwdefaults__ = original.__kwdefaults__
        return method(self, prompt, purpose=purpose, max_output_tokens=max_output_tokens,
                      reasoning_effort=reasoning_effort)
