"""All validated metadata readers use the shared byte-snapshot primitive."""
import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
READERS = [
    path for directory in ('src/eval', 'experiments/campaign-g1', 'experiments/gold-plans/campaign')
    for path in sorted((ROOT / directory).glob('*.py'))
    if not path.name.startswith('test') and path.name != 'snapshot_io.py'
] + [ROOT / 'experiments/gold-plans/tiny/driver.py']


@pytest.mark.parametrize('path', READERS, ids=lambda path: str(path.relative_to(ROOT)))
def test_metadata_readers_do_not_bypass_shared_snapshot(path):
    tree = ast.parse(path.read_text())
    bypasses = [node.lineno for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {'read_bytes', 'read_text'}]
    model_reopens = [node.lineno for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                     and node.func.id in {'load_instance', 'load_plan'}]
    assert not bypasses + model_reopens, (
        f'{path}: file reads bypass shared snapshot at {bypasses + model_reopens}')
