"""The comparison baseline removes executable optimizations, not owner fixes."""
from pathlib import Path
import ast
import importlib.util

from stowage.linear.dependencies import _semantic_hash

_spec = importlib.util.spec_from_file_location('speedup_baseline', Path(__file__).with_name('speedup_baseline.py'))
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
baseline_sources = _module.baseline_sources
_ROOT = Path(__file__).resolve().parents[2]
_FILES = ('conventions.py', 'actions/expander.py', 'feasibility/_engine.py')


def sources():
    return {key: (_ROOT / 'src/stowage' / key).read_text() for key in _FILES}


def method(raw, name):
    return next(node for node in ast.walk(ast.parse(raw))
                if isinstance(node, ast.FunctionDef) and node.name == name)


def test_disk_baseline_removes_cache_from_whole_ast_linear_identity():
    current = sources()
    baseline = baseline_sources(current)
    if '_memoize_string' in current['conventions.py']:
        assert _semantic_hash(current['conventions.py'], None) != _semantic_hash(baseline['conventions.py'], None)
    assert '_memoize_string' not in baseline['conventions.py']
    assert '_StringResult' not in baseline['conventions.py']
    assert 'lru_cache' not in baseline['conventions.py']
    assert baseline_sources(baseline) == baseline


def test_disk_baseline_commit_is_public_loop_with_original_error_wrapping():
    baseline = baseline_sources(sources())
    expected = ast.parse('''
def _validated_commit(self, trial: Expander, moves: list[Move]) -> list[Move]:
    state = self.world_state()
    try:
        for move in moves:
            state = apply_move(self.instance, state, move)
    except IllegalMoveError as exc:
        raise ValueError(f"unexpandable: {exc.violation.rule_id}: {exc.violation.message}") from exc
    self.__dict__.update(trial.__dict__)
    return moves
''').body[0]
    assert ast.dump(method(baseline['actions/expander.py'], '_validated_commit')) == ast.dump(expected)
    assert '_NATIVE_APPLY_MOVE' not in baseline['actions/expander.py']


def test_disk_baseline_restow_preserves_current_pair_rejection_bookkeeping():
    current = sources()
    before = method(current['feasibility/_engine.py'], '_restow_places')
    baseline = baseline_sources(current)
    after = method(baseline['feasibility/_engine.py'], '_restow_places')
    def bookkeeping(node):
        return [ast.dump(item) for item in ast.walk(node)
                if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                and item.func.id in {'record_rejection', '_rejected_pairs'}]
    assert bookkeeping(after) == bookkeeping(before)
    text = ast.unparse(after)
    assert not any(isinstance(node, ast.Name) and node.id in {'Replay', 'public_replay'}
                   for node in ast.walk(after))
    assert 'replay = apply_move(self.instance, replay, move)' in text
    assert "evaluate(replay.ship)" in text
    assert '_NATIVE_APPLY_MOVE' not in baseline['feasibility/_engine.py']


def test_disk_baseline_keeps_unknown_sources_and_owner_changes():
    current = sources()
    current['rules/catalogue.py'] = 'OWNER_SENTINEL = 123\n'
    current['conventions.py'] += '\nOWNER_SENTINEL = 456\n'
    baseline = baseline_sources(current)
    assert baseline['rules/catalogue.py'] == current['rules/catalogue.py']
    assert 'OWNER_SENTINEL = 456' in baseline['conventions.py']
    assert set(baseline) == set(current)
