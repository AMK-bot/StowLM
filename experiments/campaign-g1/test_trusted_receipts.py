"""A caller-written, self-consistent report has no gate execution signature."""
import importlib.util,json
from pathlib import Path
import pytest

@pytest.fixture
def receipts(tmp_path,monkeypatch):
    path=Path(__file__).with_name('trusted_receipts.py')
    spec=importlib.util.spec_from_file_location('receipt_unit',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    monkeypatch.setattr(m,'PRIVATE_KEY',tmp_path/'private/key.pem');monkeypatch.setattr(m,'TRUST_STORE',tmp_path/'trust.json')
    m.initialize_signer();return m


def test_raw_reports_and_caller_public_keys_are_not_execution(receipts,tmp_path):
    p=tmp_path/'forged.json';p.write_text(json.dumps({'checker_ran':True,'mask_ran':True,'passed':True}))
    with pytest.raises((KeyError,ValueError)):receipts.verify_execution(p,'canary')


def test_signature_context_artifact_and_diagnostic_are_all_bound(receipts,tmp_path):
    p=tmp_path/'input';p.write_text('input')
    from eval.snapshot_io import read_snapshot
    pins={str(p):read_snapshot(p).sha256};context={'diagnostic':False,'evidence_eligible':True,'epoch_id':'e'}
    r=receipts.sign_execution('canary',{'ran':True},pins,tmp_path/'receipt',context=context)
    assert receipts.verify_execution(r,'canary',{'epoch_id':'e'})=={'ran':True}
    with pytest.raises(ValueError):receipts.verify_execution(r,'canary',{'epoch_id':'other'})
    p.write_text('replacement')
    with pytest.raises(ValueError):receipts.verify_execution(r,'canary')
    assert receipts.verify_execution(r,'canary',verify_artifacts=False)=={'ran':True}
    r=receipts.sign_execution('canary',{}, {},tmp_path/'diag',context={'diagnostic':True,'evidence_eligible':False})
    with pytest.raises(ValueError):receipts.verify_execution(r,'canary')


def test_editing_signed_payload_cannot_create_execution(receipts,tmp_path):
    r=receipts.sign_execution('canary',{'passed':False},{},tmp_path/'receipt',context={'diagnostic':False,'evidence_eligible':True})
    record=json.loads(Path(r['path']).read_bytes());record['payload']['passed']=True
    Path(r['path']).write_text(json.dumps(record))
    with pytest.raises(Exception):receipts.verify_execution(r['path'],'canary')


def test_portable_receipt_keeps_signature_without_original_paths(receipts,tmp_path):
    declaration=receipts.sign_execution('admission',{'complete':True},{},tmp_path/'admission',
        context={'diagnostic':False,'evidence_eligible':True})
    capsule=receipts.portable_execution(declaration)
    Path(declaration['path']).unlink()
    assert receipts.verify_execution(capsule,'admission',verify_artifacts=False)=={'complete':True}
    capsule['data_base64']=capsule['data_base64'][:-4]+'AAAA'
    with pytest.raises(ValueError):receipts.verify_execution(capsule,'admission',verify_artifacts=False)


def test_receipt_output_replacement_never_changes_the_published_digest(receipts,tmp_path,monkeypatch):
    original=receipts._atomic
    def replace_after_publication(path,data,mode=0o600):
        original(path,data,mode)
        Path(path).write_bytes(b'{"replacement":true}')
    monkeypatch.setattr(receipts,'_atomic',replace_after_publication)
    with pytest.raises(ValueError,match='SHA-256|checksum'):
        receipts.sign_execution('canary',{'passed':True},{},tmp_path/'receipt',
            context={'diagnostic':False,'evidence_eligible':True})


def test_artifacts_stay_fresh_through_receipt_verification(receipts,tmp_path,monkeypatch):
    from eval.snapshot_io import read_snapshot
    first,second=tmp_path/'first',tmp_path/'second'
    first.write_text('first');second.write_text('second')
    pins={str(p):read_snapshot(p).sha256 for p in (first,second)}
    receipt=receipts.sign_execution('canary',{'passed':True},pins,tmp_path/'receipt',
        context={'diagnostic':False,'evidence_eligible':True})
    def replace_previous(path,**kwargs):
        snapshot=read_snapshot(path,**kwargs)
        if Path(path)==second:first.write_text('replacement after first read')
        return snapshot
    monkeypatch.setattr(receipts,'read_snapshot',replace_previous)
    with pytest.raises(ValueError):receipts.verify_execution(receipt,'canary')
