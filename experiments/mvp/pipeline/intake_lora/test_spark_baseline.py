import json

from llm.spark.client import SparkClient

from .schema import context_json_schema, prompt_messages
from .spark_baseline import TaskLedger, run_spark


def test_schema_spark_uses_shared_client_ledger_and_target_free_context(tmp_path, monkeypatch):
    compute = tmp_path / 'compute.md'
    compute.write_text('')
    client = SparkClient('offline-fake-key', compute_path=compute,
                         ledger_path=tmp_path / 'calls.jsonl', credit_ready=True, max_retries=0)
    requests = []
    class Reply:
        headers = {}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps({'id': 'fake', 'model': 'muse-spark-1.3', 'status': 'completed',
                 'usage': {'input_tokens': 120, 'output_tokens': 20},
                 'output': [{'type': 'message', 'content': [{'type': 'output_text',
                           'text': '{"refusal":{"code":"MISSING_FIELD"}}'}]}]}).encode()
    def transport(request, **kwargs):
        requests.append(json.loads(request.data))
        return Reply()
    monkeypatch.setattr('urllib.request.urlopen', transport)
    context = {'pods': ['SGSIN'], 'iso_types': ['22G1'], 'dg_classes': ['3']}
    rows = [{'id': 'fake', 'text': 'Synthetic incomplete booking', 'context': context,
             'expected': {'secret': 'must never reach schema or prompt'}}]
    results = run_spark(rows, client=client)
    assert results[0]['prediction'] == '{"refusal":{"code":"MISSING_FIELD"}}'
    assert len(requests) == 1
    assert requests[0]['input'] == prompt_messages(rows[0]['text'], context)
    assert requests[0]['text']['format']['schema'] == context_json_schema(context)
    assert 'must never reach' not in json.dumps(requests[0])
    assert isinstance(client.ledger, TaskLedger)
    assert 0 < client.ledger.spent() < 2
    assert len((tmp_path / 'calls.jsonl').read_text().splitlines()) == 2
