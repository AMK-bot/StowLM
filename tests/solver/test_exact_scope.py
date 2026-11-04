"""Certificate scope survives screening modes and an initialization timeout."""
from types import SimpleNamespace

import pytest

from stowage.model.fixtures import build_instance


def test_watchdog_timeout_cannot_lose_engine_domain_restriction(monkeypatch):
    import stowage.solver.exact as exact

    monkeypatch.setattr(exact, "run_with_deadline", lambda *args, **kwargs: SimpleNamespace(
        timed_out=True, checkpoint=None, value=None, worker_pid=123))
    result = exact.solve_exact(build_instance(), time_limit_s=1.)
    assert result.status == "TIME_LIMIT" and result.plan is None
    assert result.metrics["physical_domain_optimality_certified"] is False
    assert result.metrics["objective_components_certified"] == ["weighted_nonproductive_lifts"]
    assert result.metrics["domain_restrictions"] == [
        "public_engine_actions", "stop_at_manifest_completion"]
    assert result.metrics["screening_guarantee"] == "not_reported"


@pytest.mark.parametrize("screen,guarantee,screen_restricted", [
    ("on", "empirical_necessary_relaxation", True),
    ("off: config extra['global_screen']=0 (diagnostic)", "inactive", False),
    ("unavailable: missing coefficients", "not_reported", False),
])
def test_engine_scope_reports_actual_screen_not_configuration(
        monkeypatch, screen, guarantee, screen_restricted):
    import stowage.solver.exact as exact
    import stowage.solver.exact_engine as engine

    class CompletedEngine:
        diagnostics = {"global_screen": screen}

        def is_complete(self):
            return True

    instance = build_instance()
    instance.load_list = []
    instance.discharge_list = []
    instance.containers = []
    instance.yard.stacks = []
    monkeypatch.setattr(engine, "make_feasibility_engine", lambda *args, **kwargs: CompletedEngine())
    # This test reports certificate scope only, and never accepts a witness.
    monkeypatch.setattr(exact, "_validate_terminal", lambda *args, **kwargs:
                        ("no", None, args[1].provenance))
    result = engine.solve_engine(instance, time_limit_s=1., max_nodes=8, seed=0, num_workers=1)
    assert result.plan is None
    assert result.metrics["physical_domain_optimality_certified"] is False
    assert result.metrics["screening_guarantee"] == guarantee
    assert ("empirically_qualified_linear_screen" in result.metrics["domain_restrictions"]) == screen_restricted
