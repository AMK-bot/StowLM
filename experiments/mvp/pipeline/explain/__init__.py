"""Evidence-linked box explanations, in English and Thai."""
from pathlib import Path
import sys

# Direct pytest collection imports this folder before adding the repository root.
_ROOT = str(Path(__file__).resolve().parents[4])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from experiments.mvp.pipeline.contract.schema import Explanation
from .api import explain_box, explain_move, statement_menu
from .cache import estimate_cost, load_cached, prefetch

__all__ = ['Explanation', 'explain_box', 'explain_move', 'statement_menu', 'prefetch', 'load_cached', 'estimate_cost']
