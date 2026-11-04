"""Import the frozen Frontier planner without colliding with engine/planner.py."""
import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parent.parent / 'planner.py'
_name = '_frontier_frozen_planner09'
if _name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_name, _path)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _module
    _spec.loader.exec_module(_module)
planner = sys.modules[_name]
