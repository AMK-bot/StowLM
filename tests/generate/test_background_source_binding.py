"""Real D58 evidence cannot outlive the checker's pinned external sources."""
from pathlib import Path

import pytest

from stowage.generate import background_acceptance as gate
from stowage.io import load_instance
from stowage.params import repo_root


def _archived_instance():
    return load_instance(repo_root() / "experiments/background-1028/review-fixes/"
                         "retry-random-feeder_800-seed0.instance.json")


def _build_current_background():
    from stowage.generate.random_voyage import build_voyage_spec
    from stowage.generate.random_yard import _tiny_region
    from stowage.model import Instance
    from stowage.model.port import PortCall, Rotation
    from stowage.model.ship import ShipState
    from stowage.model.yard import YardState
    from stowage.ports.profiles import load_port
    from stowage.vessel.fleet import load_vessel
    from stowage.vessel.fleet_conditions import departure_tank_masses
    from stowage.vessel.fleet_slices import recondition_slice

    vessel = recondition_slice(_tiny_region(load_vessel("feeder_800")),
                               expected_load_mass_kg=50_000., seed=7)
    port = load_port("laem_chabang_like")
    rotation = Rotation(service="background-source-binding", calls=[
        PortCall(locode=port.locode, archetype=port.archetype, index=index)
        for index in (0, 1)])
    return Instance(id="current-background-source-binding", stage="A", size_class="tiny",
        seed=7, vessel=vessel, rotation=rotation, port=port, call_index=0,
        yard=YardState(yard_id=port.yard.id), containers=[], load_list=[],
        onboard=ShipState(vessel_id=vessel.id, tank_masses_kg=departure_tank_masses(vessel)),
        voyage=build_voyage_spec(rotation, 0, port, seed=7))


def _current_background(cache_root):
    from stowage.generate.physics_identity import current_physics_identity

    cache_root.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        physics_sha256 = current_physics_identity()["sha256"]
        path = cache_root / f"{physics_sha256}.instance.json"
        instance = load_instance(path) if path.exists() else _build_current_background()
        # Other owners may edit physics, or a cold checker may create tables,
        # during construction. Never assign the new identity to old evidence.
        if current_physics_identity()["sha256"] != physics_sha256:
            continue
        result = gate.evaluate_background(instance)
        if result.reasons == ("background.physics_input.changed",):
            continue
        assert result.accepted, result.reasons
        if result.physics_sha256 != physics_sha256:
            continue
        warm = gate.evaluate_background(instance)
        if (warm.reasons == ("background.physics_input.changed",)
                or current_physics_identity()["sha256"] != physics_sha256):
            continue
        assert warm.accepted and warm.cache_hit, warm.reasons
        assert warm.physics_sha256 == physics_sha256
        # Persist only the instance, never inject a saved acceptance into the
        # gate. Every test process primes its real checker-backed cache above.
        path.write_text(instance.model_dump_json(), encoding="utf-8")
        return instance
    pytest.fail("physics inputs changed during all three background setup attempts")


@pytest.fixture(scope="module")
def real_background(tmp_path_factory):
    gate.clear_background_cache()
    instance = _current_background(tmp_path_factory.getbasetemp() / "background-source-binding")
    yield instance
    gate.clear_background_cache()


def test_real_background_uses_current_condition_recipe(real_background):
    from stowage.vessel.fleet_conditions import CONDITION_VERSION, slice_condition
    assert slice_condition(real_background.vessel)["version"] == CONDITION_VERSION


def test_archived_stale_background_evidence_remains_rejected():
    from stowage.checker import check_static_rules
    from stowage.vessel.fleet_conditions import CONDITION_VERSION, slice_condition

    archived = _archived_instance()
    assert slice_condition(archived.vessel)["version"] != CONDITION_VERSION
    background = gate.background_instance(archived)
    result = check_static_rules(background, background.onboard, call_index=background.call_index)
    assert any(violation.rule_id == "checker.missing_vessel_data"
               and "stale versioned cell/stack audit" in violation.message
               for violation in result.violations)




@pytest.mark.parametrize("failure", ["missing", "unreadable", "checksum_changed"])
def test_real_cached_background_revalidates_pinned_offsets(real_background, monkeypatch, failure):
    """Mirror public stability's source validation even on a warm gate hit."""
    from stowage.checker import check_stability
    offsets = (repo_root() / "data/raw/kcs/kcs.fix").resolve()
    actual_read = Path.read_bytes
    reads = []

    def changed_read(path):
        if path.resolve() == offsets:
            reads.append(path)
            if failure == "missing":
                raise FileNotFoundError("regression: pinned offsets unavailable")
            if failure == "unreadable":
                raise PermissionError("regression: pinned offsets unreadable")
            return actual_read(path) + b"\nchecksum changed\n"
        return actual_read(path)

    monkeypatch.setattr(Path, "read_bytes", changed_read)
    background = gate.background_instance(real_background)
    direct = check_stability(background, background.onboard, call_index=background.call_index)
    assert not direct.ran and direct.error
    reads.clear()
    result = gate.evaluate_background(real_background)
    assert reads, "a cached acceptance must re-read the pinned hull source"
    assert not result.accepted and not result.cache_hit
    assert any("source" in reason or "physics" in reason for reason in result.reasons)


@pytest.fixture
def background_cache_harness(monkeypatch):
    """Exercise fixture caching without replacing the real source tests above."""
    from types import SimpleNamespace
    from stowage.generate import physics_identity

    state = SimpleNamespace(sha="a" * 64, builds=0, checks=0, drift=False, reject=False)
    instance = SimpleNamespace(model_dump_json=lambda: "{}")

    def build():
        state.builds += 1
        return instance

    def evaluate(candidate):
        assert candidate is instance
        state.checks += 1
        reasons = ()
        if state.drift:
            state.sha = "b" * 64
            state.drift = False
            reasons = ("background.physics_input.changed",)
        if state.reject:
            reasons = ("finisher.not_found",)
        return gate.BackgroundAcceptance(not reasons, reasons, "kept", {}, {}, 0.,
                                         cache_hit=True, physics_sha256=state.sha)

    monkeypatch.setattr(physics_identity, "current_physics_identity", lambda: {"sha256": state.sha})
    monkeypatch.setattr(f"{__name__}._build_current_background", build)
    monkeypatch.setattr(f"{__name__}.load_instance", lambda path: instance)
    monkeypatch.setattr(gate, "evaluate_background", evaluate)
    return state


def test_fixture_cache_is_bound_to_current_physics(tmp_path, background_cache_harness):
    state = background_cache_harness
    _current_background(tmp_path)
    _current_background(tmp_path)
    assert state.builds == 1
    state.sha = "b" * 64
    _current_background(tmp_path)
    assert state.builds == 2
    assert {path.name for path in tmp_path.iterdir()} == {
        f"{'a' * 64}.instance.json", f"{'b' * 64}.instance.json"}


def test_fixture_rebuilds_after_physics_changes_during_check(tmp_path, background_cache_harness):
    state = background_cache_harness
    state.drift = True
    _current_background(tmp_path)
    assert state.builds == 2
    assert [path.name for path in tmp_path.iterdir()] == [f"{'b' * 64}.instance.json"]


def test_fixture_does_not_retry_physical_rejection(tmp_path, background_cache_harness):
    state = background_cache_harness
    state.reject = True
    with pytest.raises(AssertionError, match="finisher.not_found"):
        _current_background(tmp_path)
    assert state.builds == state.checks == 1
    assert not list(tmp_path.iterdir())
