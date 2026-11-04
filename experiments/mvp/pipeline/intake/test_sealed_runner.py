"""One-run scorer tests use toy bytes only, never the sealed fixture directory."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest

from . import score_sealed as s
from .campaign_v7 import save_once
from experiments.mvp.pipeline.contract.schema import sha256_file


@pytest.fixture
def toy(tmp_path,monkeypatch):
    pins={'source_files':{'toy.py':'frozen'},'prompt_sha256':'frozen'}
    monkeypatch.setattr(s,'code_pins',lambda:pins.copy())
    cases=[]
    for i in range(6):
        name=f'sealed_{i+1:02d}'
        case=tmp_path/name
        case.mkdir()
        for file in ('documents.json','expected.json','base_instance.json'):
            (case/file).write_text('[]')
        cases.append({'split':name,'rows':12,'files':{p.name:sha256_file(p) for p in case.iterdir()}})
    save_once(tmp_path/'blind_freeze.json',{'frozen_before_tuning':True,'frozen_before_scoring':True,'cases':cases})
    (tmp_path/'blind_freeze.sha256').write_text(sha256_file(tmp_path/'blind_freeze.json'))
    s.freeze(tmp_path)
    return tmp_path,pins


def test_claim_is_once_even_after_a_crash(toy):
    root,_=toy
    assert len(s.claim_run(root)['cases'])==6
    with pytest.raises(ValueError,match='already invoked'):
        s.claim_run(root)


def test_concurrent_claim_has_one_winner(toy):
    root,_=toy
    def attempt():
        try:
            s.claim_run(root)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _:attempt(),range(2)))==1


def test_code_change_refuses_before_claim(toy):
    root,pins=toy
    pins['prompt_sha256']='changed'
    with pytest.raises(ValueError,match='Frozen code'):
        s.claim_run(root)
    assert not (root/'RUN_CLAIM.json').exists()


def test_input_change_refuses_before_claim(toy):
    root,_=toy
    (root/'sealed_01/documents.json').write_text('changed')
    with pytest.raises(ValueError,match='input changed'):
        s.claim_run(root)
    assert not (root/'RUN_CLAIM.json').exists()


def test_freeze_and_verification_never_decode_case_inputs(toy,monkeypatch):
    root,_=toy
    original=Path.read_text
    def read(path,*args,**kwargs):
        assert path.parent==root,'Case bytes must only be hashed before Task 3.1'
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    assert s.verify_frozen(root)


def test_runtime_rejects_external_document_before_paid_call(toy,monkeypatch):
    root,_=toy
    # Replace the claim with a toy manifest to isolate runtime path validation.
    monkeypatch.setattr(s,'claim_run',lambda _: {'cases':[{'split':'sealed_01','files':{'documents.json':'x'}}]})
    monkeypatch.setattr(s,'verify_frozen',lambda _:None)
    monkeypatch.setattr(s,'_documents',lambda _: [{'id':'external','path':'/private/tmp/outside'}])
    monkeypatch.setattr(s,'record_case',lambda _:pytest.fail('paid call must not occur'))
    with pytest.raises(ValueError,match='unfrozen path'):
        s.run(root)
