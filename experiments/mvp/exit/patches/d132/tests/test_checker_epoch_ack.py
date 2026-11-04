"""CPU-only D134 innermost checker protocol tests; live source stays frozen."""
from contextlib import contextmanager
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import pickle
import sys
from types import ModuleType

import pytest

STAGE=Path(__file__).resolve().parents[1]


def load(name):
    spec=importlib.util.spec_from_file_location('_d134_'+name,STAGE/(name+'.py'))
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def protocol(tmp_path,monkeypatch):
    watcher=load('checker_watchdog');events=[];expected={'checker':'expected'}
    module=ModuleType('llm.exit.outcome_epoch')
    class Mismatch(BaseException):pass
    module.OutcomeEpochMismatch=Mismatch
    def pre():events.append('pre');return dict(expected)
    def post():events.append('post');return dict(expected)
    @contextmanager
    def guard(pins):
        events.append('guard');yield {'imports':{},'resources':{}}
    module.process_snapshot=pre;module.snapshot=post;module.guarded_outcome_reads=guard
    monkeypatch.setitem(sys.modules,'llm.exit.outcome_epoch',module)
    request=tmp_path/'request';request.write_bytes(b'data')
    response=tmp_path/'response';bootstrap=tmp_path/'bootstrap.json'
    bootstrap.write_text(json.dumps(dict(expected_outcome_epoch=expected,request_id='nonce',sys_path=list(sys.path))))
    def unpickle(stream):
        events.append('unpickle')
        assert events[:2]==['pre','guard']
        return dict(timeout_s=1,checker=None,instance=None,plan=None)
    original_load=pickle.load
    monkeypatch.setattr(watcher.pickle,'load',unpickle)
    monkeypatch.setattr(watcher,'_load_checker',lambda descriptor:lambda *a:{'passed':True})
    monkeypatch.setattr(watcher,'_unavailable_reason',lambda report:None)
    return watcher,module,expected,events,request,response,bootstrap,original_load


def test_child_ack_precedes_model_unpickle_and_has_nonce_and_post_snapshot(protocol):
    watcher,module,expected,events,request,response,bootstrap,read_result=protocol
    watcher._child_main(request,response,bootstrap)
    with response.open('rb') as stream:result=read_result(stream)
    assert events==['pre','guard','unpickle','post']
    assert result['available'] is True
    assert watcher._validate_epoch_ack(result,expected,'nonce') is None
    assert watcher._validate_epoch_ack(result,expected,'foreign-nonce')=='outcome_epoch_ack_mismatch'


@pytest.mark.parametrize('kind',['checker_exception','watchdog_expired','outcome_epoch_mismatch'])
def test_unavailable_causes_are_honest_and_never_emit_reports(protocol,monkeypatch,kind):
    watcher,module,expected,events,request,response,bootstrap,read_result=protocol
    error={'checker_exception':ValueError('checker bug'),'watchdog_expired':watcher.CheckExpired(),
           'outcome_epoch_mismatch':module.OutcomeEpochMismatch()}[kind]
    def broken(*a):raise error
    monkeypatch.setattr(watcher,'_load_checker',lambda descriptor:broken)
    watcher._child_main(request,response,bootstrap)
    with response.open('rb') as stream:result=read_result(stream)
    assert result['available'] is False and result['reason']==kind
    assert 'report' not in result


@pytest.mark.parametrize('bad_field',['loaded_pre','loaded_post','request_id','imports','resources'])
def test_parent_rejects_missing_wrong_or_foreign_epoch_acks(bad_field):
    watcher=load('checker_watchdog');expected={'source':'hash'}
    ack=dict(protocol='exit-check-outcome-ack-v1',request_id='nonce',loaded_pre=expected,
             loaded_post=expected,imports={},resources={})
    assert watcher._validate_epoch_ack({'epoch_ack':ack},expected,'nonce') is None
    ack[bad_field]={'foreign':'hash'} if bad_field!='request_id' else 'foreign'
    assert watcher._validate_epoch_ack({'epoch_ack':ack},expected,'nonce')=='outcome_epoch_ack_mismatch'
    assert watcher._validate_epoch_ack({},None,'nonce') is None  # Unchanged D129 training mode.


def test_compiled_source_bytes_are_checked_even_if_disk_is_reverted_later(tmp_path):
    module=load('outcome_epoch');path=tmp_path/'src/stowage/probe.py';path.parent.mkdir(parents=True)
    original=b'value=1\n';path.write_bytes(original)
    expected={'src/stowage/probe.py':module._sha(original)}
    path.write_bytes(b'value=2\n')
    with module.guarded_outcome_reads(expected,root=tmp_path):
        loader=importlib.machinery.SourceFileLoader('_probe',str(path))
        with pytest.raises(module.OutcomeEpochMismatch):loader.get_code('_probe')
    path.write_bytes(original)
    assert module._sha(path.read_bytes())==expected['src/stowage/probe.py']


def test_source_and_primary_resource_read_guards_restore_and_return_immutable_bytes(tmp_path):
    module=load('outcome_epoch');path=tmp_path/'params/fleet.toml';path.parent.mkdir(parents=True)
    original=b'physical_limit=1\n';path.write_bytes(original)
    expected={'params/fleet.toml':module._sha(original)}
    saved=importlib.machinery.SourceFileLoader.get_code
    with module.guarded_outcome_reads(expected,root=tmp_path) as consumed:
        with path.open('rb') as immutable:
            path.write_bytes(b'physical_limit=2\n')
            assert immutable.read()==original
        with pytest.raises(module.OutcomeEpochMismatch):path.read_bytes()
        assert consumed['resources']==expected
    assert importlib.machinery.SourceFileLoader.get_code is saved


def test_child_thread_environment_matches_expected_one_thread_policy():
    source=(STAGE/'checker_watchdog.py').read_text()
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS',
                 'MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        assert name+"='1'" in source
