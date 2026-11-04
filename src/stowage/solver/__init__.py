"""Solvers (plans 4.2 ballast, 4.6 exact, 4.7 scalable, 4.8 two-step baseline, 5.7 repair).

Each planner module exposes a class implementing ``stowage.planner.Planner``. Import submodules directly
(``from stowage.solver.ballast import finish_ballast``); this package imports nothing heavy at import time.
"""

# Keep package import light: planner/search machinery loads only when requested.
_EXPORTS = {
    "ScalableSolver": "stowage.solver.scalable",
    "ScalableConfig": "stowage.solver.scalable",
    "complete": "stowage.solver.scalable",
    "improve": "stowage.solver.scalable",
    "ScalableDigOutPolicy": "stowage.solver.scalable_yard",
    "dig_out": "stowage.solver.scalable_yard",
}
__all__ = list(_EXPORTS)


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(_EXPORTS[name]), name)
    globals()[name] = value
    return value
