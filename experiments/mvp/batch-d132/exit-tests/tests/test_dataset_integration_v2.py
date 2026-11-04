"""Deferred owner integration cases, actual combined runtime (no owner overlay)."""
from pathlib import Path
import json,os,subprocess,sys,hashlib
HERE=Path(__file__).resolve().parents[1]
RUNTIME=Path('/private/tmp/stowlm-d132-exit-qualified-runtime-20261030')

def test_real_mvp_dataset_bytes_serial_vs_spawned(tmp_path):
    process=subprocess.run([sys.executable,str(HERE/'dataset_integration.py'),str(RUNTIME),str(tmp_path/'actual-dataset')],
       cwd=RUNTIME,env={**os.environ,'PYTHONPATH':str(RUNTIME/'src')},capture_output=True,text=True,timeout=240)
    assert process.returncode==0,process.stdout+process.stderr
    result=json.loads((tmp_path/'actual-dataset/result.json').read_text())
    assert result['passed'] and result['source_unchanged'] and not result['mlx_loaded']
    assert len(result['prime_rows'])==2 and all(r['rechecked'] for r in result['prime_rows'])
    for name,digest in result['byte_identical'].items():
        a=(tmp_path/'actual-dataset/dataset-1'/name).read_bytes();b=(tmp_path/'actual-dataset/dataset-2'/name).read_bytes()
        assert a==b and hashlib.sha256(a).hexdigest()==digest

def test_spawned_pool_order_fault_and_environment():
    process=subprocess.run([sys.executable,str(HERE/'tests/pool_probe.py')],cwd=RUNTIME,
      env={**os.environ,'PYTHONPATH':str(RUNTIME/'src')},capture_output=True,text=True,check=True,timeout=60)
    assert json.loads(process.stdout)==dict(order=[1,2,3,4],failure='fixture worker violation',environment_restored=True)
