"""Explicit synthetic linkage for isolated app adapter/gating tests.

These reports test presentation contracts; binding does not certify physics.
"""
from stowage.checker.full import _input_bindings


def bind_report(report, instance, plan):
    report.provenance.parents = sorted({*report.provenance.parents, *_input_bindings(instance, plan)})
    return report


# ---------------------------------------------------------------- historical artefacts (D306, task 5.2)
# Tests of older run artefacts the curated export doesn't ship. The v6 demo never reads them; they audit older films and runs.
# In the dev tree they run as before; in a clean copy they skip and name the file they'd need, so a skip is never silent.
from pathlib import Path as _Path

import pytest as _pytest

_ROOT = _Path(__file__).resolve().parents[2]


def missing(*paths: str) -> list[str]:
    return [p for p in paths if not (_ROOT / p).exists() and not any(_ROOT.glob(p))]


def historical(*paths: str):
    """Skip unless every repo-relative path (or glob) exists: a dev-only artefact, not part of the shipped demo."""
    gone = missing(*paths)
    return _pytest.mark.skipif(bool(gone), reason=f"dev-only: historical artefact not shipped in this copy: {', '.join(gone)}")
