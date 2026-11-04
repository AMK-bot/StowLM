"""Offline receipt handoff validation, using recorded native evidence only."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_file, sha256_json
from experiments.mvp.pipeline.copilot.native_handoff import NativeHandoff

HERE = Path(__file__).parent
KEY = 'e47b847abf288cf9ef1d74ee92cb80ad31a6f7c58e903565c8b1aa8a808cc77f'


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    from experiments.mvp.pipeline.lattice import store, gates
    from experiments.mvp.pipeline import lattice
    source = HERE / 'fixtures/hero-final'
    row = json.loads((source / 'store' / (KEY+'.json')).read_text())
    recorded = json.loads((source / 'native' / (KEY+'.json')).read_text())
    store_dir = tmp_path / 'store'
    store_dir.mkdir()
    (store_dir / (KEY+'.json')).write_text(canonical_json(row))
    catalogue = store_dir / 'catalogue.json'
    catalogue.write_text(canonical_json({KEY: sha256_file(store_dir / (KEY+'.json'))}))
    receipt = tmp_path / 'native.json'
    receipt.write_text(canonical_json(recorded))
    identity = recorded['gate']['engine_identity']
    epoch = recorded['gate']['gauges']['physics']['physics_epoch']
    token = (sha256_json(row), identity['engine_manifest_sha256'], epoch, 'current-reader-identity')
    monkeypatch.setattr(store, '_binding_source_matches', lambda row: True)
    monkeypatch.setattr(store, '_current_token', lambda row: token)
    monkeypatch.setattr(gates, 'engine_identity', lambda: deepcopy(identity))
    fallback = Mock(return_value=None)
    monkeypatch.setattr(lattice, 'lookup', fallback)
    adapter = NativeHandoff(store_dir=store_dir, catalogue_sha256=sha256_file(catalogue),
                            receipts={KEY: {'path': receipt, 'sha256': sha256_file(receipt)}})
    return adapter, row, recorded, receipt, fallback, token


def test_actual_recorded_report_reused_with_explicit_label_no_native_call(handoff):
    adapter, row, recorded, receipt, fallback, token = handoff
    before = receipt.read_bytes()
    result = adapter(row['instance_sha256'], [])
    assert result is not None
    assert result['label'] == 'reused-recorded' and result['reused_recorded']
    assert not result['freshly_rechecked']
    assert result['gate'] == recorded['gate']
    assert result['native_handoff']['physics_epoch'] == token[2]
    assert result['instruction_rechecked']
    assert result['instruction_gate_identity'] == token[3]
    assert result['rechecked_utc'] == recorded['gate']['checked_utc']
    assert 'Reused recorded native gate' in result['stored_message']
    fallback.assert_not_called()
    result['gate']['passed'] = False
    assert receipt.read_bytes() == before


@pytest.mark.parametrize('fault', [
    'receipt_tamper', 'source_tamper', 'source_rebound', 'token_missing',
    'epoch_drift', 'engine_drift', 'input_binding', 'taint', 'failed_gate',
    'missing_frame', 'failed_frame', 'unchecked_frame', 'failed_part',
    'false_lifts', 'changed_rule', 'reader_audit', 'changed_token',
])
def test_invalid_handoff_delegates_real_lookup_without_native_calls(handoff, monkeypatch, fault):
    from experiments.mvp.pipeline.lattice import store, gates
    from experiments.mvp.reader import pipeline
    from stowage import frozen
    adapter, row, recorded, receipt, fallback, token = handoff
    fallback.return_value = {'real_lookup_result': True}
    if fault == 'receipt_tamper':
        receipt.write_text(receipt.read_text()+' ')
    elif fault == 'source_tamper':
        path = adapter.store_dir / (KEY+'.json')
        path.write_text(path.read_text()+' ')
    elif fault == 'source_rebound':
        monkeypatch.setattr(store, '_binding_source_matches', lambda row: False)
    elif fault == 'token_missing':
        monkeypatch.setattr(store, '_current_token', lambda row: None)
    elif fault == 'changed_token':
        current = Mock(side_effect=[token, (*token[:2], 'new-epoch', token[3])])
        monkeypatch.setattr(store, '_current_token', current)
    elif fault == 'engine_drift':
        monkeypatch.setattr(gates, 'engine_identity', lambda: dict(recorded['gate']['engine_identity'], engine_version='changed'))
    elif fault == 'taint':
        monkeypatch.setattr(frozen, 'active_stubs', lambda: ['offline-stub'])
    else:
        gate = recorded['gate']
        if fault == 'epoch_drift':
            gate['gauges']['physics']['physics_epoch'] = 'another-epoch'
        elif fault == 'input_binding':
            gate['checker']['provenance']['parents'] = []
        elif fault == 'failed_gate':
            gate['passed'] = False
        elif fault == 'missing_frame':
            gate['gauges']['physics']['frames'].pop()
        elif fault == 'failed_frame':
            gate['gauges']['physics']['frames'][0]['stability_passed'] = False
        elif fault == 'unchecked_frame':
            gate['gauges']['physics']['frames'][0]['strength_passed'] = None
        elif fault == 'failed_part':
            gate['checker']['parts']['moves']['ran'] = False
        elif fault == 'false_lifts':
            gate['lifts'] += 1
        elif fault == 'changed_rule':
            gate['rules'] = [{'envelope': {'schema_version':'stowlm.reader/2', 'constraint':{'type':'close_bay','bay':9}},
                              'passed':True, 'violations':[]}]
        elif fault == 'reader_audit':
            # An authenticated row can carry a valid reader rule; a new current
            # independent audit must still reject it if the plan violates it.
            rule = {'schema_version':'stowlm.reader/2', 'constraint':{'type':'close_bay','bay':9}}
            row['canonical_rules'] = [rule]
            recorded['canonical_rules'] = [rule]
            gate['rules'] = [{'envelope':rule, 'passed':True, 'violations':[]}]
            new_key = store.entry_key(row['instance_sha256'], [rule])
            (adapter.store_dir / (new_key+'.json')).write_text(canonical_json(row))
            (adapter.store_dir / 'catalogue.json').write_text(canonical_json({new_key:sha256_file(adapter.store_dir / (new_key+'.json'))}))
            adapter.catalogue_sha256 = sha256_file(adapter.store_dir / 'catalogue.json')
            adapter.receipts[new_key] = adapter.receipts.pop(KEY)
            monkeypatch.setattr(pipeline, 'intrinsic_violations', lambda *_: ['current-rule-violation'])
        receipt.write_text(canonical_json(recorded))
        for pin in adapter.receipts.values():
            pin['sha256'] = sha256_file(receipt)
    rules = row['canonical_rules']
    result = adapter(row['instance_sha256'], rules)
    assert result == {'real_lookup_result': True}
    fallback.assert_called_once_with(row['instance_sha256'], rules, store_dir=adapter.store_dir)


def test_independent_catalogue_pin_drift_fails_closed(handoff):
    adapter, row, _, _, fallback, _ = handoff
    path = adapter.store_dir / 'catalogue.json'
    path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError, match='independent host pin'):
        adapter(row['instance_sha256'], [])
    fallback.assert_not_called()


def test_absent_handoff_uses_real_lattice_provider(handoff):
    adapter, row, _, _, fallback, _ = handoff
    adapter.receipts.clear()
    assert adapter.lookup(row['instance_sha256'], []) is None
    fallback.assert_called_once_with(row['instance_sha256'], [], store_dir=adapter.store_dir)


def test_preflight_miss_never_runs_native_fallback(handoff):
    adapter, row, _, _, fallback, _ = handoff
    adapter.receipts.clear()
    assert adapter.validated_receipt(row['instance_sha256'], []) is None
    assert adapter.last_miss_reason
    fallback.assert_not_called()


@pytest.mark.parametrize('key', [
    '9381a5a54b03ee22ba61bb73d2b68d07ffebdc604968672bcbd349dcdf84018a',
    '72ccfc883f3db82da140879280c499b280fa1edd179c31f5e69f06056ea32530',
])
def test_both_actual_alternative_receipts_pass_current_independent_rule_audit(handoff, monkeypatch, key):
    from experiments.mvp.pipeline.lattice import store
    adapter, _, _, receipt, fallback, token = handoff
    source = HERE / 'fixtures/hero-final'
    row = json.loads((source / 'store' / (key+'.json')).read_text())
    recorded = json.loads((source / 'native' / (key+'.json')).read_text())
    path = adapter.store_dir / (key+'.json')
    path.write_text(canonical_json(row))
    catalogue = adapter.store_dir / 'catalogue.json'
    catalogue.write_text(canonical_json({key:sha256_file(path)}))
    adapter.catalogue_sha256 = sha256_file(catalogue)
    receipt.write_text(canonical_json(recorded))
    adapter.receipts = {key:{'path':receipt, 'sha256':sha256_file(receipt)}}
    monkeypatch.setattr(store, '_current_token', lambda row:(sha256_json(row), *token[1:]))
    result = adapter.validated_receipt(row['instance_sha256'], row['canonical_rules'])
    assert result is not None, adapter.last_miss_reason
    assert result['canonical_rules'] == row['canonical_rules']
    assert result['gate']['lifts'] in (70, 82)
    fallback.assert_not_called()


def test_authenticated_handoff_miss_backgrounds_but_fixture_miss_does_not(monkeypatch):
    from experiments.mvp.pipeline.copilot.test_tools import tools, propose
    for trusted in (False, True):
        provider = Mock(return_value=None)
        provider.native_authenticated = trusted
        t = tools(lattice=provider)
        rid = propose(t)
        t.confirm_reading(rid)
        start = Mock(return_value={'status':'running', 'job_id':'background-native', 'passed':False})
        monkeypatch.setattr(t, '_start_replan', start)
        result = t.call('plan_lookup', {'rules':t._rules()})
        assert (result['status'] == 'running') is trusted
        assert bool(start.call_count) is trusted


def test_recorded_label_reaches_outward_tool_result(monkeypatch):
    from experiments.mvp.pipeline.copilot.test_tools import tools, propose
    t = tools(lattice=lambda *_: {'plan':{'id':'offline', 'instance_id':'offline', 'call_index':0, 'moves':[]},
                                 'computed_utc':'recorded-date', 'rechecked_utc':'native-date',
                                 'reused_recorded':True})
    rid = propose(t)
    t.confirm_reading(rid)
    monkeypatch.setattr(t, '_stored_report', lambda *_: None)
    monkeypatch.setattr(t, '_gate_plan', lambda *args, **kwargs: {'status':'checked_plan', 'text':'Checked evidence.', 'passed':True})
    result = t.call('plan_lookup', {'rules':t._rules()})
    assert 'Reused recorded native gate' in result['text']
    assert 'native checked at native-date' in result['text']
    assert 'rechecked now' not in result['text']


def test_negotiation_does_not_label_recorded_native_gate_as_fresh(monkeypatch):
    from experiments.mvp.pipeline.copilot.test_tools import tools
    from experiments.mvp.reader import pipeline
    t = tools(lattice=lambda *_: None)
    rule = {'schema_version':'stowlm.reader/2', 'constraint':{'type':'close_bay','bay':1}}
    stored = {'plan':{'id':'offline', 'instance_id':t.instance.id, 'call_index':0, 'moves':[]},
              'computed_utc':'saved-date', 'rechecked_utc':'native-date', 'gate':{},
              'reused_recorded':True}
    monkeypatch.setattr(t, '_lookup', lambda *_: stored)
    monkeypatch.setattr(t, '_stored_report', lambda *args, **kwargs:
                        {'passed':True, 'count':{'weighted_nonproductive':2}})
    monkeypatch.setattr(pipeline, 'intrinsic_violations', lambda *_: [])
    result, = t._negotiation_alternatives([
        {'id':'recorded-option', 'sentence':'Keep bay 1 clear', 'constraint':rule}])
    assert result['checked'] and result['reused_recorded']
    assert result['certificate'] == 'recorded_native_handoff'
    assert 'native checked at native-date' in result['text']
    assert 'fresh full checker' not in result['text']


def test_actual_first_tool_call_preserves_epoch_and_two_recorded_checked_choices():
    """Fresh interpreter catches runner-package import effects, no native solve.

    Reader answers and the refusal are explicitly replay fixtures. The two
    passing native receipts use real source/engine/epoch/input/frame validation.
    """
    import subprocess
    import sys
    script = r'''
from copy import deepcopy
import json
from pathlib import Path
from experiments.mvp.pipeline.copilot.native_runtime import native_caches
from experiments.mvp.pipeline.contract.schema import sha256_file
here = Path('experiments/mvp/pipeline/copilot').resolve()
root = here/'fixtures/hero-verified'
manifest = json.loads((root/'receipt-manifest.json').read_text())
catalogue = json.loads((root/'store/catalogue.json').read_text())
rows = {key:json.loads((root/'store'/(key+'.json')).read_text()) for key in catalogue}
base = next(key for key in rows if key.startswith('e47b'))
refusal = next(key for key in rows if key.startswith('3398'))
choices = [next(key for key in rows if key.startswith(prefix)) for prefix in ('9381','72cc')]
records = json.loads((root/'reader-records.json').read_text())
class RecordedReader:
    def parse(self, sentence, context):
        from experiments.mvp.reader.schema import validate_answer
        if not sentence.strip():
            raw = {'schema_version':'stowlm.reader/2','refusal':{'refused':True,'code':'not_an_instruction','reason':'Empty standing text.'}}
            return validate_answer(json.dumps(raw), context), dict(parser='empty-standing-template',adapter_sha256=None)
        record = next(row for row in records if row['sentence'] == sentence)
        evidence = dict(deepcopy(record['evidence']), exact_response_reused=True,
                        diagnostic_label='Offline recorded Spark-reader output; no provider call.')
        return validate_answer(evidence['raw'],context),evidence
with native_caches():
    from app.stage_physics import physics_drift, physics_epoch
    from experiments.mvp.pipeline.copilot.native_handoff import NativeHandoff
    from experiments.mvp.pipeline.copilot.tools import ToolSet
    from experiments.mvp.pipeline.lattice.store import _current_token
    adapter = NativeHandoff(store_dir=root/'store',catalogue_sha256=manifest['catalogue_sha256'],
        receipts={key:dict(path=here/entry['path'],sha256=entry['sha256']) for key,entry in manifest['entries'].items()
                  if key in choices})
    before = physics_epoch()
    assert physics_drift() is None
    assert all(_current_token(rows[key]) is not None for key in choices)
    class NoNativeFallback:
        native_authenticated = True
        def __call__(self, instance_sha, rules):
            return adapter.validated_receipt(instance_sha,rules)
    pinned_refusal = manifest['entries'][refusal]
    path = here/pinned_refusal['path']
    assert sha256_file(path) == pinned_refusal['sha256']
    recorded_refusal = json.loads(path.read_text())
    tools = ToolSet(rows[base]['instance'], parser=RecordedReader(), plan=rows[base]['plan'],
        lattice=NoNativeFallback(), lookup_fallback=False,
        refusal_lookup=lambda *args:deepcopy(recorded_refusal),
        catalogue_alternatives=[dict(id=key, sentences=rows[key]['sentences'],canonical_rules=rows[key]['canonical_rules']) for key in choices])
    assert tools.call('list_limits',{})['status'] == 'limits'
    assert physics_epoch() == before and physics_drift() is None
    result = tools.call('read_instruction',{'sentence':'\n'.join(rows[refusal]['sentences'])})
    assert result['checked_alternative_count'] == 2, result.get('alternatives')
    assert all(item['checked'] and item['reused_recorded'] for item in result['alternatives'])
    assert physics_epoch() == before and physics_drift() is None
    assert all(_current_token(rows[key]) is not None for key in choices)
    print(json.dumps(dict(checked_alternatives=2,epoch=before,drift=physics_drift())))
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=HERE.parents[3],
                            text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['checked_alternatives'] == 2
