"""Fail-closed provenance mutations; no numerical solver or verifier main.

Load only verifier definitions before its first artifact loop. File mutations
are virtual Path.read_bytes replacements: repository files are never edited.
The parent runs these tests serially; this author did not run the test suite.
"""
import ast
from pathlib import Path
import sys

import pytest


HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
STABILITY='src/stowage/checker/stability.py'
LOADING='src/stowage/checker/stability_loading.py'
PRE_D64='93efb614863f3eca66b0aa2323207234d7f478caf3b3692a27b0429bac1f53e7'
POST_D64='bac930becd2ac0f3bedfb91fb93bafe2ae3d9c700c8401c80642b5343f8a7943'
CURRENT='43f02c8f516c6bf334b6f455918a522738ebac32b979036781657b6c0293a9ab'
LOADING_OLD='acab0ef99fabf2ddc0b5f970edfa6c49f5db1f27e02700dcdaf9b5b164a2166f'
LOADING_NEW='c449d9d3df5ecf5e18f0bf899e6eba1f6677570cdfffc5fdb8d3acae26bc2533'


@pytest.fixture
def guard():
    path=HERE/'verify_followup.py'
    tree=ast.parse(path.read_text())
    prefix=[]
    for node in tree.body:
        if isinstance(node,ast.For):break
        prefix.append(node)
    assert len(prefix)<len(tree.body),'Verifier main boundary changed: update definition-only loader'
    namespace={'__file__':str(path),'__name__':'epoch_guard_test_definitions'}
    exec(compile(ast.Module(body=prefix,type_ignores=[]),str(path),'exec'),namespace)
    return namespace


def snapshot(guard,module,digest):
    return (HERE/guard['_AUDITED_SOURCE_SNAPSHOTS'][module][digest]).read_bytes()


def virtual_bytes(monkeypatch,replacements):
    original=Path.read_bytes
    targets={Path(path).resolve():payload for path,payload in replacements.items()}
    def read(path):
        return targets[path.resolve()] if path.resolve() in targets else original(path)
    monkeypatch.setattr(Path,'read_bytes',read)


@pytest.mark.parametrize('live_epoch',[PRE_D64,POST_D64,CURRENT])
def test_known_complete_stability_epochs_are_accepted(guard,monkeypatch,live_epoch):
    virtual_bytes(monkeypatch,{guard['ROOT']/STABILITY:snapshot(guard,STABILITY,live_epoch)})
    guard['_verify_audited_source'](STABILITY,PRE_D64)


@pytest.mark.parametrize('live_epoch',[LOADING_OLD,LOADING_NEW])
def test_only_the_two_explicit_loading_epochs_are_accepted(guard,monkeypatch,live_epoch):
    virtual_bytes(monkeypatch,{guard['ROOT']/LOADING:snapshot(guard,LOADING,live_epoch)})
    guard['_verify_audited_source'](LOADING,LOADING_OLD)


def test_reviewers_public_metric_mutation_is_rejected_even_with_helpers_unchanged(guard,monkeypatch):
    source=snapshot(guard,STABILITY,CURRENT).decode()
    needle="        metrics['geometry_loading_equilibrium_direct'] = float(exact_loading_equilibrium)"
    assert source.count(needle)==1
    altered=source.replace(needle,needle+"\n        metrics['area_0_30_mrad'] = 0.0")
    # Reproduce the old loophole's premise before checking fail-closed behavior.
    before,after=ast.parse(source),ast.parse(altered)
    for name in ('_context','_deck_edge_angle','_refine_peak','_windage','_weather_keel_area'):
        a=next(n for n in before.body if isinstance(n,ast.FunctionDef) and n.name==name)
        b=next(n for n in after.body if isinstance(n,ast.FunctionDef) and n.name==name)
        assert ast.dump(a,include_attributes=False)==ast.dump(b,include_attributes=False)
    virtual_bytes(monkeypatch,{guard['ROOT']/STABILITY:altered.encode()})
    record={'source_hashes_before':{STABILITY:PRE_D64},'source_hashes_after':{STABILITY:PRE_D64}}
    with pytest.raises(AssertionError,match='unaudited live source epoch'):
        guard['verify_epoch'](record)


def test_unknown_port_only_edit_cannot_auto_qualify_as_a_new_epoch(guard,monkeypatch):
    source=snapshot(guard,STABILITY,CURRENT).decode()
    needle="            check_budget('in-port fixed-trim mass/contact balance')"
    assert source.count(needle)==1
    altered=source.replace(needle,needle+"\n            mass = 0.0")
    # Even unchanged specialized seagoing syntax is not enough: the complete
    # new source has not been reviewed/pinned and must be rejected.
    assert guard['_seagoing_module_ast'](source.encode())==guard['_seagoing_module_ast'](altered.encode())
    virtual_bytes(monkeypatch,{guard['ROOT']/STABILITY:altered.encode()})
    with pytest.raises(AssertionError,match='unaudited live source epoch'):
        guard['_verify_audited_source'](STABILITY,PRE_D64)


def test_unknown_artifact_epoch_is_rejected_even_when_live_source_is_known(guard,monkeypatch):
    virtual_bytes(monkeypatch,{guard['ROOT']/STABILITY:snapshot(guard,STABILITY,CURRENT)})
    with pytest.raises(AssertionError,match='unaudited artifact source epoch'):
        guard['_verify_audited_source'](STABILITY,'0'*64)


@pytest.mark.parametrize('mutation',['comment','executable'])
def test_unpinned_loading_changes_are_not_accepted_by_ast_similarity(guard,monkeypatch,mutation):
    source=snapshot(guard,LOADING,LOADING_NEW)
    altered=source+(b'\n# Further unaudited comment\n' if mutation=='comment' else b'\nLOADING_OVERRIDE = 1\n')
    virtual_bytes(monkeypatch,{guard['ROOT']/LOADING:altered})
    with pytest.raises(AssertionError,match='unaudited live source epoch'):
        guard['_verify_audited_source'](LOADING,LOADING_OLD)


def test_altered_audit_manifest_is_rejected(guard,monkeypatch):
    virtual_bytes(monkeypatch,{guard['ROOT']/STABILITY:snapshot(guard,STABILITY,CURRENT),
                              HERE/guard['_EPOCH_AUDIT_PATH']:b'{}\n'})
    with pytest.raises(AssertionError,match='epoch audit pin'):
        guard['_verify_audited_source'](STABILITY,PRE_D64)


@pytest.mark.parametrize('relative_path',[
    'followup/snapshots/stability-seagoing-43f02c8f516c.txt',
    'followup/snapshots/pre-d64-to-bac930.patch',
    'followup/snapshots/bac930-to-43f02.patch',
    'followup2/source-snapshot/src/stowage/checker/stability_loading.py',
    'source-drift-audit.md',
])
def test_tampered_complete_snapshot_patch_or_audit_is_rejected(guard,monkeypatch,relative_path):
    target=HERE/relative_path
    live=snapshot(guard,STABILITY,CURRENT)
    altered=target.read_bytes()+b'\nTAMPERED\n'
    virtual_bytes(monkeypatch,{guard['ROOT']/STABILITY:live,target:altered})
    with pytest.raises(AssertionError,match='epoch audit dependency'):
        guard['_verify_audited_source'](STABILITY,PRE_D64)
