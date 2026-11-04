"""Run without modifying live source: uv run pytest <this directory> -q."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import llm.exit
STAGE = Path(llm.exit.__file__).resolve().parent
ROOT = next(p for p in STAGE.parents if (p/'pyproject.toml').exists())
_spec=importlib.util.spec_from_file_location('d134_dataset_pool',STAGE/'dataset_pool.py')
pool=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pool)


def allocation(cores=8,ram_gb=76):
    return SimpleNamespace(active=True,cores=cores,ram_gb=ram_gb)


@pytest.mark.parametrize('background,cores,ram,requested,expected',[
    (6,8,76,0,6),(12,8,76,0,3),(6,4,68,0,2),
    (6,8,66,0,1),(6,8,76,2,2),(6,8,76,99,6),
])
def test_worker_count_spends_only_free_and_reserved_capacity(monkeypatch,background,cores,ram,requested,expected):
    from llm.exit import resources
    monkeypatch.setattr(resources,'background_cores',lambda:background)
    args=SimpleNamespace(_reservation=allocation(cores,ram),dataset_workers=requested,workers=6)
    result=pool.choose_workers(args)
    assert result['workers']==expected
    assert result['workers']+1<=cores
    assert result['workers']*2+64<=ram
    assert result['workers']+1+background<=16


@pytest.mark.parametrize('bad',[True,-1,1.5])
def test_invalid_worker_request_refused(bad):
    with pytest.raises(ValueError):pool.choose_workers(SimpleNamespace(dataset_workers=bad))
    with pytest.raises(ValueError):
        with pool.ordered_pool(bad):pass


def test_malformed_ledger_is_serial_and_never_grows_a_lease(monkeypatch):
    from llm.exit import resources
    def broken():raise RuntimeError('unknown numeric reservation')
    monkeypatch.setattr(resources,'background_cores',broken)
    result=pool.choose_workers(SimpleNamespace(_reservation=allocation(),workers=6))
    assert result['workers']==1
    assert 'capacity unavailable' in result['reason']
    assert pool.choose_workers(SimpleNamespace(workers=6))['workers']==1


def test_serial_pool_keeps_order_and_propagates_errors(monkeypatch):
    monkeypatch.setenv('OMP_NUM_THREADS','7')
    monkeypatch.setenv('STOWAGE_IN_PORT_WORKERS','4')
    with pool.ordered_pool(1) as work:
        assert os.environ['OMP_NUM_THREADS']=='1'
        assert os.environ['STOWAGE_IN_PORT_WORKERS']=='1'
        assert work(abs,[-3,2,-1])==[3,2,1]
        with pytest.raises(ValueError):work(int,['1','bad'])
    assert os.environ['OMP_NUM_THREADS']=='7'
    assert os.environ['STOWAGE_IN_PORT_WORKERS']=='4'


def test_real_mvp_dataset_bytes_serial_vs_spawned(tmp_path):
    """Real admitted winner/replay/val rows, full securing+lookahead, no stubbed checker.

    The integration fixture uses *identical bound reports* for both runs. A stale
    report's fresh checker timestamps are nondeterministic even serial-vs-serial;
    this test does not erase or fabricate those physics/provenance fields.
    """
    inputs=STAGE/'measurements/inputs.json'
    if not inputs.exists():pytest.skip('local saved-plan integration inputs unavailable')
    config=json.loads(inputs.read_text())
    if not all(Path(p).exists() for key in ('winners','replay','validation_records') for p in config[key]):
        pytest.skip('local saved-plan fixtures unavailable')
    results={}
    env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
         'VECLIB_MAXIMUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'}
    for workers in (1,2):
        output=tmp_path/f'dataset-{workers}'
        run=subprocess.run([sys.executable,str(STAGE/'tests/fixture_driver.py'),str(output),
                            str(workers),str(inputs)],cwd=ROOT,env=env,text=True,capture_output=True,
                           check=True,timeout=180)
        results[workers]=json.loads(run.stdout)
        assert results[workers]['report']['current_rows']==2
        assert results[workers]['report']['replay_rows']==2
        assert results[workers]['report']['validation_rows']==1
        if workers==2:
            for _,values in results[workers]['probes']:
                assert all(v==('false' if k=='TOKENIZERS_PARALLELISM' else '1') for k,v in values.items())
    hashes={}
    for name in ('train.jsonl','val.jsonl','stats.json','exit_build_report.json'):
        first=(tmp_path/'dataset-1'/name).read_bytes()
        second=(tmp_path/'dataset-2'/name).read_bytes()
        assert first==second,name
        hashes[name]=hashlib.sha256(first).hexdigest()
    report=dict(phase_times_s={str(k):v['wall_s'] for k,v in results.items()},sha256=hashes,
                rows='2 current + 2 replay + 1 loss-val; real bound checker reports')
    # The test's report is owned experiment output, never a production artifact.
    (STAGE/'measurements/byte-equivalence.json').write_text(json.dumps(report,indent=2)+'\n')


def test_spawned_pool_order_fault_and_environment():
    run=subprocess.run([sys.executable,str(STAGE/'tests/pool_probe.py')],cwd=ROOT,
                       capture_output=True,text=True,check=True,timeout=60)
    assert json.loads(run.stdout)==dict(order=[1,2,3,4],failure='fixture worker violation',
                                       environment_restored=True)
