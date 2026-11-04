"""Confirmed reader rules -> the source-pinned Frontier Engine 0.13.0."""
from pathlib import Path
import sys

# The prescribed pytest invocation supplies engine/lab/src on PYTHONPATH.
# The repository's experiments namespace is also needed by reader imports.
_root = str(Path(__file__).resolve().parents[4])
if _root not in sys.path:
    sys.path.insert(0, _root)

from experiments.mvp.pipeline.binding.adapter import execute_brief, plan_with_rules
from experiments.mvp.pipeline.binding import confirmation as _confirmation
from experiments.mvp.pipeline.binding.confirmation import ConfirmedBrief, confirm_brief

# The prescribed pytest import can name this package ``binding``. Keep the
# confirmation class and signing key identical through both import spellings.
sys.modules.setdefault(__name__ + '.confirmation', _confirmation)

__all__ = ['plan_with_rules', 'execute_brief', 'confirm_brief', 'ConfirmedBrief']

# Review-three public policy; the immutable adapter remains the non-hatch core.
from .review3.api import plan_with_rules, execute_brief, confirmation_text, planner_message
__all__ += ['confirmation_text', 'planner_message']
