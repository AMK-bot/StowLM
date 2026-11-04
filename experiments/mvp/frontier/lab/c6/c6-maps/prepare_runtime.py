"""Copy an unmodified, self-contained generation runtime into this folder."""
from pathlib import Path
import hashlib
import json
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
RUNTIME = HERE / 'runtime'

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    assert not (HERE / 'requests.json').exists(), 'never replace frozen runtime'
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
    # Acceleration artifacts only, no map/plan data. Copies have independent inodes.
    for name in ('stability', 'vessels'):
        shutil.copytree(ROOT / 'experiments/cache' / name,
                        RUNTIME / 'experiments/cache' / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('*.partial', '*.lock'))
    assert before == {name: sha(ROOT / name) for name in before}, 'source changed while copying'
    assert before == {name: sha(RUNTIME / name) for name in before}, 'copy mismatch'
    (HERE / 'runtime-snapshot.json').write_text(json.dumps(dict(files=before,
        builder_sha256=before['experiments/mvp/corpus_tools/build.py'],
        method='independent file copies; source bytes identical before/after snapshot'), indent=2)+'\n')
    print(json.dumps(dict(event='runtime_prepared', files=len(before))))

if __name__ == '__main__':
    main()
