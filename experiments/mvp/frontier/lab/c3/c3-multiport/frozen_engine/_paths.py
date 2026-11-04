"""Locate unchanged native dependencies without importing the live engine."""
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'src/stowage').is_dir())
FRONTIER = ROOT / 'experiments/mvp/frontier'
