"""Prepare a byte-pinned independent generation runtime (copied from C6).

Copies source, parameters and acceleration caches only; never maps or plans.
"""
from pathlib import Path
import hashlib
import json
import shutil
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUNTIME = HERE / 'runtime'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def await_capacity():
    """Wait for a current fleet admission reading; unavailable means wait."""
    while True:
        try:
            state = json.loads((ROOT / 'experiments/fleet/core-state.json').read_text())
            age = time.time() - float(state['t'])
            mask = int(state['mask'], 0) if isinstance(state['mask'], str) else int(state['mask'])
            if mask.bit_count() >= 8 and float(state['load1']) <= 45 and 0 <= age < 120:
                return dict(mask=mask, load1=state['load1'], age_s=age)
        except (OSError, ValueError, TypeError, KeyError):
            pass
        print(json.dumps(dict(event='waiting_for_capacity')), flush=True)
        time.sleep(10)


def main():
    assert not (HERE / 'requests.json').exists(), 'never replace frozen runtime'
    admission = await_capacity()
    source_paths = [p for base in ('src/stowage', 'params') for p in (ROOT / base).rglob('*')
                    if p.is_file() and p.suffix in {'.py', '.json', '.toml'}]
    source_paths += [ROOT / name for name in ('pyproject.toml', 'experiments/mvp/corpus_tools/build.py',
                                             'data/raw/kcs/kcs.fix')]
    before = {str(p.relative_to(ROOT)): sha(p) for p in source_paths}
    for name in ('src/stowage', 'params'):
        shutil.copytree(ROOT / name, RUNTIME / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('pyproject.toml', 'experiments/mvp/corpus_tools/build.py', 'data/raw/kcs/kcs.fix'):
        target = RUNTIME / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    # Independent file copies; no map, plan, acceptance-witness or solver cache.
    caches = {}
    for name in ('stability', 'vessels'):
        source = ROOT / 'experiments/cache' / name
        target = RUNTIME / 'experiments/cache' / name
        shutil.copytree(source, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('*.partial', '*.lock'))
        for copied in target.rglob('*'):
            if copied.is_file():
                relative = copied.relative_to(RUNTIME).as_posix()
                caches[relative] = sha(copied)
                assert copied.stat().st_ino != (ROOT / relative).stat().st_ino
    assert before == {name: sha(ROOT / name) for name in before}, 'source changed while copying'
    assert before == {name: sha(RUNTIME / name) for name in before}, 'copy mismatch'
    (HERE / 'runtime-snapshot.json').write_text(json.dumps(dict(files=before,
        builder_sha256=before['experiments/mvp/corpus_tools/build.py'],
        acceleration_cache_files=caches, capacity_admission=admission,
        method='independent file copies; source bytes identical before/after snapshot'), indent=2)+'\n')
    print(json.dumps(dict(event='runtime_prepared', files=len(before), cache_files=len(caches))))


if __name__ == '__main__':
    main()
