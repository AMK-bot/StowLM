"""Fast behavioral checks of exact-margin search and the conservative LP boundary."""

from time import monotonic
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.model import MovePlan, PartReport, Violation
from stowage.model.fixtures import build_instance
from stowage.solver.ballast_search import Deadline, Found, search


def test_smooth_recovery_requires_continuous_correction():
    target = np.array([0.27, 0.81])
    evaluated = []

    def evaluate(fill):
        assert np.all((0 <= fill) & (fill <= 1))
        evaluated.append(fill.copy())
        margins = 1e-4 - np.abs(fill - target)
        if np.all(margins >= 0):
            raise Found
        return margins

    with pytest.raises(Found):
        search([np.zeros(2)], evaluate, monotonic() + 5, seed=19)
    np.testing.assert_allclose(evaluated[-1], target, atol=1e-4)
    assert not np.any(np.isin(evaluated[-1], [0, 0.5, 1]))


def test_endpoint_restart_handles_discontinuous_free_surface():
    evaluated = []

    def evaluate(fill):
        evaluated.append(fill.copy())
        if np.array_equal(fill, [1.0, 0.0]):
            raise Found
        # A deliberately discontinuous free-surface penalty: entering a slack
        # tank worsens the margin, so a derivative cannot discover the endpoint.
        slack = np.any((fill > 0) & (fill < 1))
        return np.array([-2.0 if slack else -1.0])

    with pytest.raises(Found):
        search([np.array([0.4, 0.4])], evaluate, monotonic() + 5, seed=19)
    np.testing.assert_array_equal(evaluated[-1], [1.0, 0.0])


def test_seeded_unsuccessful_traversal_is_deterministic():
    def run(seed):
        seen = []

        def evaluate(fill):
            seen.append(tuple(fill))
            return np.array([-1.0])

        search([np.zeros(3)], evaluate, monotonic() + 5, seed=seed)
        return seen

    first = run(11)
    assert first == run(11)
    assert first != run(12)


def test_expired_deadline_stops_before_physics():
    def evaluate(fill):
        pytest.fail("expired search must not call exact physics")

    with pytest.raises(Deadline):
        search([np.zeros(2)], evaluate, monotonic() - 1, seed=0)


def test_lp_uses_strict_error_margins_and_final_exact_lashing_gate(monkeypatch):
    import stowage.checker.lashing as lashing
    import stowage.checker.stability as stability
    import stowage.checker.strength as strength
    import stowage.checker.visibility as visibility
    import stowage.linear.model as linear
    import stowage.moves as moves
    import stowage.solver.ballast as ballast
    import stowage.solver.ballast_proof as proof

    from stowage.model.voyage import VoyageSpec, VoyageTankPolicy
    instance = build_instance()
    instance.vessel.voyage_policy = VoyageTankPolicy(
        groups=[dict(id='fuel', kind='fuel_oil', tank_ids=['FO-1'])], drawdown_order=['FO-1'])
    instance.voyage = VoyageSpec.model_validate(dict(
        departure=dict(id='d', phase='departure', call_index=0, locode='THLCH',
                       environment_id='d', density_id='d', water_density_kg_m3=1025.),
        arrival=dict(id='a', phase='arrival', call_index=1, locode='THBKK',
                     environment_id='a', density_id='a', water_density_kg_m3=1025.)))
    plan = MovePlan(id="lp-boundary-test", instance_id=instance.id, call_index=instance.call_index)
    tanks = sorted((t for t in instance.vessel.tanks if t.adjustable), key=lambda tank: tank.id)
    capacities = {tank.id: tank.capacity_m3 * tank.content_density_kg_m3 for tank in tanks}
    model = object()
    calls = []
    monkeypatch.setattr(moves, "apply_plan", lambda *a: SimpleNamespace(
        legal=True, final_state=SimpleNamespace(ship=instance.onboard)))
    monkeypatch.setattr(proof, "prove_infeasible", lambda *a, **kw: None)
    monkeypatch.setattr(linear, "build_linear_model", lambda inst, **kwargs: model)
    import stowage.linear.bands as bands
    monkeypatch.setattr(bands, "band_for_instance", lambda *a, **kw: object())

    def lp(candidate_model, ship, inst, *, direction, margin_scale):
        assert candidate_model is model and inst is instance
        assert direction == "conservative"
        assert margin_scale == 1.001
        calls.append("lp")
        return SimpleNamespace(status="optimal", tank_masses_kg={
            tank_id: capacity * 0.25 for tank_id, capacity in capacities.items()})

    monkeypatch.setattr(linear, "solve_ballast_lp", lp)

    def checker(name):
        def check(inst, ship, *, call_index):
            fractions = tuple(ship.tank_masses_kg[tank.id] / capacities[tank.id] for tank in tanks)
            calls.append((name, fractions))
            violations = []
            if name == "lashing" and not all(abs(value - 0.75) < 1e-12 for value in fractions):
                violations = [Violation(rule_id="iacs.member_irs.container_securing", severity="hard", call_index=call_index,
                                        message="Synthetic exact securing load fails at the LP condition.")]
            return PartReport(name=name, violations=violations)
        return check

    for module, name in [(stability, "stability"), (strength, "strength"),
                         (visibility, "visibility"), (lashing, "lashing")]:
        monkeypatch.setattr(module, f"check_{name}", checker(name))

    def correction(fills, evaluate, deadline, seed, **kwargs):
        evaluate(fills[0])
        assert calls[0] == "lp"
        assert ("lashing", tuple([0.25] * len(tanks))) in calls
        evaluate(np.full(len(tanks), 0.75))

    monkeypatch.setattr(ballast, "search", correction)
    outcome = ballast._finish_ballast(instance, plan, time_limit_s=5, seed=19)
    assert outcome.status == "ok"
    assert set(outcome.parts) == {"stability", "strength", "visibility", "lashing",
                                  "arrival/stability", "arrival/strength"}
    assert all(not part.violations and part.ran for part in outcome.parts.values())
    assert outcome.plan.ballast.source == "finisher"
    for tank_id, capacity in capacities.items():
        assert outcome.plan.ballast.tank_masses_kg[tank_id] == pytest.approx(capacity * 0.75)
