"""Offline transport and accounting checks; never use a real credential."""
import base64
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.error

from PIL import Image
import pytest
import requests

from experiments.mvp.pipeline.yard_vision.clients import (
    LedgerSparkClient, VisionBudget, VisionError, YardVisionClient, parse_segments,
)
from llm.spark.client import SparkCreditNotReady, SparkIncompleteError, SparkProtocolError
from experiments.mvp.pipeline.contract.schema import sha256_json
from stowage.model.yard import YardState
from stowage.conventions import make_container_id


SAM_TEXT = '<0f>0<|box;x1=2;y1=3;x2=20;y2=25;w=32;h=32|><|mask;x=0;y=0;data=10,12,~encoded|>'


@pytest.fixture
def picture(tmp_path):
    path = tmp_path / 'generated.png'
    Image.new('RGB', (32, 32), 'red').save(path)
    return path


def response(data, status=200):
    return SimpleNamespace(text=json.dumps(data), status_code=status, headers={'x-request-id': 'synthetic-request'})


def sam_response():
    return response({'model': 'sam-3.1', 'status': 'completed', 'usage': {'images_processed': 1},
                     'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': SAM_TEXT}]}]})


def test_read_sends_two_images_and_only_supplied_metadata(picture, tmp_path):
    captured = {}

    class FakeSpark:
        def complete_image(self, prompt, images, **kwargs):
            captured.update(prompt=prompt, images=images, kwargs=kwargs)
            return SimpleNamespace(text=json.dumps({'complete_view': True, 'quay_clear': True,
                'stacks': [{'key': 'A-001-01', 'height': 1, 'length_ft': 20, 'boxes': [None]}]}),
                cost_usd=Decimal('0.01'), request_id='fake-spark')

    def post(url, **kwargs):
        assert kwargs['json']['model'] == 'sam-3.1'
        assert kwargs['json']['input'][0]['content'][1]['type'] == 'input_image'
        assert kwargs['timeout'] == (20, 180)
        return sam_response()

    client = YardVisionClient(spark=FakeSpark(), api_key='fake-key', http_post=post,
                              budget_path=tmp_path / 'calls.jsonl')
    result = client.read(picture, layout={'id': 'yard', 'blocks': []}, inventory=[{'id': 'ABC', 'length_ft': 20}])
    assert len(captured['images']) == 2
    assert all(image.startswith('data:image/png;base64,') for image in captured['images'])
    assert 'Never fill an id by elimination' in captured['prompt']
    assert '"id": "ABC"' in captured['prompt']
    assert result['stacks'][0]['boxes'] == [None]
    assert result['cost_usd'] == 0.0125
    assert result['mode'] == 'live'
    assert result['segments'][0]['box'] == [2, 3, 20, 25]
    assert result['segments'][0]['mask']['data'] == '10,12,~encoded'
    assert [row.stage for row in client.records] == ['yard_vision.sam', 'yard_vision.spark']
    assert sum(row.cost_usd for row in client.records) == 0.0125
    assert all(row.source_sha256 and row.prompt_sha256 for row in client.records)
    assert 'fake-key' not in (tmp_path / 'calls.jsonl').read_text()


def test_missing_flags_remain_uncertain(picture, tmp_path):
    spark = SimpleNamespace(complete_image=lambda *args, **kwargs: SimpleNamespace(
        text='{"stacks": []}', cost_usd=Decimal(0), request_id='fake'))
    client = YardVisionClient(spark=spark, api_key='fake', http_post=lambda *args, **kwargs: sam_response(),
                              budget_path=tmp_path / 'calls.jsonl')
    result = client.read(picture, layout={}, inventory=[])
    assert result['complete_view'] is False
    assert result['quay_clear'] is False


def test_bad_spark_json_retains_attempt_and_paid_records(picture, tmp_path):
    spark = SimpleNamespace(complete_image=lambda *args, **kwargs: SimpleNamespace(
        text='This is not JSON', cost_usd=Decimal('0.003'), request_id='fake'))
    client = YardVisionClient(spark=spark, api_key='fake', http_post=lambda *args, **kwargs: sam_response(),
                              budget_path=tmp_path / 'calls.jsonl')
    with pytest.raises(VisionError, match='not a JSON'):
        client.read(picture, layout={}, inventory=[])
    assert client.last_attempt['spark']['text'] == 'This is not JSON'
    assert client.last_attempt['sam']['raw']['model'] == 'sam-3.1'
    assert sum(row.cost_usd for row in client.records) == 0.0055
    json.dumps(client.last_attempt)


def test_real_probe_special_tokens_parse():
    path = Path(__file__).resolve().parents[2] / 'meta-tools/vision/sam-response.json'
    data = json.loads(path.read_text())
    segment = parse_segments(data['output'][0]['content'][0]['text'])[0]
    assert segment['box'] == [40, 73, 216, 192]
    assert segment['mask']['data'].startswith('120,177,')


@pytest.mark.parametrize('text', [
    '<|box;x1=20;y1=3;x2=2;y2=25;w=32;h=32|>',
    '<|box;bad|>', '<|mask;x=0;y=0;data=unpaired|>',
])
def test_malformed_segments_refuse(text):
    with pytest.raises(VisionError):
        parse_segments(text)


def test_repaint_png_and_generated_label(picture, tmp_path):
    buffer = io.BytesIO()
    Image.new('RGB', (64, 64), 'blue').save(buffer, format='WEBP')
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs['json'])
        return response({'data': [{'b64_json': base64.b64encode(buffer.getvalue()).decode()}]})

    client = YardVisionClient(api_key='fake', http_post=post, budget_path=tmp_path / 'calls.jsonl')
    output = tmp_path / 'photo.png'
    result = client.repaint(picture, output)
    assert result['scene_preservation'] == 'unchecked'
    assert result['generated'] is True
    assert result['cost_usd'] == 0.01
    assert result['synthesis_manifest_sha256'] is None
    assert result['prompt'] == calls[0]['prompt']
    assert calls[0]['n'] == 1
    assert all(value is False for value in calls[0]['tool_enablement'].values())
    with Image.open(output) as image:
        assert image.format == 'PNG'
        assert image.getpixel((0, 0)) == (255, 255, 255)
    with pytest.raises(VisionError, match='already exists'):
        client.repaint(picture, output)
    assert len(calls) == 1


def test_repaint_manifest_exact_counts_order_and_prompt_audit(picture, tmp_path):
    buffer = io.BytesIO()
    Image.new('RGB', (64, 64), 'blue').save(buffer, format='PNG')
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs['json'])
        return response({'data': [{'b64_json': base64.b64encode(buffer.getvalue()).decode()}]})

    ids = [make_container_id('QZXU', n) for n in range(3)]
    source = YardState.model_validate({'yard_id': 'synthetic', 'stacks': [
        {'key': 'A-001-01', 'length_ft': 20, 'boxes': ids[:2]},
        {'key': 'A-003-01', 'length_ft': 40, 'boxes': ids[2:]}], 'quay_buffer': []})
    client = YardVisionClient(api_key='fake', http_post=post, budget_path=tmp_path / 'calls.jsonl')
    result = client.repaint(picture, tmp_path / 'manifest-photo.png', source_state=source)
    manifest = result['synthesis_manifest']
    assert manifest['container_count'] == 3
    assert manifest['stack_container_count'] == 3
    assert manifest['stack_count'] == 2
    assert manifest['stacks'][0]['height'] == 2
    assert manifest['stacks'][0]['boxes_bottom_to_top'] == ids[:2]
    assert manifest['stacks'][1]['length_ft'] == 40
    assert result['synthesis_manifest_sha256'] == sha256_json(manifest)
    assert result['prompt_sha256'] == hashlib.sha256(calls[0]['prompt'].encode()).hexdigest()
    assert result['prompt'] == calls[0]['prompt']
    assert 'EXACTLY 3 shipping containers' in result['prompt']
    assert 'EXACTLY 2 nonempty yard stacks' in result['prompt']
    assert ids[0] in result['prompt']
    # Synthesis ground truth does not change the separate reader prompt.
    from experiments.mvp.pipeline.yard_vision.clients import read_prompt
    assert ids[0] not in read_prompt({}, [])


def test_unknown_vision_call_holds_cap_and_never_retries(picture, tmp_path):
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout('secret-body-never-printed')

    client = YardVisionClient(api_key='fake', http_post=post, cap_usd=0.0025,
                              budget_path=tmp_path / 'calls.jsonl')
    with pytest.raises(VisionError, match='billing unknown'):
        client.segment(picture)
    with pytest.raises(VisionError, match='cap reached'):
        client.segment(picture)
    assert len(calls) == 1
    assert json.loads((tmp_path / 'calls.jsonl').read_text().splitlines()[-1])['cost_kind'] == 'conservative_hold'


class HTTPResult:
    headers = {'x-request-id': 'test-request'}

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.data).encode()


def spark_client(tmp_path, ready=True):
    compute = tmp_path / 'compute.md'
    compute.write_text('')
    return LedgerSparkClient(api_key='offline-fake-key', ledger_path=tmp_path / 'ledger.jsonl',
        compute_path=compute, credit_ready=ready, max_retries=0)


def spark_data(status='completed'):
    return {'model': 'muse-spark-1.3', 'id': 'fake-response', 'status': status,
        'usage': {'input_tokens': 100, 'output_tokens': 20},
        'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK'}]}]}


def test_multimodal_spark_uses_shared_ledger(tmp_path, monkeypatch):
    captured = []

    def transport(request, **kwargs):
        captured.append(json.loads(request.data))
        return HTTPResult(spark_data())

    monkeypatch.setattr('urllib.request.urlopen', transport)
    client = spark_client(tmp_path)
    answer = client.complete_image('Read picture', ['data:image/png;base64,abc'], max_output_tokens=128)
    assert answer.text == 'OK'
    assert answer.cost_usd == Decimal('0.00021')
    assert captured[0]['input'][0]['content'][1]['type'] == 'input_image'
    rows = [json.loads(line) for line in client.ledger.path.read_text().splitlines()]
    assert [row['phase'] for row in rows] == ['reservation', 'settlement']
    assert rows[1]['cost_kind'] == 'measured'
    assert 'spark-jsonl' in (tmp_path / 'compute.md').read_text()


def test_spark_credit_closed_no_transport_or_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: pytest.fail('Unexpected network'))
    client = spark_client(tmp_path, ready=False)
    with pytest.raises(SparkCreditNotReady):
        client.complete_image('Read', ['data:image/png;base64,abc'])
    assert not client.ledger.path.exists()


def test_spark_timeout_retains_shared_hold_no_retry(tmp_path, monkeypatch):
    calls = []

    def transport(*args, **kwargs):
        calls.append(1)
        raise urllib.error.URLError('do not log this')

    monkeypatch.setattr('urllib.request.urlopen', transport)
    client = spark_client(tmp_path)
    with pytest.raises(Exception, match='billing unknown'):
        client.complete_image('Read', ['data:image/png;base64,abc'])
    rows = [json.loads(line) for line in client.ledger.path.read_text().splitlines()]
    assert rows[-1]['cost_kind'] == 'conservative_hold'
    assert rows[-1]['cost_usd'] == rows[0]['cost_usd']
    assert len(calls) == 1


def test_spark_incomplete_settles_measured_usage(tmp_path, monkeypatch):
    data = spark_data('incomplete')
    data['incomplete_details'] = {'reason': 'max_output_tokens'}
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: HTTPResult(data))
    client = spark_client(tmp_path)
    with pytest.raises(SparkIncompleteError):
        client.complete_image('Read', ['data:image/png;base64,abc'])
    row = json.loads(client.ledger.path.read_text().splitlines()[-1])
    assert row['cost_kind'] == 'measured'
    assert row['status'] == 'incomplete'


def test_spark_bad_usage_retains_hold(tmp_path, monkeypatch):
    data = spark_data()
    data['usage']['input_tokens'] = -1
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: HTTPResult(data))
    client = spark_client(tmp_path)
    with pytest.raises(SparkProtocolError):
        client.complete_image('Read', ['data:image/png;base64,abc'])
    row = json.loads(client.ledger.path.read_text().splitlines()[-1])
    assert row['cost_kind'] == 'conservative_hold'
