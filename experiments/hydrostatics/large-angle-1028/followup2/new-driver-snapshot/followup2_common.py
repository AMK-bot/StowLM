"""New-file-only follow-up plumbing; production modules are read-only."""
from pathlib import Path
import hashlib
import json
import os
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / 'followup2'
sys.path.insert(0, str(HERE))
os.environ['STOWAGE_STABILITY_DIRECT'] = '1'
os.environ.setdefault('STOWAGE_STABILITY_TIME_BUDGET_S', '120')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    """Never overwrite a reviewed artifact, or even an earlier new result."""
    with Path(path).open('x') as f:
        json.dump(value, f, indent=2, default=float)
        f.write('\n')


def source_hashes():
    # These two modules implement batch dispatch/in-port replay. Neither is
    # imported or called by the explicit direct seagoing paths in this study.
    # Their broader epoch hashes remain in earlier manifests and drift audits.
    stability = [p for p in sorted((ROOT/'src/stowage/checker').glob('stability*.py'))
                 if p.name not in {'stability_batch.py','stability_states.py'}]
    paths = [*stability,
             *sorted((ROOT/'src/stowage/vessel').glob('*.py')),
             ROOT/'src/stowage/model/voyage.py', ROOT/'src/stowage/checker/full.py',
             ROOT/'src/stowage/generate/background_acceptance.py',
             *sorted((ROOT/'params/fleet').glob('feeder_*.toml')),
             HERE/'feeder_envelope.py', HERE/'extend19.py', HERE/'iges_upper19.py',
             HERE/'marlantes-fig22-samples.csv', *sorted(HERE.glob('followup2_*.py')),
             ROOT/'data/raw/kcs/kcs.fix', ROOT/'data/raw/kcs/KCS_hull_SVA.igs']
    return {str(p.relative_to(ROOT)): sha(p) for p in paths}


def prepare():
    OUT.mkdir(exist_ok=False)
    # Snapshot all pre-existing reviewed files. Imports run without bytecode writes.
    old = {str(p.relative_to(HERE)):sha(p) for p in HERE.rglob('*')
           if p.is_file() and OUT not in p.parents and not p.name.startswith('followup2')}
    save(OUT/'reviewed-files-before.json', old)
    save(OUT/'source-hashes-at-start.json', source_hashes())
    (OUT/'source-snapshot').mkdir()
    for name in source_hashes():
        p = ROOT/name
        if p.suffix in ('.py', '.toml'):
            target=OUT/'source-snapshot'/name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as f: f.write(p.read_bytes())


if __name__ == '__main__':
    prepare()
