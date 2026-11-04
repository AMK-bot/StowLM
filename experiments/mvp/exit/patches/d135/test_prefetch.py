"""Small CPU-only publication/admission regressions; no physics/model decode."""
import importlib.util
import json
from pathlib import Path
import pytest


ROOT=Path(__file__).resolve().parents[5]
SPEC=importlib.util.spec_from_file_location('d135_prefetch',ROOT/'experiments/mvp/exit/prefetch_replay.py')
MODULE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MODULE)


def test_atomic_replace_publishes_complete_bytes(tmp_path):
    target=tmp_path/'entry.json'
    MODULE.atomic(target,b'{"passed":true}')
    assert json.loads(target.read_bytes())=={'passed':True}
    MODULE.atomic(target,b'{"passed":false,"complete":true}')
    assert json.loads(target.read_bytes())=={'passed':False,'complete':True}
    assert list(tmp_path.glob('*.tmp'))==[]


def test_materialize_only_sampled_keys_and_never_parse_or_overwrite_live_file(tmp_path):
    shelf=tmp_path/'shelf';shelf.mkdir()
    loop=tmp_path/'loop';generation=loop/'gen-001';generation.mkdir(parents=True)
    record=tmp_path/'original.record.json';record.write_text('{}')
    MODULE.write(generation/'replay-selection.json',{'selected_records':[str(record)]})
    (shelf/'wanted.record.json').write_text('{"complete":true}')
    (shelf/'unused.record.json').write_text('{"unused":true}')
    mappings={str(record.resolve()):'wanted'}
    assert MODULE.publish_shelf(shelf,loop,mappings)==1
    target=generation/'data-replay-0.67/replay-current-epoch/wanted.record.json'
    assert json.loads(target.read_text())=={'complete':True}
    # Simulate the sequential runner's non-atomic in-flight write. The sidecar
    # must neither read this partial JSON nor replace it.
    target.write_text('{')
    assert MODULE.publish_shelf(shelf,loop,mappings)==0
    assert target.read_text()=='{'
    assert not list(loop.rglob('unused.record.json'))
    assert not (loop/'gen-002').exists()


def test_active_dataset_is_never_read_for_seeding(tmp_path):
    directory=tmp_path/'gen-000';directory.mkdir()
    selection=directory/'replay-selection.json'
    MODULE.write(selection,{'selected_records':['must-not-open']})
    MODULE.write(directory/'phase-status.json',{'phases':[{'name':'dataset','status':'running'}]})
    assert MODULE.seed_completed_selection(selection,tmp_path,{},'epoch')==0


def test_worker_admission_capped_by_ledger_ram_and_measured_load(monkeypatch):
    from llm.exit import resources
    monkeypatch.setattr(resources,'ledger_totals',lambda _: (11,84,True))
    monkeypatch.setattr(resources,'probe',lambda: {'load_1m':10.9})
    assert MODULE.capacity(6)==5
    monkeypatch.setattr(resources,'probe',lambda: {'load_1m':16.2})
    assert MODULE.capacity(6)==0


def test_prefetch_yields_during_audit_and_decode(tmp_path):
    directory=tmp_path/'gen-000';directory.mkdir()
    status=directory/'phase-status.json'
    for phase in ('audit','fitness','decode_verify_pipeline'):
        MODULE.write(status,{'phases':[{'name':phase,'status':'running'}]})
        assert not MODULE.safe_phase(tmp_path)
    for phase in ('dataset','child_0_train','child_1_dpo'):
        MODULE.write(status,{'phases':[{'name':phase,'status':'running'}]})
        assert MODULE.safe_phase(tmp_path)


def test_same_checker_epoch_cannot_reuse_changed_mask_shelf(tmp_path):
    pins={'src/stowage/checker.py':'physics','src/llm/securing/decoder.py':'old-mask'}
    MODULE.bind_shelf(tmp_path,'same-checker-epoch',pins)
    MODULE.bind_shelf(tmp_path,'same-checker-epoch',pins)
    changed={**pins,'src/llm/securing/decoder.py':'new-mask'}
    with pytest.raises(RuntimeError,match='fresh --out'):
        MODULE.bind_shelf(tmp_path,'same-checker-epoch',changed)


def test_unbound_existing_shelf_is_refused(tmp_path):
    (tmp_path/'old.record.json').write_text('{}')
    with pytest.raises(RuntimeError,match='unbound existing'):
        MODULE.bind_shelf(tmp_path,'epoch',{'mask':'hash'})


def test_shared_owner_refuses_second_output_directory(tmp_path):
    shared=tmp_path/'shared-owner.lock'
    with MODULE.owner_lock(shared):
        with pytest.raises(BlockingIOError):
            with MODULE.owner_lock(shared):
                raise AssertionError('second prefetch owner admitted')
    with MODULE.owner_lock(shared):pass
