import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.binding import measure
from experiments.mvp.pipeline.binding.measurement_identity import build_identity


@pytest.fixture
def measurement_runner(tmp_path, monkeypatch):
    root = tmp_path
    here = root / "binding"; here.mkdir()
    source = root / "input.json"; source.write_text('{"input":1}')
    out = here / "measurements/rows"; out.mkdir(parents=True)
    instance = SimpleNamespace(id="test", load_list=["box"], onboard=SimpleNamespace(placements=[]))
    settings = {"config": 1, "source": "one"}
    calls = []
    monkeypatch.setattr(measure, "ROOT", root)
    monkeypatch.setattr(measure, "HERE", here)
    monkeypatch.setattr(measure, "RERUN_FAILURES", False)
    monkeypatch.setattr(measure, "admit", lambda: {"mask": 4095, "load1": 1, "t": 0})
    monkeypatch.setattr(measure, "profile", lambda name: (90, 30, None))
    monkeypatch.setattr(measure, "binding_snapshot", lambda: {})
    import stowage.io
    monkeypatch.setattr(stowage.io, "load_instance", lambda path: instance)
    def expected(*args, **kwargs):
        return build_identity(input_path=source, cell="tiny", profile="live", rule=None,
            settings={"config": settings["config"]}, engine_identity={"passed": True},
            implementation={"source": settings["source"]})
    monkeypatch.setattr(measure, "expected_identity", expected)
    return SimpleNamespace(root=root, here=here, input=source, out=out, calls=calls,
                           settings=settings, expected=expected)


@pytest.mark.parametrize("passed", [True, False])
@pytest.mark.parametrize("change", ["input", "config", "source"])
def test_run_one_rejects_stale_success_and_failure_without_editing_finished_row(measurement_runner, monkeypatch, passed, change):
    fixture = measurement_runner
    import experiments.mvp.pipeline.binding as binding
    def plan(*args, **kwargs):
        fixture.calls.append(1)
        return None, {"passed": passed, "status": "checked_plan" if passed else "no_checked_plan", "lifts": 1}
    monkeypatch.setattr(binding, "plan_with_rules", plan)
    first = measure.run_one("tiny", "input.json", "live", out=fixture.out)
    original = (fixture.out / (first["key"] + ".json")).read_bytes()
    assert measure.run_one("tiny", "input.json", "live", out=fixture.out)["key"] == first["key"]
    assert len(fixture.calls) == 1
    if change == "input":
        fixture.input.write_text('{"input":2}')
    else:
        fixture.settings[change] = "two"
    second = measure.run_one("tiny", "input.json", "live", out=fixture.out)
    assert second["key"] != first["key"] and len(fixture.calls) == 2
    assert (fixture.out / (first["key"] + ".json")).read_bytes() == original
    assert len(list(fixture.out.glob("*.json"))) == 2


def test_run_one_persists_unknown_before_keyboardinterrupt_escapes(measurement_runner, monkeypatch):
    fixture = measurement_runner
    import experiments.mvp.pipeline.binding as binding
    def plan(*args, **kwargs):
        kwargs["on_incumbent"]({"lifts": 3, "wall_s": 0.1})
        raise KeyboardInterrupt("test interruption")
    monkeypatch.setattr(binding, "plan_with_rules", plan)
    with pytest.raises(KeyboardInterrupt):
        measure.run_one("tiny", "input.json", "live", out=fixture.out)
    assert not list(fixture.out.glob("*.json"))
    attempt = next((fixture.out.parent / "attempts").iterdir())
    assert json.loads((attempt / "unknown.json").read_text())["verdict"] == "unknown"
    assert list((attempt / "incumbents").glob("*.json"))
    assert "unknown" in (attempt / "lifecycle.jsonl").read_text()


def test_stale_current_view_sidecar_does_not_change_finished_rows(measurement_runner, monkeypatch):
    fixture = measurement_runner
    import experiments.mvp.pipeline.binding.adapter as adapter
    import experiments.mvp.pipeline.binding.measurement_identity as identities
    monkeypatch.setattr(adapter, "verify_manifest", lambda: {"passed": True})
    monkeypatch.setattr(identities, "implementation_identity", lambda root: {})
    row = {"key": "old", "cell": "tiny", "map": "input.json", "profile": "live",
           "rule_type": "baseline", "canonical_rule": None, "passed": True, "wall_s": 1}
    target = fixture.out / "old.json"; target.write_text(json.dumps(row))
    before = target.read_bytes()
    rows, report = measure.current_rows(fixture.out)
    assert rows == [] and report["stale_keys"] == ["old"]
    assert target.read_bytes() == before
    assert (fixture.out.parent / "current-compatibility.json").exists()


def test_input_mutation_during_load_fails_before_attempt_start(measurement_runner, monkeypatch):
    fixture = measurement_runner
    import stowage.io
    def load(path):
        fixture.input.write_text('{"input":"mutated during load"}')
        return SimpleNamespace(id="old parsed content", load_list=["box"])
    monkeypatch.setattr(stowage.io, "load_instance", load)
    with pytest.raises(ValueError, match="input changed while loading"):
        measure.run_one("tiny", "input.json", "live", out=fixture.out)
    assert not (fixture.out.parent / "attempts").exists()
