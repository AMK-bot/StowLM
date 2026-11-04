"""Build the D137 revision 2 patch set from the tested scratch tree; touches no src/ or live experiment file.

Inputs: /private/tmp/d137r2-base (installed tree + original D137 diffs), /private/tmp/d137r2 (same + D141/D142 fixes) and
/private/tmp/d137r2-prefetch/{base,revised}. Writes the diffs here, plus installed-manifest.json (EXPECTED bytes, not an
assertion of installation). Then proves the apply order on a fresh copy of the live tree reproduces the scratch tree.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p/'pyproject.toml').is_file())
BASE, FINAL = Path('/private/tmp/d137r2-base'), Path('/private/tmp/d137r2')
PREFETCH = Path('/private/tmp/d137r2-prefetch')
D137 = HERE.parent
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


def diff(a_root, b_root, sub, out):
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache')
        for name, root in (('old', a_root), ('new', b_root)):
            shutil.copytree(root/sub, t/name/sub, ignore=ignore)
        done = subprocess.run(['git', 'diff', '--no-index', '--no-color', f'old/{sub}', f'new/{sub}'],
                              cwd=t, capture_output=True, text=True)
        assert done.returncode in (0, 1), done.stderr
        text = done.stdout.replace('a/old/', 'a/').replace('b/new/', 'b/').replace(' old/', ' ').replace(' new/', ' ')
    Path(out).write_text(text)
    return text


def main():
    src = diff(BASE, FINAL, 'src/llm/exit', HERE/'01-loop-fixes-src.diff')
    tests = diff(BASE, FINAL, 'tests/llm/exit', HERE/'02-loop-fixes-tests.diff')
    # The prefetcher is an experiment script, deliberately kept out of the live tree until install.
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        for name, source in (('old', PREFETCH/'prefetch_replay.base.py'), ('new', PREFETCH/'prefetch_replay.revised.py')):
            target = t/name/'experiments/mvp/exit/prefetch_replay.py'
            target.parent.mkdir(parents=True)
            shutil.copy2(source, target)
        done = subprocess.run(['git', 'diff', '--no-index', '--no-color', 'old', 'new'], cwd=t, capture_output=True, text=True)
        (HERE/'03-prefetch-outcome-id.diff').write_text(done.stdout.replace('a/old/', 'a/').replace('b/new/', 'b/'))
    changed = sorted(str(p.relative_to(FINAL)) for p in (FINAL/'src/llm/exit').rglob('*.py') if '__pycache__' not in p.parts
                     and (not (BASE/p.relative_to(FINAL)).exists() or sha(p) != sha(BASE/p.relative_to(FINAL))))
    original = json.loads((D137/'installed-manifest.json').read_text())
    targets = dict(original['targets'])
    targets.update({name: sha(FINAL/name) for name in changed})
    before = {name: (sha(ROOT/name) if (ROOT/name).exists() else None) for name in targets}
    (HERE/'installed-manifest.json').write_text(json.dumps(dict(schema='exit-d137-installed-v1', targets=targets, before=before,
        prerequisites=original['prerequisites'], revision=2, production_installed=False), indent=2, sort_keys=True)+'\n')
    # Proof: a fresh copy of the live tree + the apply order == the tested scratch tree.
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
        shutil.copytree(ROOT/'src/llm', t/'src/llm', ignore=ignore)
        shutil.copytree(ROOT/'tests/llm/exit', t/'tests/llm/exit', ignore=ignore)
        shutil.copytree(ROOT/'src/stowage', t/'src/stowage', ignore=ignore)
        subprocess.run(['git', 'init', '-q', '.'], cwd=t, check=True)
        for patch in (D137/'candidate-teacher-loop.diff', D137/'candidate-teacher-loop-tests.diff',
                      HERE/'01-loop-fixes-src.diff', HERE/'02-loop-fixes-tests.diff'):
            subprocess.run(['git', 'apply', str(patch)], cwd=t, check=True)
        names = [str(p.relative_to(FINAL)) for sub in ('src/llm', 'tests/llm/exit') for p in (FINAL/sub).rglob('*')
                 if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
        wrong = [n for n in names if not (t/n).exists() or sha(t/n) != sha(FINAL/n)]
        assert not wrong, wrong[:5]
    print(json.dumps(dict(src_diff_lines=src.count('\n'), tests_diff_lines=tests.count('\n'), changed=changed,
                          apply_order_reproduces_scratch=True, files_compared=len(names),
                          manifest_sha256=sha(HERE/'installed-manifest.json'))))


if __name__ == '__main__':
    main()
