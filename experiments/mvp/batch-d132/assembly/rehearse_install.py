"""Exercise shell entry points only in a disposable, independently copied root.

This mechanics rehearsal does not claim numerical qualification: its private
package explicitly permits execution while the deliverable remains fail-closed.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from snapshot import ROOT, WORK, manifest

HERE = Path(__file__).resolve().parent
REHEARSAL = Path('/private/tmp/stowlm-d132-install-rehearsal-20261030')


def main():
    global REHEARSAL
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=REHEARSAL)
    parser.add_argument('--output', type=Path, default=HERE / 'install-rehearsal.json')
    args = parser.parse_args()
    REHEARSAL = args.root.resolve()
    if args.output.exists():
        raise RuntimeError('Never overwrite a prior rehearsal report')
    if REHEARSAL.exists():
        raise RuntimeError('Never reuse a possibly mutated rehearsal tree')
    delivery = json.loads((HERE / 'package/manifest.json').read_text())
    revision = delivery.get('revision', 1)
    runtime_manifest = HERE / ('revision2-combined-manifest.json' if revision == 2 else 'combined-manifest.json')
    selected = json.loads(runtime_manifest.read_text())
    shutil.copytree(Path(selected['baseline']), REHEARSAL)
    # Tokenizer-only data is read from the live, hash-pinned local asset by the
    # mask guard, rather than bundled in either runtime snapshot. Copy any
    # newly bound qualification input explicitly into this private rehearsal.
    for name, wanted in delivery['qualified_inputs'].items():
        target = REHEARSAL / name
        if not target.exists():
            source = ROOT / name
            assert hashlib.sha256(source.read_bytes()).hexdigest() == wanted['sha256']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    batch = REHEARSAL / 'experiments/mvp/batch-d132'
    assembly = batch / 'assembly'
    assembly.mkdir(parents=True)
    shutil.copytree(HERE / 'package', assembly / 'package')
    for name in ('batch_tool.py',):
        shutil.copy2(HERE / name, assembly / name)
    for name in ('INSTALL.sh', 'ROLLBACK.sh'):
        shutil.copy2(HERE.parent / name, batch / name)
    (REHEARSAL / '.venv').symlink_to(ROOT / '.venv', target_is_directory=True)
    packaged = assembly / 'package/manifest.json'
    value = json.loads(packaged.read_text())
    assert value['qualification_complete'] is False, 'Use this helper only before qualification'
    value['qualification_complete'] = True
    value['rehearsal_only'] = 'Private mechanics override, not a qualified release'
    packaged.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    (packaged.parent / 'manifest.sha256').write_text(hashlib.sha256(packaged.read_bytes()).hexdigest() + '\n')
    environment = dict(os.environ)
    environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0',
        OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1',
        MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1')
    original = manifest(REHEARSAL)
    original_exit = manifest(REHEARSAL, 'src/llm/exit') if revision == 2 else None
    commands = []
    for name, arguments in [('INSTALL.sh', ['--check-only']), ('INSTALL.sh', []),
                            ('INSTALL.sh', []), ('ROLLBACK.sh', ['--check-only']),
                            ('ROLLBACK.sh', []), ('ROLLBACK.sh', [])]:
        started = time.monotonic()
        result = subprocess.run([str(batch / name), *arguments], cwd=REHEARSAL,
                                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, check=False)
        record = {'command': [name, *arguments], 'returncode': result.returncode,
                  'wall_s': time.monotonic() - started,
                  'stdout': result.stdout, 'stderr': result.stderr}
        commands.append(record)
        args.output.write_text(json.dumps({'root': str(REHEARSAL),
            'private_qualification_override': True, 'commands': commands}, indent=2) + '\n')
        if result.returncode:
            raise RuntimeError(f'Rehearsal failed: {record}')
    assert manifest(REHEARSAL) == original, 'Rollback did not restore exact whole source tree'
    assert manifest(ROOT) == original, 'Production source changed during rehearsal'
    if revision == 2:
        assert manifest(REHEARSAL, 'src/llm/exit') == original_exit
        assert manifest(ROOT, 'src/llm/exit') == original_exit
    receipt = {'root': str(REHEARSAL), 'private_qualification_override': True,
               'production_install_invocations': 0, 'commands': commands,
               'whole_source_exact_after_rollback': True,
               'exit_inventory_exact_after_rollback': revision == 2,
               'production_source_unchanged': True, 'passed': True}
    args.output.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'passed': True, 'root': str(REHEARSAL),
                      'command_wall_s': [row['wall_s'] for row in commands]}))


if __name__ == '__main__':
    main()
