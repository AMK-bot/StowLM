"""One-shot, preregistered pipeline evaluation. Importing never opens maps."""
from pathlib import Path
import sys

_root = str(Path(__file__).resolve().parents[4])
if _root not in sys.path:
    sys.path.insert(0, _root)
