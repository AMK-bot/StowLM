"""Voyage packet to checked, labelled plan package."""
from pathlib import Path
import sys

_root = str(Path(__file__).resolve().parents[4])
if _root not in sys.path:
    sys.path.insert(0, _root)

from .assembly import assemble
from .core import PipelineOutcome, run_packet

__all__ = ['assemble', 'run_packet', 'PipelineOutcome']
