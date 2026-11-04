"""Allow the documented direct pytest command with the fleet PYTHONPATH."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
