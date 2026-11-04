"""Evidence-bound planner copilot."""
from pathlib import Path
import sys

# Direct pytest supplies engine/lab/src on PYTHONPATH; readers also need this namespace.
_root = str(Path(__file__).resolve().parents[4])
if _root not in sys.path:
    sys.path.insert(0, _root)

__all__ = ['Copilot', 'ActionGrant', 'TurnRequest']


def __getattr__(name):
    # The warm launcher imports this package before the final freeze. Delay
    # agent/authorization imports until callers actually request those exports.
    if name == 'Copilot':
        from .agent import Copilot
        return Copilot
    if name in ('ActionGrant', 'TurnRequest'):
        from .authorization import ActionGrant, TurnRequest
        return {'ActionGrant': ActionGrant, 'TurnRequest': TurnRequest}[name]
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__():
    return sorted(set(globals()) | set(__all__))
