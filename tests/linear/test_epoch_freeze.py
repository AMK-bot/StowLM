"""Qualification guards share production AST identities and preserve audit bytes."""
from dataclasses import dataclass
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from stowage.linear import dependencies


OWNER=Path(__file__).resolve().parents[2]/'experiments/linear-model/epoch_requalification'


@pytest.fixture
def guard(monkeypatch,tmp_path):
    spec=importlib.util.spec_from_file_location('epoch_freeze_test',OWNER/'freeze.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    root=tmp_path/'repo';home=root/'run/closure'
    def put(name,text):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        return path
    numeric=put('src/stowage/physics.py','"""Numerical documentation."""\nLIMIT=2\ndef numeric(x): return x+LIMIT\n')
    for name in ('dependencies.py','cache.py','margin_policy.py','boundary_calibration.py','qualification.py'):
        put('src/stowage/linear/'+name,'VALUE=1\n')
    owner='experiments/linear-model/epoch_requalification'
    for name in ('freeze.py','driver.py'):
        put(owner+'/'+name,'"""Producer documentation."""\nVALUE=1\n')
    put('experiments/linear-model/helper.py','VALUE=1\n')
    for name in ('claude-edge-cause.py','claude-face-pilot.py','claude-face-budget-pilot.py',
                 'claude-freeze-review-v2.py','d49_holdout_audit.py','claude_edge_audit.py',
                 'claude-prior-holdout-gate.py','claude_audit_inputs.py'):
        put(owner+'/production-1028b/'+name,'VALUE=1\n')
    put('src/stowage/rules/catalogue.py','def load(): return 1\n')
    catalogue=put('src/stowage/rules/catalogue.toml','''[[rules]]
id="iscode.a2_2.initial_gm"
[rules.parameters.minimum]
value=0.15
unit="m"
''')
    params=put('params/fleet/ship.toml','[hull_form]\noffsets_path="data/offsets.txt"\n')
    offsets=put('data/offsets.txt','0 1 2\n')
    monkeypatch.setattr(module,'ROOT',root)
    monkeypatch.setattr(module,'__file__',str(root/owner/'freeze.py'))
    monkeypatch.setattr(module,'HERE',home)
    # Use real production hashing/fingerprint functions on a tiny admitted graph.
    monkeypatch.setattr(dependencies,'_ROOT',root/'src/stowage')
    monkeypatch.setattr(dependencies,'_PROFILES',{'geometry':{'physics.py':('numeric',)}})
    monkeypatch.setattr(module,'dependencies',lambda:vars(dependencies))
    return SimpleNamespace(module=module,root=root,home=home,numeric=numeric,
                           catalogue=catalogue,params=params,offsets=offsets,put=put)


def capture(guard,*,finalize=True):
    state=guard.module.capture(guard.home)
    if finalize:guard.module.freeze_producers([],guard.home)
    return state


def test_capture_uses_production_ast_hash_and_is_immutable(guard):
    state=capture(guard);original=(guard.home/'start.json').read_bytes()
    assert state['profiles']['geometry']['fingerprint']==dependencies.dependency_fingerprint('geometry')
    assert state['python_sources']['src/stowage/physics.py']==dependencies._semantic_hash(guard.numeric.read_bytes(),('numeric',))
    assert state['hash_scheme_version']==dependencies.LINEAR_DEPENDENCY_HASH_SCHEME_VERSION
    with pytest.raises(FileExistsError):capture(guard)
    assert (guard.home/'start.json').read_bytes()==original


@pytest.mark.parametrize('target',['numeric','producer','catalogue_loader','catalogue_toml'])
def test_comments_and_docstrings_do_not_revoke_semantic_guard(guard,target):
    capture(guard)
    path={'numeric':guard.numeric,'producer':guard.root/'experiments/linear-model/epoch_requalification/driver.py',
          'catalogue_loader':guard.root/'src/stowage/rules/catalogue.py','catalogue_toml':guard.catalogue}[target]
    text=path.read_text()
    if path.suffix=='.py':text='"""New module documentation."""\n'+text.replace('documentation.','revised documentation.') if not text.startswith('"""') else text.replace('documentation.','revised documentation.')
    path.write_text('# Harmless comment\n'+text)
    assert guard.module.check(output=guard.home)['passed']
    assert not (guard.home.parent/'ABORT').exists()


@pytest.mark.parametrize('target',['constant','function','unselected','producer','catalogue_loader'])
def test_code_or_constants_abort_without_narrowing_to_selectors(guard,target):
    capture(guard);path=guard.numeric
    if target=='constant':text=path.read_text().replace('LIMIT=2','LIMIT=3')
    elif target=='function':text=path.read_text().replace('x+LIMIT','x-LIMIT')
    elif target=='unselected':text=path.read_text()+'\ndef other(): return 99\n'
    else:
        path=guard.root/('experiments/linear-model/epoch_requalification/driver.py' if target=='producer' else 'src/stowage/rules/catalogue.py')
        text=path.read_text().replace('1','2')
    path.write_text(text)
    result=guard.module.check(output=guard.home)
    assert not result['passed']
    assert 'python_sources/'+str(path.relative_to(guard.root)) in result['strict_changes']
    abort=json.loads((guard.home.parent/'ABORT').read_text())
    assert abort['strict_changes']==result['strict_changes']


@pytest.mark.parametrize('target',['params','offsets'])
def test_non_code_inputs_retain_exact_bytes(guard,target):
    capture(guard);path=getattr(guard,target);path.write_text(path.read_text()+'# Changed bytes\n')
    result=guard.module.check(output=guard.home)
    assert not result['passed']
    assert 'input_bytes/'+str(path.relative_to(guard.root)) in result['strict_changes']


def test_syntax_invalid_source_fails_closed_and_writes_abort(guard):
    capture(guard);guard.numeric.write_text('def broken(:\n')
    result=guard.module.check(output=guard.home)
    assert not result['passed']
    assert 'SyntaxError' in result['strict_changes']['@invalid_source_or_baseline']
    assert json.loads((guard.home.parent/'ABORT').read_text())['passed'] is False


def test_dependency_loader_failure_also_writes_shared_abort(guard,monkeypatch):
    capture(guard)
    def broken_loader():raise NameError('live dependency module is incomplete')
    monkeypatch.setattr(guard.module,'dependencies',broken_loader)
    result=guard.module.check(output=guard.home)
    assert not result['passed']
    assert 'NameError' in result['strict_changes']['@invalid_source_or_baseline']
    assert json.loads((guard.home.parent/'ABORT').read_text())['passed'] is False


def test_hash_scheme_mismatch_fails_closed(guard):
    capture(guard);path=guard.home/'start.json';state=json.loads(path.read_text())
    state['hash_scheme_version']='previous-scheme';path.write_text(json.dumps(state))
    result=guard.module.check(output=guard.home)
    assert not result['passed']
    assert result['strict_changes']['@hash_scheme_version']=={'before':'previous-scheme','after':'2'}
    assert (guard.home.parent/'ABORT').exists()


def test_semantic_producer_pins_preserve_original_raw_audit_hashes(guard):
    capture(guard,finalize=False);producer=guard.put('run/profile.py','"""Original documentation."""\nLIMIT=2\n')
    evidence=guard.put('run/fixture.json','{"mass":2}\n')
    guard.module.freeze_producers([producer,evidence],guard.home)
    path=guard.home/'run-producers.json';original=path.read_bytes();manifest=json.loads(original)
    name=str(producer.relative_to(guard.root));fixture=str(evidence.relative_to(guard.root))
    assert manifest['sources'][name]==hashlib.sha256(producer.read_bytes()).hexdigest()
    assert manifest['semantic_sources'][name]==dependencies._semantic_hash(producer.read_bytes(),None)
    assert manifest['sources'][fixture]==manifest['semantic_sources'][fixture]
    producer.write_text('# New comment\n'+producer.read_text().replace('Original documentation.','New documentation.'))
    assert hashlib.sha256(producer.read_bytes()).hexdigest()!=manifest['sources'][name]
    assert guard.module.check(output=guard.home)['passed']
    assert path.read_bytes()==original
    with pytest.raises(FileExistsError):guard.module.freeze_producers([producer],guard.home)
    assert path.read_bytes()==original
    producer.write_text(producer.read_text().replace('LIMIT=2','LIMIT=3'))
    result=guard.module.check(output=guard.home)
    assert not result['passed'] and 'producer/'+name in result['strict_changes']


def test_non_python_producer_evidence_remains_exact(guard):
    capture(guard,finalize=False);evidence=guard.put('run/fixture.json','{"mass":2}\n')
    guard.module.freeze_producers([evidence],guard.home)
    evidence.write_text('{"mass":3}\n')
    result=guard.module.check(output=guard.home)
    assert not result['passed'] and 'producer/run/fixture.json' in result['strict_changes']


def test_producer_manifest_scheme_mismatch_aborts(guard):
    capture(guard,finalize=False);producer=guard.put('run/profile.py','LIMIT=2\n')
    guard.module.freeze_producers([producer],guard.home)
    path=guard.home/'run-producers.json';record=json.loads(path.read_text())
    record['hash_scheme_version']='old';path.write_text(json.dumps(record))
    result=guard.module.check(output=guard.home)
    assert not result['passed']
    assert 'finalized producer manifest changed' in result['strict_changes']['@invalid_source_or_baseline']


def test_assert_current_never_clears_an_existing_abort(guard):
    capture(guard);abort=guard.home.parent/'ABORT';abort.write_text('previous stop\n')
    with pytest.raises(RuntimeError,match='stop and report'):guard.module.assert_current()
    assert abort.read_text()=='previous stop\n'


@pytest.mark.parametrize('mutation',['documentation','code'])
def test_physical_driver_live_guard_is_semantic_but_audit_pin_is_original_raw(monkeypatch,tmp_path,mutation):
    # Compile only the actual function; replace every physical producer with a
    # stub. This tests its live source check without constructing a model.
    from stowage.linear import physics,bands,reusable
    source=ast.parse((OWNER/'driver.py').read_bytes())
    function=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='physical_case')
    @dataclass
    class Record:
        value: int=1
    driver=tmp_path/'driver.py';driver.write_text('"""Original documentation."""\nLIMIT=2\n')
    original=driver.read_bytes();before={'physical':'unchanged'}
    family=SimpleNamespace(cache_key='same',parameters={})
    model=SimpleNamespace(station_x_m=[])
    monkeypatch.setattr(bands,'band_for_instance',lambda instance:Record())
    monkeypatch.setattr(reusable,'coefficient_family',lambda *a:family)
    monkeypatch.setattr(reusable,'assemble_instance',lambda *a,**kw:model)
    def measure(*args,**kwargs):
        driver.write_text('# Edited during capture\n'+original.decode().replace(
            'Original documentation.' if mutation=='documentation' else 'LIMIT=2',
            'New documentation.' if mutation=='documentation' else 'LIMIT=3'))
        return Record(),{}
    monkeypatch.setattr(physics,'measure_errors',measure)
    import dataclasses,os,resource,time
    namespace=dict(__file__=str(driver),Path=Path,json=json,hashlib=hashlib,time=time,
                   asdict=dataclasses.asdict,os=os,resource=resource,
                   cargo_instance=lambda *a:(object(),{}),snapshot=lambda *a:before,
                   write=lambda path,record:None)
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(OWNER/'driver.py'),'exec'),namespace)
    args=('ship','light','holdout',tmp_path,(family,None,Record(),{},before))
    if mutation=='code':
        with pytest.raises(ValueError,match='driver changed during measurement'):
            namespace['physical_case'](*args,samples=1,seed=7,public=False)
    else:
        record=namespace['physical_case'](*args,samples=1,seed=7,public=False)
        assert record['sample_state_exact_source']['driver_sha256']==hashlib.sha256(original).hexdigest()
        assert record['sample_state_exact_source']['driver_sha256']!=hashlib.sha256(driver.read_bytes()).hexdigest()
        assert record['physical_inputs_unchanged']
