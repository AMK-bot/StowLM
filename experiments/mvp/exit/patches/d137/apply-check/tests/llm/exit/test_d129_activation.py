"""CPU-only activation fencing fixtures; fake repo files, never real activation."""
import fcntl
import importlib.util
import json
from pathlib import Path

import pytest


ROOT=Path(__file__).resolve().parents[3]


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    if isinstance(value,dict):path.write_text(json.dumps(value))
    else:path.write_bytes(value)


@pytest.fixture
def activation(tmp_path,monkeypatch):
    source=ROOT/'experiments/mvp/exit/patches/d129/apply_recovery.py'
    spec=importlib.util.spec_from_file_location('_d129_activation_test',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    root=tmp_path/'repo';out=root/'experiments/mvp/exit/deployment'
    stage=root/'experiments/mvp/exit/patches/d129'
    runner='src/llm/exit/runner.py';oldpins={runner:module.sha(b'old runner')}
    write(root/runner,b'old runner')
    reviewed={}
    for name in sorted(module.APPROVED_DRIFT):
        original=('old '+name).encode();current=('reviewed '+name).encode()
        oldpins[name]=module.sha(original)
        write(root/name,current)
        reviewed[name]=dict(old_sha256=module.sha(original),current_sha256=module.sha(current))
    write(out/'gen-000/instances.json',dict(instances=['map.json'],source_pins=oldpins))
    write(out/'gen-000/cases/000/selection.json',{})
    write(out/'SOURCE_DRIFT_D124.json',dict(files=reviewed))
    for path in (out/'deployment.cancel',out/'.deployment.lock',out/'.runner.lock',
                 root/'experiments/mvp/exit/.gpu.lock'):
        write(path,b'')
    contents={runner:b'new reviewed runner',
              'experiments/mvp/exit/deploy_when_ready.py':b'new gated queue'}
    digest=module.digest({p:module.sha(v) for p,v in contents.items()})
    monkeypatch.setattr(module,'bundle',lambda path:(dict(contents),digest))
    return module,root,out,contents,digest,reviewed


def test_activation_writes_only_owned_targets_and_no_fake_generation_artifacts(activation):
    module,root,out,contents,digest,reviewed=activation
    production={name:(root/name).read_bytes() for name in reviewed}
    record=module.apply(root,out,digest)
    assert record['gpu_started'] is False
    assert record['nonowned_reviewed_drift']=={name:row['current_sha256'] for name,row in reviewed.items()}
    for name,content in contents.items():assert (root/name).read_bytes()==content
    assert production=={name:(root/name).read_bytes() for name in reviewed}
    assert (out/'d129-original-source-backup/src/llm/exit/runner.py').read_bytes()==b'old runner'
    for forbidden in ('gen-000/summary.json','epoch-boundary.json','survivor.json','STARTED'):
        assert not (out/forbidden).exists()
    assert (root/'experiments/mvp/exit/patches/d129/activation-manifest.json').exists()


@pytest.mark.parametrize('name', ['.deployment.lock','.runner.lock','gpu'])
def test_active_old_owner_prevents_all_source_changes(activation,name):
    module,root,out,contents,digest,reviewed=activation
    path=root/'experiments/mvp/exit/.gpu.lock' if name=='gpu' else out/name
    with path.open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError,match='old owner active'):module.apply(root,out,digest)
    assert (root/'src/llm/exit/runner.py').read_bytes()==b'old runner'


def test_missing_cancel_or_partial_selection_refuses_activation(activation):
    module,root,out,contents,digest,reviewed=activation
    (out/'deployment.cancel').unlink()
    with pytest.raises(RuntimeError,match='cancellation'):module.apply(root,out,digest)
    write(out/'deployment.cancel',b'')
    (out/'gen-000/cases/000/selection.json').unlink()
    with pytest.raises(RuntimeError,match='selection is incomplete'):module.apply(root,out,digest)
    assert (root/'src/llm/exit/runner.py').read_bytes()==b'old runner'


def test_unreviewed_nonowned_drift_refuses_before_source_write(activation):
    module,root,out,contents,digest,reviewed=activation
    write(root/'src/llm/stowlm/unreviewed.py',b'not reviewed')
    with pytest.raises(RuntimeError,match='unreviewed non-owned drift'):module.apply(root,out,digest)
    assert (root/'src/llm/exit/runner.py').read_bytes()==b'old runner'


def test_reviewed_drift_requires_exact_current_hash_including_reverted_files(activation):
    module,root,out,contents,digest,reviewed=activation
    name=next(iter(reviewed))
    write(root/name,('old '+name).encode())  # No longer appears in changed-path set.
    with pytest.raises(RuntimeError,match='reviewed.*changed|reviewed.*hash|reviewed.*mismatch'):
        module.apply(root,out,digest)
    assert (root/'src/llm/exit/runner.py').read_bytes()==b'old runner'


def test_missing_reviewed_artifact_entry_refuses_before_writing(activation):
    module,root,out,contents,digest,reviewed=activation
    reviewed.pop(next(iter(reviewed)))
    write(out/'SOURCE_DRIFT_D124.json',dict(files=reviewed))
    with pytest.raises((RuntimeError,ValueError),match='reviewed|drift'):
        module.apply(root,out,digest)
    assert (root/'src/llm/exit/runner.py').read_bytes()==b'old runner'


def test_unknown_owned_bytes_are_not_overwritten(activation):
    module,root,out,contents,digest,reviewed=activation
    write(root/'src/llm/exit/runner.py',b'concurrent unknown edit')
    with pytest.raises(RuntimeError,match='unknown owned source bytes'):module.apply(root,out,digest)
    assert (root/'src/llm/exit/runner.py').read_bytes()==b'concurrent unknown edit'


def test_bundle_digest_change_refuses_before_any_source_mutation(activation):
    module,root,out,contents,digest,reviewed=activation
    with pytest.raises(RuntimeError,match='reviewed recovery bundle changed'):
        module.apply(root,out,'wrong reviewed digest')
    assert (root/'src/llm/exit/runner.py').read_bytes()==b'old runner'


def test_production_drift_during_activation_cannot_publish_success(activation,monkeypatch):
    module,root,out,contents,digest,reviewed=activation
    name=next(iter(reviewed));calls=[]
    def bundle(path):
        calls.append(path)
        if len(calls)==2:write(root/name,b'concurrent production edit')
        return dict(contents),digest
    monkeypatch.setattr(module,'bundle',bundle)
    with pytest.raises(RuntimeError,match='drift|changed|reviewed'):
        module.apply(root,out,digest)
    assert not (root/'experiments/mvp/exit/patches/d129/activation-manifest.json').exists()


@pytest.fixture
def d126(activation):
    module,root,out,contents,digest,reviewed=activation
    name=module.D126_TARGET
    original=(ROOT/'experiments/mvp/fixwindow/d126/watchdog-application/backups'/name).read_bytes()
    current=(ROOT/'experiments/mvp/fixwindow/d121-watchdog/stability-cache-timeout.py').read_bytes()
    assert module.sha(original)==module.D126_BEFORE
    assert module.sha(current)==module.D126_AFTER
    manifest=json.loads((out/'gen-000/instances.json').read_text())
    manifest['source_pins'][name]=module.sha(original)
    write(out/'gen-000/instances.json',manifest)
    write(root/name,current)
    receipt=root/'experiments/mvp/fixwindow/d126/watchdog-application/RECEIPT.json'
    write(receipt,(ROOT/'experiments/mvp/fixwindow/d126/watchdog-application/RECEIPT.json').read_bytes())
    return activation,receipt,original,current


def test_known_d126_four_line_watchdog_rethrow_is_admitted_by_exact_receipt(d126):
    activation,receipt,original,current=d126
    module,root,out,contents,digest,reviewed=activation
    record=module.apply(root,out,digest)
    assert record['gpu_started'] is False
    assert record['nonowned_reviewed_drift'][module.D126_TARGET]==module.D126_AFTER
    assert (root/module.D126_TARGET).read_bytes()==current  # Never edited by ExIt.


@pytest.mark.parametrize('failure', ['missing','unapplied','failed','wrong_decision',
    'before_hash','after_hash','duplicate_target','missing_target','old_epoch_hash','current_hash'])
def test_optional_d126_requires_applied_unique_exact_hash_receipt(d126,failure):
    activation,receipt,original,current=d126
    module,root,out,contents,digest,reviewed=activation
    record=json.loads(receipt.read_text())
    entry=next(row for row in record['preflight']['files'] if row['target']==module.D126_TARGET)
    if failure=='missing':receipt.unlink()
    elif failure=='unapplied':record['applied']=False
    elif failure=='failed':record['passed']=False
    elif failure=='wrong_decision':record['decision']='unreviewed'
    elif failure=='before_hash':entry['before']='wrong-original'
    elif failure=='after_hash':entry['after']='wrong-current'
    elif failure=='duplicate_target':record['preflight']['files'].append(dict(entry))
    elif failure=='missing_target':record['preflight']['files'].remove(entry)
    elif failure=='old_epoch_hash':
        manifest=json.loads((out/'gen-000/instances.json').read_text())
        manifest['source_pins'][module.D126_TARGET]='different-unreviewed-original'
        write(out/'gen-000/instances.json',manifest)
    elif failure=='current_hash':write(root/module.D126_TARGET,current+b'\n# unreviewed extra edit\n')
    if failure not in ('missing','old_epoch_hash','current_hash'):write(receipt,record)
    with pytest.raises((RuntimeError,ValueError,FileNotFoundError),match='(?i)D126|drift|receipt|reviewed'):
        module.apply(root,out,digest)
    assert (root/'src/llm/exit/runner.py').read_bytes()==b'old runner'
