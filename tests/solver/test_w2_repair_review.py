"""Review C: diagnostic options must not change the frozen production API."""
from importlib import import_module
import inspect

from stowage.actions.samples import tiny_instance
from stowage.model import PlannerResult


def test_frozen_wrapper_keeps_original_signature_and_production_defaults(monkeypatch):
    module = import_module("stowage.solver.repair")
    assert "feasibility_config" not in inspect.signature(module.repair).parameters
    seen = []
    sentinel = object()
    monkeypatch.setattr(module, "_repair", lambda *args, **kwargs: seen.append((args, kwargs)) or sentinel)
    instance = tiny_instance(71, n_load=0, n_other=0, p40=0)
    original = PlannerResult(status="dead_end", planner="test", planner_version="test")
    assert module.repair(instance, original, None, time_limit_s=2, seed=17) is sentinel
    assert seen == [((instance, original, None), {"time_limit_s": 2, "seed": 17})]


def test_internal_helper_retains_diagnostic_configuration():
    from stowage.feasibility import FeasibilityConfig

    module = import_module("stowage.solver.repair")
    instance = tiny_instance(71, n_load=0, n_other=0, p40=0)
    original = PlannerResult(status="dead_end", planner="test", planner_version="test")
    configured = module._repair(instance, original, None, time_limit_s=0,
        feasibility_config=FeasibilityConfig(extra={"global_screen": 0}))
    assert configured.status == "timeout"
