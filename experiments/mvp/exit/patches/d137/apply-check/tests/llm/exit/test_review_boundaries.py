import importlib.util
import json
from pathlib import Path

import pytest

from llm.exit.identity import IdentityGuard
from llm.exit.smoke_once import attempt_path, claim_attempt
from stowage.model.fixtures import build_instance


@pytest.mark.parametrize('split',['test','val','showcase'])
def test_inline_or_copied_heldout_instance_is_excluded_by_identity(tmp_path,split):
    instance=build_instance()
    corpus=tmp_path/'corpus'
    target=corpus/split/'tiny'/'map.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(instance.model_dump(mode='json'),indent=4))
    guard=IdentityGuard(corpus)
    # No path and differently serialized bytes: exactly the inline bypass.
    with pytest.raises(ValueError,match='held-out'):
        guard.require_allowed(instance,raw_sha='different-inline-byte-hash')
    # Copied file with a changed JSON layout also remains held-out.
    with pytest.raises(ValueError,match='held-out'):
        guard.require_allowed(instance,input_path=tmp_path/'copied-train.json')
    if split=='val':
        guard.require_allowed(instance,role='loss_val')
    # Altered payload cannot evade immutable root-seed identity.
    changed=instance.model_copy(update={'id':'changed-id'})
    with pytest.raises(ValueError,match='held-out'):
        guard.require_allowed(changed)


@pytest.mark.parametrize('split',['test','val'])
def test_inline_dataset_record_cannot_enter_winner_or_replay_training(tmp_path,monkeypatch,split):
    from llm.exit import data
    instance=build_instance()
    corpus=tmp_path/'experiments/mvp/corpus'
    target=corpus/split/'map.json'
    target.parent.mkdir(parents=True)
    target.write_text(instance.model_dump_json(indent=2))
    guard=IdentityGuard(corpus)
    from llm.exit import identity
    monkeypatch.setattr(identity,'identity_guard',lambda *a:guard)
    record=tmp_path/'inline.record.json'
    record.write_text(json.dumps(dict(instance=instance.model_dump(mode='json'),
        plan=dict(id='plan',instance_id=instance.id,call_index=instance.call_index),report={})))
    # Guard runs before report admission or tokenizer/model work.
    with pytest.raises(ValueError,match='held-out'):
        data.build_training_dataset([record],[],tmp_path/'out',tokenizer=object())


def test_independent_output_directories_share_gpu_job_lock(tmp_path,monkeypatch):
    from llm.exit import resources as r
    monkeypatch.setattr(r,'ROOT',tmp_path)
    fleet=tmp_path/'experiments/fleet'
    fleet.mkdir(parents=True)
    (fleet/'ledger.md').write_text('')
    marker=tmp_path/'experiments/mvp/runs/sft-pilot2-small/polish_summary.json'
    marker.parent.mkdir(parents=True)
    marker.write_text('{}')
    monkeypatch.setattr(r,'probe',lambda:dict(load_1m=0,memory_free_percent=99))
    first,second=tmp_path/'first-run',tmp_path/'second-run'
    first.mkdir();second.mkdir()
    with r.reservation(first,cores=1,ram_gb=1,gpu=True,estimate_s=1):
        assert r.ledger_totals((fleet/'ledger.md').read_text()) == (1.,1.,True)
        with pytest.raises(RuntimeError,match='shared GPU/job lock'):
            with r.reservation(second,cores=1,ram_gb=1,gpu=True,estimate_s=1):
                pytest.fail('second GPU job admitted')
    assert 'mvp-exit/local' not in (fleet/'ledger.md').read_text()


def test_smoke_claim_persists_across_restarts_and_is_content_keyed(tmp_path):
    parent=tmp_path/'parent';parent.mkdir()
    (parent/'adapters.safetensors').write_bytes(b'weights1')
    marker=attempt_path(parent,{'source':'one'},tmp_path/'attempts')
    with claim_attempt(marker):pass
    assert json.loads(marker.read_text())['status']=='done'
    with pytest.raises(RuntimeError,match='already attempted'):
        with claim_attempt(marker):pytest.fail('duplicate smoke')
    assert attempt_path(parent,{'source':'two'},tmp_path/'attempts') != marker
    (parent/'adapters.safetensors').write_bytes(b'weights2')
    assert attempt_path(parent,{'source':'one'},tmp_path/'attempts') != marker


def test_restarted_watcher_never_repeats_existing_current_key_claim(tmp_path,monkeypatch):
    source=Path(__file__).resolve().parents[3]/'experiments/mvp/exit/wait_for_smoke.py'
    spec=importlib.util.spec_from_file_location('exit_test_watcher',source)
    watcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(watcher)
    out=tmp_path/'out';out.mkdir()
    monkeypatch.setattr(watcher,'OUT',out)
    monkeypatch.setattr(watcher,'ROOT',tmp_path)
    parent=tmp_path/'experiments/mvp/sft/pilot2/adapter';parent.mkdir(parents=True)
    (parent/'adapters.safetensors.provenance.json').write_text('{}')
    pilot=tmp_path/'experiments/mvp/runs/sft-pilot2-small/polish_summary.json'
    pilot.parent.mkdir(parents=True);pilot.write_text('{}')
    current=tmp_path/'current-key.json';current.write_text('{"status":"done"}')
    from llm.exit import smoke_once,runner
    monkeypatch.setattr(smoke_once,'attempt_path',lambda *a:current)
    monkeypatch.setattr(runner,'source_pins',lambda:{})
    (tmp_path/'compute.json').write_text('{}')
    monkeypatch.setattr(watcher.subprocess,'Popen',lambda *a,**k:pytest.fail('duplicate launched'))
    assert watcher.main()==0


def test_stale_compute_does_not_consume_current_key_smoke_after_capacity_refusal(tmp_path,monkeypatch):
    from types import SimpleNamespace
    source=Path(__file__).resolve().parents[3]/'experiments/mvp/exit/wait_for_smoke.py'
    spec=importlib.util.spec_from_file_location('exit_test_watcher_retry',source)
    watcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(watcher)
    out=tmp_path/'out';out.mkdir();(out/'compute.json').write_text('{"status":"completed"}')
    monkeypatch.setattr(watcher,'OUT',out);monkeypatch.setattr(watcher,'ROOT',tmp_path)
    parent=tmp_path/'experiments/mvp/sft/pilot2/adapter';parent.mkdir(parents=True)
    (parent/'adapters.safetensors.provenance.json').write_text('{}')
    pilot=tmp_path/'experiments/mvp/runs/sft-pilot2-small/polish_summary.json'
    pilot.parent.mkdir(parents=True);pilot.write_text('{}')
    current=tmp_path/'new-key.json'
    from llm.exit import smoke_once,runner
    monkeypatch.setattr(smoke_once,'attempt_path',lambda *a:current)
    monkeypatch.setattr(runner,'source_pins',lambda:{})
    clock=iter([0,1,2]);monkeypatch.setattr(watcher.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(watcher.time,'sleep',lambda _:None)
    calls=[]
    def launch(*a,**kw):
        calls.append(a)
        if len(calls)==2:current.write_text('{"status":"done"}')
        return SimpleNamespace(pid=123,wait=lambda:1 if len(calls)==1 else 0)
    monkeypatch.setattr(watcher.subprocess,'Popen',launch)
    assert watcher.watch()==0 and len(calls)==2
