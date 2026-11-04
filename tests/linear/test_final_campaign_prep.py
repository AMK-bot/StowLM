"""Exercise FINAL glue with fresh source copies and isolated synthetic outputs."""
import ast
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest

OWNER=Path(__file__).resolve().parents[2]/'experiments/linear-model/epoch_requalification'
REPO=OWNER.parents[2]


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@contextmanager
def prepared():
    helper=load(OWNER/'prepare_final.py','final_preparer_test')
    before={name:helper.sha(REPO/'tests/linear'/name) for name in helper.TEST_NAMES}
    with tempfile.TemporaryDirectory(prefix='prep-test-',dir=OWNER) as name:
        output=Path(name);output.rmdir();helper.prepare(output,1)
        try:yield helper,output
        finally:
            assert all(helper.sha(REPO/'tests/linear'/n)==h for n,h in before.items())


def test_preparation_contains_only_sources_and_pinned_inputs():
    with prepared() as (helper,output):
        metadata=json.loads((output/'preparation.json').read_text())
        assert metadata['profiles']==dict(seawater=1025.,freshwater=1000.)
        assert metadata['tests_modified'] is False
        assert metadata['original_numerical_evidence_copied'] is False
        assert json.loads((output/'compute-capacity.json').read_text())==dict(workers=1)
        for path in output.rglob('*.py'):compile(path.read_text(),str(path),'exec')
        for name in ('closure/start.json','closure/finalized.json','closure/run-producers.json','ABORT'):
            assert not (output/name).exists()
        with pytest.raises(ValueError,match='fresh output required'):helper.prepare(output,1)


@pytest.mark.parametrize('name',('test_claude_edge_rows.py','test_d49_holdout_inclusion.py','test_claude_qualification.py'))
def test_migration_is_current_source_with_every_assertion_preserved(name):
    helper=load(OWNER/'prepare_final.py','migration_source_test')
    before=(REPO/'tests/linear'/name).read_text()
    after=helper.migrate_test_source(name,before,'experiments/linear-model/epoch_requalification/final-test')
    assertions=lambda s:[ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(s)) if isinstance(n,ast.Assert)]
    assert assertions(before)==assertions(after)
    assert 'production-1028c' not in after
    assert after.count("EVIDENCE_HASH_SCHEME = '2'")==1
    assert "EVIDENCE_HASH_SCHEME = '1'" not in after
    with pytest.raises(ValueError,match='marker changed'):
        helper.migrate_test_source(name,'assert False\n','final-test')


def test_capture_rejects_invalid_fresh_catalogue_before_baseline(monkeypatch):
    with prepared() as (helper,output):
        path=output/'d64-inport-command.json';config=json.loads(path.read_text())
        config['arguments']=[str(OWNER/'driver.py')];path.write_text(json.dumps(config))
        monkeypatch.setattr(helper.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stderr='invalid schema',stdout=''))
        with pytest.raises(ValueError,match='fresh catalogue/schema validation failed'):helper.capture(output)
        assert not (output/'closure/start.json').exists()


def test_capture_requires_l4_acceptance_command_before_freeze():
    with prepared() as (helper,output):
        with pytest.raises(ValueError,match='L4 must fill'):helper.capture(output)
        assert not (output/'closure/start.json').exists()


def test_d64_entrypoint_and_extra_helpers_are_pinned():
    with prepared() as (helper,output):
        script=output/'d64.py';script.write_text('print("diagnostic stub")\n')
        data=output/'request.json';data.write_text('{}')
        configured=dict(arguments=[str(script),'--root',str(output)],producer_paths=[str(data)])
        assert set(helper.d64_producer_paths(configured))=={script,data}
        with pytest.raises(ValueError,match='existing repository file'):
            helper.d64_producer_paths(dict(arguments=['/private/tmp/missing-d64.py']))
        with pytest.raises(ValueError,match='requires a Python script'):
            helper.d64_producer_paths(dict(arguments=['-c','raise RuntimeError']))


def test_finish_synthetic_bank_and_real_migration_are_isolated(monkeypatch,tmp_path):
    with prepared() as (helper,output):
        calls=[];real_bank=REPO/'src/stowage/linear/margin_policy_data.json';before=helper.sha(real_bank)
        synthetic_repo=tmp_path/'repo';synthetic_repo.mkdir()
        synthetic_bank=synthetic_repo/'bank.json';synthetic_bank.write_text('{"entries":{"synthetic":{"reserve":7}}}')
        stub=synthetic_repo/'read-only-d64-stub.py'
        stub.write_text('import json,sys\nfrom pathlib import Path\n'
            'assert json.loads(Path(sys.argv[1]).read_text())["entries"]["synthetic"]["reserve"]==7\n'
            'Path(sys.argv[2]).write_text('+repr(json.dumps(helper.D64_EXPECTED))+')\n')
        config=output/'d64-inport-command.json';value=json.loads(config.read_text())
        value['arguments']=[str(stub),str(synthetic_bank),str(output/'d64-inport-acceptance.json')]
        config.write_text(json.dumps(value))
        fake_compute=SimpleNamespace(run=lambda *a,**k:None,abort_check=lambda:None)
        monkeypatch.setitem(sys.modules,'compute',fake_compute)
        finish=load(output/'finish.py','isolated_finish_test')
        monkeypatch.setattr(finish,'ready',lambda:True)
        gate_paths=('candidate-residual-gate.json','candidate-row-audit.json','holdout-audit/summary.json')
        for profile in ('seawater','freshwater'):
            for relative in gate_paths:
                path=output/profile/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{"passed":true}')
            (output/profile/'claude-current-audit-inputs.json').write_text('{}')
        plan=json.loads((output/'migration-plan.json').read_text())
        for item in plan:
            target=synthetic_repo/item['target'];target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((REPO/item['target']).read_bytes())
        guard_calls=[]
        monkeypatch.setitem(sys.modules,'guard',SimpleNamespace(assert_current=lambda:guard_calls.append(True)))
        migration=load(output/'migrate_evidence_tests.py','isolated_migration_test')
        migration.REPO=synthetic_repo
        def run(name,args,**kwargs):
            calls.append(name)
            if name in ('seawater-candidate','freshwater-candidate','seawater-apply','freshwater-apply'):
                profile=name.split('-')[0];folder=output/profile/'promotion';folder.mkdir(exist_ok=True)
                (folder/'candidate-bank.json').write_bytes(synthetic_bank.read_bytes())
                (folder/'report.json').write_text(json.dumps(dict(passed=True,applied='--apply' in args)))
            if name=='migrate-evidence-tests':migration.main()
            if name=='D64-inport-acceptance':subprocess.run([sys.executable,*map(str,args)],check=True)
            return dict(exit_code=0)
        monkeypatch.setattr(finish,'run',run)
        monkeypatch.setattr(finish,'controller',lambda name,args:calls.append(name))
        monkeypatch.setattr(finish,'publics',lambda:calls.append('publics'))
        finish.main()
        assert calls.index('candidate-gates')<calls.index('guard-pre-apply')<calls.index('seawater-apply')<calls.index('freshwater-apply')
        assert calls.index('freshwater-apply')<calls.index('D64-inport-acceptance')<calls.index('publics')
        assert calls.index('migrate-evidence-tests')>max(calls.index(n) for n in ('publics','ordinary-replays','input-manifests'))
        assert calls.index('migrate-evidence-tests')<calls.index('linear-feasibility-tests')<calls.index('guard-final')
        assert json.loads((output/'finish-execution.json').read_text())['label']=='final'
        assert json.loads((output/'test-migration-ready.json').read_text())['passed']
        assert len(guard_calls)==2
        for item in plan:assert helper.sha(synthetic_repo/item['target'])==item['after_sha256']
        assert helper.sha(real_bank)==before
        assert json.loads(synthetic_bank.read_text())['entries']['synthetic']['reserve']==7


def test_manifest_abort_stops_own_recorded_worker(tmp_path,monkeypatch):
    from stowage.linear import dependencies
    guard=load(OWNER/'freeze.py','abort_worker_test')
    home=tmp_path/'closure'
    worker=tmp_path/'compute_worker.py'
    worker.write_bytes((OWNER/'production-1028c/compute_worker.py').read_bytes())
    script=tmp_path/'wait.py';script.write_text('from pathlib import Path\nimport time\nPath(__file__).with_suffix(".ready").touch()\ntime.sleep(30)\n')
    baseline=dict(hash_scheme_version=dependencies.LINEAR_DEPENDENCY_HASH_SCHEME_VERSION,
        python_sources={},input_bytes={},profiles={},runtime_inputs={})
    monkeypatch.setattr(guard,'ROOT',tmp_path);monkeypatch.setattr(guard,'HERE',home)
    monkeypatch.setattr(guard,'snapshot',lambda:dict(baseline))
    monkeypatch.setattr(guard,'dependencies',lambda:vars(dependencies))
    guard.capture(home);guard.freeze_producers([worker,script],home)
    process=subprocess.Popen([sys.executable,str(worker),str(script)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+5
        while not script.with_suffix('.ready').exists():
            assert time.monotonic()<deadline;time.sleep(.02)
        (home/'run-producers.json').unlink()
        assert guard.check(output=home)['passed'] is False
        _,stderr=process.communicate(timeout=4)
        assert process.returncode==86
        assert b'stopping own numerical worker' in stderr
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=4)
