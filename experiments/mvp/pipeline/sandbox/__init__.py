"""Drag a container, screen it, then ask the independent full checker."""
from pathlib import Path
import sys

# The prescribed pytest command supplies src and engine/lab on PYTHONPATH.
# Shared pipeline shapes live in the repository's experiments namespace.
_root = str(Path(__file__).resolve().parents[4])
if _root not in sys.path:
    sys.path.insert(0, _root)

from experiments.mvp.pipeline.contract.schema import Explanation, MoveVerdict
from .moves import fast_screen, move_plan, try_move
from .explain import VerdictExplainer, explain_verdict

__all__ = ['Explanation', 'MoveVerdict', 'VerdictExplainer', 'explain_verdict',
           'fast_screen', 'move_plan', 'try_move']
