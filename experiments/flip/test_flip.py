"""Prep is non-mutating; stale, absent and unexecuted gates cannot pass."""
from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location('_flip_test_'+name, HERE/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def prep(): return load('prepare_patch')


@pytest.fixture
def checks(): return load('preconditions')


def sample_plan():
    return dict(changes=[dict(path='src/example.py', old='VERSION = "1"',
        new='VERSION = "2"', label='VERSION')])


def test_patch_render_changes_only_memory(prep, tmp_path):
    source=tmp_path/'src/example.py';source.parent.mkdir()
    source.write_text('VERSION = "1"\n')
    before=source.read_bytes()
    patch,pins,updated=prep.render(tmp_path,sample_plan())
    assert b'+VERSION = "2"' in patch
    assert updated['src/example.py']==b'VERSION = "2"\n'
    assert pins['src/example.py']['before_sha256']==sha256(before).hexdigest()
    assert source.read_bytes()==before


@pytest.mark.parametrize('text',['VERSION = "3"\n','VERSION = "1"\nVERSION = "1"\n'])
def test_patch_refuses_wrong_or_nonunique_preimage(prep,tmp_path,text):
    source=tmp_path/'src/example.py';source.parent.mkdir();source.write_text(text)
    with pytest.raises(ValueError,match='preimage'):prep.render(tmp_path,sample_plan())


def test_patch_paths_cannot_escape(prep,tmp_path):
    plan=sample_plan();plan['changes'][0]['path']='src/../../outside.py'
    with pytest.raises(ValueError,match='escapes'):prep.render(tmp_path,plan)


def test_real_proposal_is_parsable_and_has_one_consistent_activation_default(prep):
    import ast,tomllib
    plan=json.loads((HERE/'version-plan.json').read_bytes())
    patch,pins,updated=prep.render(prep.ROOT,plan)
    assert patch and len(updated)>1
    assert tomllib.loads(updated['src/stowage/rules/catalogue.toml'].decode())['activation']['rule_freeze_v1'] is True
    tree=ast.parse(updated['src/stowage/rules/catalogue.py'])
    activation=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='Activation')
    switch=next(node for node in activation.body if isinstance(node,ast.AnnAssign) and node.target.id=='rule_freeze_v1')
    assert ast.literal_eval(switch.value) is True
    namespace={}
    exec(compile(updated['src/stowage/versions.py'],'<unapplied versions>','exec'),namespace)
    from stowage.rules.catalogue import rule_freeze_v1
    for enabled,version in [(False,'10.0.0'),(True,'11.0.0')]:
        with rule_freeze_v1(enabled=enabled):
            assert namespace['effective_simulated_instances_version']()==version
    assert namespace['CHECKER_VERSION']=='3.0'
    assert namespace['MOVE_ENGINE_VERSION']=='3.0'
    assert namespace['ACTION_SPACE_VERSION']=='2.0.0'


def test_patch_pin_rejects_staged_noop_identity_drift(prep,tmp_path):
    source=tmp_path/'src/example.py';source.parent.mkdir();source.write_text('VERSION = "1"\n')
    staged=tmp_path/'src/staged.py';staged.write_text('PROMPT = "3"\n')
    plan=sample_plan();plan['already_current']=[dict(path='src/staged.py')]
    first=prep.render(tmp_path,plan)[1]
    staged.write_text('PROMPT = "4"\n')
    assert prep.render(tmp_path,plan)[1]!=first


def test_gate_inventory_covers_every_path_and_has_both_review_refs(checks):
    definitions=json.loads((HERE/'gate-suites.json').read_bytes())
    ids=set()
    for gate in definitions['gates']:
        assert gate['id'] not in ids
        ids.add(gate['id'])
        assert checks.gate_paths(gate)
        for key in ('code_review','logic_review'):
            assert (checks.ROOT/gate[key]).is_file()
    assert len(ids)==35


def test_missing_suite_and_path_escape_fail(checks,tmp_path):
    for pattern in ('does-not-exist.py','../escape.py'):
        with pytest.raises(ValueError):checks.gate_paths(dict(patterns=[pattern]),tmp_path)


def review_fixture(tmp_path):
    for name in ('code.md','logic.md'):(tmp_path/name).write_text('reviewed\n')
    digest=sha256(b'reviewed\n').hexdigest()
    gate=dict(id='g',code_review='code.md',logic_review='logic.md',
        pre_flip_requirements=['implementation ready'],post_flip_requirements=['qualified live run'])
    item=dict(reviewer='leader',fixes_verified=True,code_review_sha256=digest,
        logic_review_sha256=digest,requirements_confirmed=['implementation ready'])
    approval=dict(source_sha256='epoch',items={'g':item})
    return gate,approval


def test_review_file_existence_never_auto_approves(checks,tmp_path):
    gate,approval=review_fixture(tmp_path)
    assert checks.review_check(gate,{},'epoch',tmp_path)
    assert not checks.review_check(gate,approval,'epoch',tmp_path)
    assert checks.review_check(gate,approval,'different',tmp_path)
    (tmp_path/'logic.md').write_text('changed\n')
    assert 'logic_review missing or changed' in checks.review_check(gate,approval,'epoch',tmp_path)


def test_post_flip_proofs_are_deferred_without_claiming_them_before_flip(checks,tmp_path):
    gate,approval=review_fixture(tmp_path)
    assert not checks.review_check(gate,approval,'epoch',tmp_path,phase='pre-flip')
    assert checks.review_check(gate,approval,'epoch',tmp_path,phase='post-flip')
    approval['items']['g']['requirements_confirmed'].append('qualified live run')
    assert not checks.review_check(gate,approval,'epoch',tmp_path,phase='post-flip')


@pytest.mark.parametrize('body',[
    '<testsuite tests="0"/>',
    '<testsuite tests="2" skipped="2"/>',
    '<testsuite tests="1" failures="1"/>',
])
def test_no_executed_passing_suite_cannot_pass(checks,tmp_path,monkeypatch,body):
    (tmp_path/'test_dummy.py').write_text('def test_dummy(): assert True\n')
    def launch(command,**kwargs):
        target=next(arg.split('=',1)[1] for arg in command if arg.startswith('--junitxml='))
        Path(target).write_text(body)
        class Child:
            pid=12345
            def wait(self,timeout=None):return 0
        return Child()
    monkeypatch.setattr(checks.subprocess,'Popen',launch)
    result=checks.run_gate(dict(id='g',patterns=['test_dummy.py']),tmp_path,modes=('on',),root=tmp_path)
    assert result[0]['passed'] is False


def test_real_serial_worker_launch(checks,tmp_path):
    (tmp_path/'test_dummy.py').write_text('def test_dummy(): assert True\n')
    # A real fresh child validates interpreter/uv/pytest/context transport.
    # This does not execute a numerical checker or any activation write.
    result=checks.run_gate(dict(id='transport',patterns=['test_dummy.py']),tmp_path,
        modes=('off','on'),timeout_s=30,root=tmp_path)
    assert [item['mode'] for item in result]==['off','on']
    assert all(item['passed'] for item in result),result
    assert all(item['junit']['executed']==1 for item in result)
    assert (tmp_path/'test_dummy.py').read_text()=='def test_dummy(): assert True\n'


def test_unresolved_numeric_conflict_cannot_be_ready(checks):
    with pytest.raises(ValueError,match='unresolved'):
        checks.require_resolved_versions(dict(unresolved_numeric_proposals=['requested9.2 versus D77active9.3']))
    checks.require_resolved_versions(dict(unresolved_numeric_proposals=[]))


def test_source_identity_excludes_outputs_but_pins_patch_and_tests(checks,tmp_path):
    folder=tmp_path/'experiments/flip';folder.mkdir(parents=True)
    (folder/'activation.patch').write_text('patch1')
    before=checks.source_identity(tmp_path)
    (folder/'run.json').write_text('output')
    assert checks.source_identity(tmp_path)==before
    (folder/'activation.patch').write_text('patch2')
    assert checks.source_identity(tmp_path)!=before
