"""Revision2 broad ExIt inventory guards, with no production source writes."""
import hashlib

import pytest

from test_batch_tool import setup


def add_exit(setup):
    tool, root, package, manifest, save = setup
    keep = root / 'src/llm/exit/unchanged.py'
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b'keep exact\n')
    old = root / 'src/llm/exit/runner.py'
    old.write_bytes(b'old exit\n')
    manifest['src_exit_before'] = tool.source_info(root, 'src/llm/exit')
    after = dict(manifest['src_exit_before'])
    for relative, payload in [('src/llm/exit/runner.py', b'new exit\n'),
                              ('src/llm/exit/outcome_epoch.py', b'new epoch\n')]:
        target = package / 'after' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        record = {'before': tool.file_info(root / relative), 'after': tool.file_info(target)}
        manifest['targets'][relative] = record
        after[relative] = record['after']
        manifest['artifacts']['after/' + relative] = hashlib.sha256(payload).hexdigest()
    manifest['src_exit_after'] = after
    save()
    return setup


def test_both_inventories_restored_exactly(setup):
    tool, root, _, manifest, _ = add_exit(setup)
    assert tool.execute('install', root)['state'] == 'installed'
    assert tool.source_info(root, 'src/llm/exit') == manifest['src_exit_after']
    assert tool.execute('rollback', root)['state'] == 'rolled_back'
    assert tool.source_info(root, 'src/llm/exit') == manifest['src_exit_before']
    assert not (root / 'src/llm/exit/outcome_epoch.py').exists()


@pytest.mark.parametrize('phase', ['install', 'rollback'])
@pytest.mark.parametrize('path', ['src/llm/exit/unchanged.py', 'src/llm/exit/foreign.py'])
def test_foreign_exit_changes_block_before_any_target_write(setup, phase, path):
    tool, root, _, _, _ = add_exit(setup)
    if phase == 'rollback':
        tool.execute('install', root)
    before = (root / 'src/stowage/old.py').read_bytes()
    (root / path).write_bytes(b'foreign exit edit\n')
    with pytest.raises(RuntimeError, match='mismatch|drift'):
        tool.execute(phase, root)
    assert (root / 'src/stowage/old.py').read_bytes() == before
    assert (root / path).read_bytes() == b'foreign exit edit\n'
