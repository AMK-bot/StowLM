"""Archive qualified D132 v1, then compose ExIt additions in a new scratch epoch."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from snapshot import ROOT, WORK, manifest

HERE = Path(__file__).resolve().parent
BATCH = HERE.parent
V2 = Path('/private/tmp/stowlm-d132-assembly-v2-20261030')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    archive = BATCH / 'revisions/v1'
    if archive.exists() or V2.exists():
        raise RuntimeError('Never overwrite a prior release archive or scratch revision')
    original = json.loads((HERE / 'combined-manifest.json').read_text())
    package = json.loads((HERE / 'package/manifest.json').read_text())
    assert package['qualification_complete'] is True
    assert manifest(ROOT) == original['src_stowage_before']
    assert manifest(WORK / 'candidate') == original['src_stowage_after']
    archive.mkdir(parents=True)
    shutil.copytree(HERE, archive / 'assembly', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('INSTALL.sh', 'ROLLBACK.sh', 'QUALIFIED.json', 'RELEASE-READY.json',
                 'REPORT.md', 'STATUS.md', 'COMPUTE.json', 'INBOX.md'):
        if (BATCH / name).exists():
            shutil.copy2(BATCH / name, archive / name)
    qualification = json.loads((BATCH / 'QUALIFIED.json').read_text())
    for row in qualification['guards']:
        source = ROOT / row['path']
        assert sha(source) == row['sha256']
        relative = source.relative_to(BATCH)
        target = archive / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)
        assert sha(target) == row['sha256']
    preserved = {str(path.relative_to(archive)): sha(path)
                 for path in sorted(archive.rglob('*')) if path.is_file()}
    (archive / 'ARCHIVE-MANIFEST.json').write_text(json.dumps({
        'created_utc': datetime.now(timezone.utc).isoformat(), 'files': preserved,
        'v1_roots_untouched': [str(WORK / 'baseline'), str(WORK / 'candidate')],
        'production_installed': False}, indent=2, sort_keys=True) + '\n')
    before = {'stowage': manifest(ROOT), 'exit': manifest(ROOT, 'src/llm/exit')}
    V2.mkdir()
    for arm in ('baseline', 'candidate'):
        shutil.copytree(WORK / arm, V2 / arm,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
        target = V2 / arm / 'src/llm/exit'
        shutil.rmtree(target)
        shutil.copytree(ROOT / 'src/llm/exit', target,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
        assert manifest(V2 / arm, 'src/llm/exit') == before['exit']
    patches = [
        ('experiments/mvp/exit/patches/d134-dataset/parallel-dataset.diff',
         'd54b0d205c70ee1eacb9cae57ec432dbf6175f3e9a878b156c78e9a6237bb7a0'),
        ('experiments/mvp/exit/patches/d132/audit-outcome-epoch.diff',
         'f5ed072b6b723a6c24543f2ee6130339d82e81af1c6f5c2efcfb124630c64147'),
        ('experiments/mvp/exit/patches/d135-dpo-checkpoint/checkpoint-suffix.diff',
         '2bb264acb0cd6c77f62cf187da6b552084d193992df077387d03af8db11926ef'),
    ]
    delivery = HERE / 'revision2-patches'
    delivery.mkdir()
    applied = []
    for index, (relative, expected) in enumerate(patches, 4):
        source = ROOT / relative
        assert sha(source) == expected, f'INBOX patch checksum drift: {relative}'
        target = delivery / f'{index:02d}-{source.name}'
        shutil.copy2(source, target)
        subprocess.run(['git', 'apply', '--check', str(target)], cwd=V2 / 'candidate', check=True)
        subprocess.run(['/usr/bin/patch', '-p1', '--batch', '--forward', '-i', str(target)],
                       cwd=V2 / 'candidate', check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(['git', 'apply', '--reverse', '--check', str(target)], cwd=V2 / 'candidate', check=True)
        applied.append({'original_path': relative, 'copied_path': str(target.relative_to(ROOT)), 'sha256': expected})
    after_exit = manifest(V2 / 'candidate', 'src/llm/exit')
    changed_exit = [name for name in sorted(set(before['exit']) | set(after_exit))
                    if before['exit'].get(name) != after_exit.get(name)]
    assert len(changed_exit) == 10
    composed = json.loads((ROOT / 'experiments/mvp/exit/patches/d134-combined/compose-check.json').read_text())
    for name in changed_exit:
        assert after_exit[name]['sha256'] == composed['composed_targets'][name]
        ast.parse((V2 / 'candidate' / name).read_text(), filename=name)
    assert manifest(ROOT) == before['stowage']
    assert manifest(ROOT, 'src/llm/exit') == before['exit'], 'Live ExIt changed during copy/composition'
    assert manifest(V2 / 'baseline') == original['src_stowage_before']
    assert manifest(V2 / 'candidate') == original['src_stowage_after']
    assert manifest(WORK / 'baseline') == original['src_stowage_before']
    assert manifest(WORK / 'candidate') == original['src_stowage_after']
    new = {**original, 'revision': 2, 'baseline': str(V2 / 'baseline'), 'candidate': str(V2 / 'candidate'),
           'src_exit_before': before['exit'], 'src_exit_after': after_exit,
           'changed_exit': changed_exit, 'exit_patches': applied,
           'carried_v1_combined_manifest_sha256': sha(archive / 'assembly/combined-manifest.json'),
           'carried_v1_qualification_sha256': sha(archive / 'QUALIFIED.json'),
           'carried_v1_release_ready_sha256': sha(archive / 'RELEASE-READY.json'),
           'v1_guard_dependency_carryover_requires_independent_review': True,
           'qualification_complete': False}
    output = HERE / 'revision2-combined-manifest.json'
    output.write_text(json.dumps(new, indent=2, sort_keys=True) + '\n')
    (HERE / 'revision2-baseline-manifest.json').write_text(json.dumps(before, indent=2, sort_keys=True) + '\n')
    ready = {'ready_for_cpu_qualification': True, 'revision': 2,
             'candidate': str(V2 / 'candidate'), 'baseline': str(V2 / 'baseline'),
             'combined_manifest_path': str(output.relative_to(ROOT)), 'combined_manifest_sha256': sha(output),
             'src_stowage_identical_to_qualified_v1_candidate': True,
             'src_exit_inventory_before': len(before['exit']), 'src_exit_inventory_after': len(after_exit),
             'changed_exit': changed_exit, 'production_written': False,
             'v1_archived_before_mutation': str(archive.relative_to(ROOT)),
             'candidate_source_immutable_from_now': True}
    (HERE / 'READY-v2.json').write_text(json.dumps(ready, indent=2, sort_keys=True) + '\n')
    print(json.dumps(ready))


if __name__ == '__main__':
    main()
