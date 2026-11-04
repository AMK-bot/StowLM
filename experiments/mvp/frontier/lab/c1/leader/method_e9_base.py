"""Frontier 0.9.0 unchanged, with only the mask's E9 clearance lookup corrected."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e9fix

e9fix.install()
spec = importlib.util.spec_from_file_location('frontier09_ref', HERE.parents[1] / 'ref' / 'frontier09.py')
ref = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ref)
propose = ref.propose
finish = ref.finish
